"""[Nau] 26/11/2026: trabajador siempre encendido para las tareas de Jarvis.

Antes, cada tarea arrancaba un Claude Code nuevo y en frío (tarea_claude.py): ~10 s antes de
empezar y sin recordar la anterior. Ahora una sola sesión de Claude Code se queda abierta
(stream-json, como el cerebro) y va haciendo las tareas que claude_code.py marca con
«trabajador»: empiezan al momento y recuerda lo que acaba de hacer («cierra la pestaña que
abriste»). Escribe el resultado en el mismo data/tareas/<id>.json, así que el oyente no cambia.

- Un solo trabajador (mutex). Su estado, en data/trabajador.json (pid, sesión, ocupado, latido).
- Si está ocupado, la tarea nueva va como antes, en un proceso aparte (tarea_claude.py), para
  no esperar en cola. Las tareas en un proyecto también van aparte (arrancan en su carpeta).
- Se cierra solo tras IDLE_EXIT_S sin tareas (libera RAM); el oyente lo despierta al hablarle.
- Confirmaciones (enviar, borrar, pagar…): igual que en tarea_claude.py, con su misma marca.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "data" / "tareas"
STATE_FILE = ROOT / "data" / "trabajador.json"
LOG_FILE = ROOT / "data" / "trabajador.log"
MUTEX = "Local\\JarvisTrabajador"
ERROR_ALREADY_EXISTS = 183
POLL_S = 0.3
HEARTBEAT_S = 2.0
ALIVE_S = 10.0  # un latido más antiguo: el trabajador ya no está
IDLE_EXIT_S = 15 * 60
MAX_TASKS_PER_SESSION = 20  # después, sesión nueva (la conversación no crece sin fin)
WORKER_NOTE = (
    " Trabajas en una sesión que sigue abierta: cada mensaje es una orden nueva de voz. "
    "Recuerda lo que hiciste en las anteriores (pestañas que abriste, archivos que creaste) "
    "por si la orden se refiere a ello («ciérrala», «cámbiale el nombre»). Pero si te repite "
    "una orden que ya hiciste, hazla otra vez: el usuario puede haber cerrado o borrado las "
    "cosas por su cuenta; comprueba cómo está el PC ahora y nunca contestes «ya estaba hecho»."
)
NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200 | NO_WINDOW
BREAKAWAY = 0x01000000

log = logging.getLogger("trabajador")


# ── Lo que usan claude_code.py y el oyente ────────────────────────────────────

def worker_state() -> dict:
    """Estado del trabajador si está vivo (latido reciente); {} si no."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if time.time() - float(state.get("latido") or 0) < ALIVE_S else {}


def _base_pythonw() -> str:
    base = Path(getattr(sys, "_base_executable", "") or sys.executable)
    for exe in (base.with_name("pythonw.exe"), Path(sys.executable).with_name("pythonw.exe")):
        if exe.exists():
            return str(exe)
    return sys.executable


def ensure_running() -> None:
    """Arranca el trabajador si no está (si ya está, el nuevo se cierra solo por el mutex)."""
    if worker_state():
        return
    command = [_base_pythonw(), str(Path(__file__).resolve())]
    popen = dict(cwd=str(ROOT), close_fds=True, stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        subprocess.Popen(command, creationflags=DETACHED | BREAKAWAY, **popen)
    except OSError:
        subprocess.Popen(command, creationflags=DETACHED, **popen)


# ── El trabajador ─────────────────────────────────────────────────────────────

class _Session:
    """Una sesión de Claude Code abierta; ask() manda una orden y espera su «result»."""

    def __init__(self) -> None:
        import tarea_claude as t
        from cerebro_claude import _kill_with_parent

        command = [
            t.claude_exe(), "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "auto",
            "--model", t.CLAUDE_MODEL,
            "--add-dir", str(t.HOME),
            "--chrome",
            "--append-system-prompt", t.SYSTEM_PROMPT + WORKER_NOTE,
            "--name", "Jarvis: trabajador",
        ]
        self.proc = subprocess.Popen(
            command, cwd=str(t.HOME), env=t.subscription_env(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
        _kill_with_parent(self.proc.pid)  # si el trabajador muere, su Claude Code también
        self.events: queue.Queue = queue.Queue()
        self.tasks_done = 0
        self.session_id: str | None = None
        threading.Thread(target=self._read, daemon=True, name="lector").start()
        log.info("Sesión de Claude Code abierta (pid %d)", self.proc.pid)

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                continue
        self.events.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def ask(self, prompt: str, timeout_s: float) -> dict:
        message = {"type": "user", "message": {"role": "user", "content": prompt}}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout_s
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            event = self.events.get(timeout=left)  # una orden larga puede pasar rato callada
            if event is None:
                raise OSError("Claude Code se ha cerrado")
            if event.get("session_id"):
                self.session_id = event["session_id"]
            if event.get("type") == "result":
                self.tasks_done += 1
                return event

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


class Worker:
    def __init__(self) -> None:
        self.session: _Session | None = None
        self.busy = False
        self.started = time.time()
        self.last_beat = 0.0

    def beat(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_beat < HEARTBEAT_S:
            return
        self.last_beat = now
        state = {"pid": os.getpid(), "ocupado": self.busy, "latido": now,
                 "session_id": self.session.session_id if self.session else None}
        tmp = STATE_FILE.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass

    def ensure_session(self) -> _Session:
        if (self.session is None or not self.session.alive()
                or self.session.tasks_done >= MAX_TASKS_PER_SESSION):
            if self.session is not None:
                self.session.close()
            self.session = _Session()
        return self.session

    def next_task(self) -> Path | None:
        """La tarea pendiente más antigua que le toca al trabajador."""
        for path in sorted(TASKS_DIR.glob("*.json")):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if task.get("trabajador") and task.get("estado") == "pendiente":
                return path
        return None

    def process(self, path: Path) -> None:
        import tarea_claude as t

        task = json.loads(path.read_text(encoding="utf-8"))
        task.update(estado="en_curso", empezada=time.time(),
                    confirmado=t.accept_confirmation(task))
        t.save(path, task)
        self.busy = True
        self.beat(force=True)
        log.info("Tarea %s: empiezo", task.get("id"))
        try:
            session = self.ensure_session()
            event = session.ask(t.prompt_for(task), t.TIMEOUT_S)
            task.update(_fields(event))
            task["session_id"] = event.get("session_id") or session.session_id
        except TimeoutError:
            task.update(estado="error", resultado="Ha tardado más de 20 minutos y la he parado.")
            if self.session:
                self.session.close()
                self.session = None
        except (OSError, ValueError, queue.Empty) as exc:
            task.update(estado="error", resultado="Ha habido un fallo técnico y no he podido terminarlo.",
                        detalle=f"Trabajador: {exc}")
            if self.session:
                self.session.close()
                self.session = None
        task["terminada"] = time.time()
        t.save(path, task)
        log.info("Tarea %s: %s en %.1f s", task.get("id"), task.get("estado"),
                 task["terminada"] - task["empezada"])
        self.busy = False
        self.beat(force=True)

    def run(self) -> None:
        idle_since = time.monotonic()
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self.beat(force=True)
        try:
            self.ensure_session()  # arranca ya: la primera tarea no espera al arranque
        except OSError:
            log.exception("No arranca Claude Code")
        while time.monotonic() - idle_since < IDLE_EXIT_S:
            path = self.next_task()
            if path is not None:
                self.process(path)
                idle_since = time.monotonic()
                continue
            self.beat()
            time.sleep(POLL_S)
        log.info("Sin tareas en %d min: me cierro", IDLE_EXIT_S // 60)


def _fields(event: dict) -> dict:
    """Los campos de resultado, como en tarea_claude.run()."""
    import tarea_claude as t

    text = str(event.get("result") or "").strip()[:t.MAX_RESULT_CHARS]
    fields = {"coste_usd": event.get("total_cost_usd"),
              "denegados": len(event.get("permission_denials") or [])}
    if event.get("is_error") or not text:
        return {**fields, "estado": "error", "resultado": text or "Ha terminado sin respuesta."}
    confirm = text.startswith(t.CONFIRM_PREFIX)
    if confirm:
        text = text[len(t.CONFIRM_PREFIX):].strip()
    return {**fields, "estado": "hecha", "resultado": text, "confirmar": confirm}


def main() -> None:
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = kernel32.CreateMutexW(None, False, MUTEX)  # noqa: F841 - vive lo que el proceso
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    worker = Worker()
    log.info("Trabajador en marcha")
    try:
        worker.run()
    except Exception:  # noqa: BLE001 - se registra; claude_code.py lo vuelve a arrancar
        log.exception("Fallo inesperado del trabajador")
    finally:
        if worker.session:
            worker.session.close()
        STATE_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
