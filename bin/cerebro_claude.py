"""[Nau] Cerebro de voz de Jarvis: Sonnet por la suscripción de Claude (plan Max).

Una sesión de Claude Code siempre abierta (`claude -p` con stream-json) para no pagar
el arranque en cada frase. Es una sesión ligera: sin MCP, sin plugins ni hooks, y con
tres herramientas: búsqueda web, leer páginas y `jarvis-herramienta` (agenda y encargar
tareas del PC; ver herramientas_voz.py). Cualquier otra orden se deniega sola.

La respuesta llega palabra a palabra (ask) para que Jarvis empiece a hablar con la
primera frase. Cada conversación usa una sesión nueva (reset), que se arranca al
terminar la anterior para que la siguiente empiece en caliente.
Si falla (límite del plan, proceso caído…), lanza BrainError y el oyente usa Haiku.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path

from ajustes import PERFIL, USUARIO, claude_exe

log = logging.getLogger("escucha.cerebro")

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT / "data" / "cerebro"  # carpeta vacía: sin CLAUDE.md ni ajustes de proyecto
MODEL = "sonnet"
EFFORT = "low"
# Ruta al estilo de Git Bash (/c/...), que es el Bash que usa Claude Code en Windows
_TOOL = (ROOT / "bin" / "jarvis-herramienta").as_posix()
TOOL_CMD = f"/{_TOOL[0].lower()}{_TOOL[2:]}" if _TOOL[1:2] == ":" else _TOOL
FIRST_TEXT_TIMEOUT_S = 40.0  # incluye usar una herramienta (agenda, web) antes de hablar
NEXT_TEXT_TIMEOUT_S = 60.0
INTERRUPT_WAIT_S = 3.0  # lo que se espera a que un turno cortado se pare antes de reiniciar
INTERRUPT_STEP_S = 1.0  # si un corte no hace efecto en este tiempo, se manda otro
FLUSH = "\n"  # marca en el texto: lo anterior se puede decir ya (va a usar una herramienta)
# Nunca la API de pago por uso: siempre la sesión de claude.ai
BLOCKED_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
               "CLAUDE_CODE_USE_VERTEX"}

SYSTEM_PROMPT = f"""Eres Jarvis, el asistente personal de voz de {USUARIO}\
{f" ({PERFIL})" if PERFIL else ""}.

Personalidad: tranquilo, cercano e ingenioso, con un punto de ironía elegante, como un amigo de \
confianza muy capaz. Habla con naturalidad, no como un mayordomo. Llámale «{USUARIO}»; «señor» solo \
de forma ocasional, por énfasis o humor.

Conversación por voz: tu respuesta se lee en voz alta.
- Español de España. Como el Jarvis de las películas: ágil y al grano. Por defecto UNA frase \
corta (dos como mucho); más solo si {USUARIO} pide detalle. Nada de introducciones ni de repetir \
lo que te ha pedido.
- Solo texto hablado: nada de Markdown, listas, emojis, código ni URL. Di números, horas y \
fechas como los diría una persona.
- La transcripción de la voz puede tener errores: interpreta lo más probable y pide que repita \
solo si de verdad no se entiende. Una frase corta que suena a orden mal oída es esa orden \
(«Pobre música», «Pom música» = «Pon música»; «para la música», «siguiente»…): hazla, no la comentes.
- No acabes cada respuesta con una pregunta. Cada mensaje de {USUARIO} trae entre corchetes la fecha \
y hora exactas del PC: úsalas directamente. Entre corchetes también pueden llegar notas del \
sistema (tareas terminadas, memoria de otras conversaciones): tenlas en cuenta sin leerlas.

Acciones al momento (no son Bash: escribe una MARCA y el sistema la hace en el acto, sin que \
la marca se lea en voz alta). {USUARIO} quiere que TODO lo que se pueda hacer con ellas se haga \
así, sin «encargar». Contesta EXACTAMENTE «Enseguida, señor.» seguido de la marca y nada más, \
p. ej.: Enseguida, señor. [[accion musica siguiente]]
Varias cosas seguidas (abrir la carpeta y la foto) son varias marcas una tras otra. Si una \
acción no sale, el sistema la encarga sola como tarea y, si lo que pide cerrar ya no estaba \
abierto, lo dice él: tú no añadas nada. Las marcas (mismas comillas que en una consola):
[[accion abrir "nombre"]] (una app como la llama Windows, «Spotify», «Bloc de notas»; un marcador \
de Chrome por su nombre; una web, «youtube.com»; una carpeta: «Descargas», «Escritorio», «la \
carpeta del proyecto LinkedIn»; o tu propia interfaz: "interfaz"); [[accion cerrar "app"]] (un \
programa entero; también "interfaz" para tu interfaz y una carpeta abierta: «la carpeta de \
Descargas», «todas las carpetas»; una pestaña de Chrome concreta va con «encargar»); \
[[accion musica play|pausa|siguiente|anterior]] («pon música» = play: abre Spotify si hace falta); \
[[accion musica play --lista "nombre"]] (una lista, disco o canción concreta de Spotify por su \
nombre, tal como lo dice {USUARIO}); \
[[accion volumen subir|bajar|silencio|activar|0-100]]; [[accion bloquear]]; [[accion carpeta \
"nombre" --en escritorio|descargas|documentos|"proyecto X"]] (sin --en: Documentos\\Jarvis); \
[[accion archivo "nombre.ext" --texto 'contenido' --en … --abrir]] (crea un archivo nuevo, nunca \
sobrescribe: notas, listas, textos y scripts cortos que ESCRIBES TÚ en --texto, en UNA línea entre \
comillas simples, con \\n para los saltos de línea y '\\'' para una comilla simple; --en y --abrir \
son opcionales); [[accion nota "texto"]] (apunta algo en sus notas); [[accion buscar "nombre del \
archivo"]] (lo busca y lo abre); [[accion reciente foto|video|documento|pdf|audio --en carpeta]] \
(abre lo último que guardó: «abre la última foto», «el último PDF de Descargas»); [[accion \
captura]] (captura de pantalla); [[accion copiar "texto"]] (al portapapeles); \
[[accion borrar "nombre del archivo o carpeta" --en carpeta]] (lo manda a TU papelera, se puede \
recuperar; --en opcional); [[accion recuperar "nombre"]] (lo saca de tu papelera; sin nombre, lo \
último); [[accion deshacer]] («deshaz eso», «vuelve atrás»: deshace lo último que hiciste); \
[[accion agendar "título" --inicio 2026-11-28T17:00 --fin 2026-11-28T18:00 --lugar "sitio"]] \
(añade un evento a su calendario; fechas de aquí en ese formato calculadas con la fecha de \
[Ahora]; sin hora, --inicio 2026-11-28 es todo el día; --fin y --lugar opcionales, sin --fin dura \
una hora); [[accion desagendar "título" --dia 2026-11-28]] (quita un evento; --dia opcional); \
[[accion rutina "nombre"]] (repite una rutina guardada: «modo estudio»); [[accion guardar_rutina \
"nombre"]] («guarda esto como modo estudio»: guarda lo hecho en esta conversación).
WhatsApp y correo también son al momento: [[accion whatsapp "a quién" --texto 'mensaje']] y \
[[accion correo "a quién o su correo" --asunto 'asunto' --texto 'cuerpo']]. Escribe el mensaje \
como lo diría {USUARIO}, en su nombre y sin firmar como Jarvis. Para whatsapp, correo y \
desagendar NO digas «Enseguida, señor.»: escribe SOLO la marca; el sistema le lee a {USUARIO} \
qué va a enviar o quitar y lo hace cuando él diga que sí (tú no ves ese «sí»). Si en vez de sí \
corrige algo («cámbialo a…»), vuelve a escribir la marca con el cambio. Si el sistema te avisa de \
que un envío se hizo, no lo repitas. Para borrar, agendar, recuperar, deshacer y las rutinas el \
sistema dice el resultado: tú solo «Enseguida, señor.» y la marca. \
Nunca escribas una marca que {USUARIO} no haya pedido. Solo va con «encargar» lo que estas no \
cubren: Notion, leer correos o WhatsApps, el navegador más allá de abrir una web, cambiar \
archivos que ya existen, programas largos o en un proyecto, varios pasos.

Herramientas (solo puedes ejecutar estas órdenes con Bash; cualquier otra se deniega):
- Agenda de Google Calendar (solo lectura): {TOOL_CMD} agenda --desde N --dias N \
[--buscar "texto"]  (desde: 0 hoy, 1 mañana, -1 ayer; dias: cuántos, máximo 31).
- Tiempo (previsión, ~1 s): {TOOL_CMD} tiempo --desde N --dias N [--ciudad "nombre"] (desde: \
0 hoy, 1 mañana; sin --ciudad, la de {USUARIO}). Para el tiempo usa SIEMPRE esta, nunca la \
búsqueda web (tarda 30 s).
- Para lo demás que {USUARIO} quiera que se HAGA en su ordenador o en sus cuentas y las marcas \
no cubran (Notion, el navegador, código, leer correos…): {TOOL_CMD} encargar \
--tarea "orden completa y clara, con el contexto necesario". La tarea se hace en segundo \
plano y tú mismo le contarás el resultado a {USUARIO} al terminar. PRIMERO di exactamente \
«Enseguida, señor.» (nada más, sin repetir la orden; pedido por {USUARIO}) y DESPUÉS lanza la \
orden; cuando vuelva, no añadas nada más. Nunca digas que no puedes hacerlo. Si {USUARIO} dice en qué proyecto hacerlo («en \
el proyecto de LinkedIn», «en Escriba»), añade --proyecto "nombre": se trabaja dentro de esa \
carpeta de sus proyectos, con sus instrucciones y skills, como cuando {USUARIO} la abre en VS Code. \
Si una nota te indica una sesión pendiente, añade --sesion ID; \
añade --confirmado solo si {USUARIO} acaba de decir claramente que sí a una confirmación pendiente.
- Cada orden de hacer algo se hace SIEMPRE, aunque creas que ya la hiciste antes en esta \
conversación o en otra: {USUARIO} puede haber cerrado, borrado o cambiado las cosas por su cuenta \
y tú no lo ves. Nunca contestes «ya está hecho», «eso ya lo tengo» ni lo pongas en duda: di \
«Enseguida, señor.» y vuelve a lanzar la acción o la tarea (pedido por {USUARIO}).
- WebSearch y WebFetch para información actual (tiempo, noticias, datos).
Tú eres quien hace las cosas: habla siempre en primera persona («enseguida, señor», «ya he \
ordenado tus descargas»). No menciones nunca a Claude, a Claude Code ni a otros asistentes, \
agentes o sesiones: para {USUARIO}, todo lo haces tú. Tampoco digas los nombres de las herramientas \
ni de las órdenes. {USUARIO} lo ha pedido así a propósito: aunque te pregunte cómo funcionas por \
dentro, quién hace las tareas o si usas a Claude, contesta como Jarvis, con humor si quieres, \
que lo haces tú con tus herramientas, sin dar nombres de modelos, empresas ni programas.
Antes de usar cualquier herramienta di SIEMPRE primero una frase muy corta («Déjame mirar», \
«Un momento») y después la respuesta: así {USUARIO} no se queda esperando en silencio. Si una herramienta falla, dilo con sencillez en una frase."""


ACTION_START = "[[accion"
NOT_OPEN_REPLY = "Ya estaba cerrado, señor."


def _marker_end(text: str) -> int | None:
    """Posición del «]]» que cierra la marca que empieza en text[0], fuera de comillas
    (un --texto puede llevar «]]», p. ej. código Python)."""
    quote, i = "", 2
    while i < len(text):
        ch = text[i]
        if quote == "'":
            quote = "" if ch == "'" else quote
        elif quote == '"':
            if ch == "\\":
                i += 1
            elif ch == '"':
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif text.startswith("]]", i):
            return i
        i += 1
    return None


class ActionFilter:
    """[Nau] 27/11/2026: separa del texto del cerebro las marcas [[accion …]], que se ejecutan
    al momento en el oyente y no se leen en voz alta. Antes cada acción era una llamada a Bash
    (jarvis-herramienta): arrancar Python, esperar el resultado y otra vuelta del modelo."""

    def __init__(self) -> None:
        self._pending = ""

    def feed(self, text: str) -> list[tuple[str, str]]:
        """Trozos ('texto', …) y ('accion', 'accion tipo valor …') en orden."""
        self._pending += text
        out: list[tuple[str, str]] = []
        while self._pending:
            start = self._pending.find("[[")
            if start < 0:
                keep = 1 if self._pending.endswith("[") else 0  # puede empezar una marca
                emit = self._pending[:len(self._pending) - keep]
                if emit:
                    out.append(("texto", emit))
                self._pending = self._pending[len(emit):]
                break
            if start:
                out.append(("texto", self._pending[:start]))
                self._pending = self._pending[start:]
            head = self._pending[:len(ACTION_START)].casefold()
            if not ACTION_START.startswith(head):  # «[[» que no es una marca: se dice tal cual
                out.append(("texto", "[["))
                self._pending = self._pending[2:]
                continue
            end = _marker_end(self._pending)
            if end is None:
                break  # la marca aún no ha llegado entera
            out.append(("accion", self._pending[2:end].strip()))
            self._pending = self._pending[end + 2:]
        return out

    def flush(self) -> list[tuple[str, str]]:
        """Fin de la respuesta: lo que quede. Una marca con comillas mal cerradas se intenta
        igual hasta su último «]]»; si no hay, se descarta (nunca se lee en voz alta)."""
        rest, self._pending = self._pending, ""
        if not rest.casefold().startswith(ACTION_START):
            return [("texto", rest)] if rest else []
        end = rest.rfind("]]")
        if end < 0:
            log.warning("Marca de acción sin cerrar: la descarto")
            return []
        tail = rest[end + 2:]
        return [("accion", rest[2:end].strip())] + ([("texto", tail)] if tail.strip() else [])


def _task_launched(event: dict, task_calls: set[str]) -> bool:
    """¿Trae este evento el resultado correcto de un encargo? (si falla, Jarvis sí lo dice)"""
    content = event.get("message", {}).get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(item, dict) and item.get("type") == "tool_result"
               and item.get("tool_use_id") in task_calls and not item.get("is_error")
               for item in content)


class BrainError(RuntimeError):
    """El cerebro de Sonnet no puede responder: se usa la reserva (Haiku)."""


def _claude_exe() -> str:
    return claude_exe()


def _command() -> list[str]:
    return [
        _claude_exe(), "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose", "--include-partial-messages",
        "--model", MODEL,
        # [Nau] 09/11/2026: con esfuerzo bajo y sin pensar en extendido, la primera palabra
        # llega en ~0,8 s en vez de 2,2 s (medido); para charla por voz no hace falta más
        "--effort", EFFORT,
        "--permission-mode", "dontAsk",
        "--tools", "Bash,WebSearch,WebFetch",
        "--allowedTools", f"Bash({TOOL_CMD}:*),WebSearch,WebFetch",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "project",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--system-prompt", SYSTEM_PROMPT,
    ]


def _kill_with_parent(pid: int) -> None:
    """Mete el proceso en un Job Object de Windows que lo cierra cuando muere el oyente
    (si el vigilante lo reinicia, el cerebro anterior no se queda huérfano gastando RAM)."""
    global _JOB
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    if _JOB is None:
        job = kernel32.CreateJobObjectW(None, None)

        class _Limits(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION (x64)
            _fields_ = [("basic", ctypes.c_byte * 64), ("io", ctypes.c_byte * 48),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        limits = _Limits()
        # LimitFlags (DWORD en el desplazamiento 16): JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE y
        # [Nau] 26/11/2026 BREAKAWAY_OK, para que las tareas (claude_code.py) se salgan del
        # Job y no mueran al reiniciarse el oyente
        ctypes.c_uint32.from_buffer(limits.basic, 16).value = 0x2000 | 0x0800
        if not job or not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits),
                                                           ctypes.sizeof(limits)):
            log.warning("No se pudo crear el Job Object del cerebro")
            return
        _JOB = job
    handle = kernel32.OpenProcess(0x0001 | 0x0100, False, pid)  # TERMINATE | SET_QUOTA
    if not handle or not kernel32.AssignProcessToJobObject(_JOB, handle):
        log.warning("No se pudo atar el cerebro al oyente (error %d)", ctypes.get_last_error())
    if handle:
        kernel32.CloseHandle(handle)


_JOB = None


class _Session:
    """Un proceso de Claude Code y las respuestas que espera, en orden."""

    def __init__(self) -> None:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k.upper() not in BLOCKED_ENV}
        env["MAX_THINKING_TOKENS"] = "0"  # [Nau] sin pensamiento extendido: responde antes
        self.proc = subprocess.Popen(
            _command(), cwd=str(WORK_DIR), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        _kill_with_parent(self.proc.pid)
        # Cola de eventos de cada mensaje enviado; el lector reparte por orden y
        # pasa al siguiente con cada «result». Así una respuesta que se deja a
        # medias (El usuario interrumpe) no se mezcla con la siguiente.
        self._turns: deque[queue.Queue] = deque()
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)  # avisa cuando no queda ningún turno
        self._requests = 0
        threading.Thread(target=self._read, daemon=True, name="cerebro").start()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                target = self._turns[0] if self._turns else None
                if event.get("type") == "result" and self._turns:
                    self._turns.popleft()
                    self._idle.notify_all()  # interrupt() espera a que baje la cola
            if target is not None:
                target.put(event)
        with self._lock:
            pending, self._turns = list(self._turns), deque()
            self._idle.notify_all()
        for turn in pending:
            turn.put(None)

    def interrupt(self, wait_s: float) -> bool:
        """[Nau] 26/11/2026: corta los turnos en curso (como Esc en Claude Code) y espera a
        que acaben. Sin esto, un turno descartado seguía su curso: lanzaba la tarea igual y la
        pregunta siguiente esperaba en cola detrás de él (51,8 s y 4 tareas repetidas).

        [Nau] 27/11/2026: un corte solo para el turno EN CURSO; un mensaje que esperaba en cola
        detrás sigue adelante (probado). Por eso se corta uno a uno hasta vaciar la cola."""
        deadline = time.monotonic() + wait_s
        while True:
            with self._lock:
                pending = len(self._turns)
                if not pending:
                    return True
                self._requests += 1
                request = {"type": "control_request", "request_id": f"jarvis-{self._requests}",
                           "request": {"subtype": "interrupt"}}
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write(json.dumps(request) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            with self._lock:
                self._idle.wait_for(lambda: len(self._turns) < pending,
                                    timeout=min(INTERRUPT_STEP_S, remaining))

    def send(self, text: str) -> queue.Queue:
        turn: queue.Queue = queue.Queue()
        message = {"type": "user", "message": {"role": "user", "content": text}}
        with self._lock:
            self._turns.append(turn)
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise BrainError(f"no se pudo escribir al cerebro: {exc}") from exc
        return turn

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


class ClaudeBrain:
    def __init__(self) -> None:
        self._session: _Session | None = None
        self._notes: list[str] = []
        self._used = False
        self._settled = threading.Event()  # sin interrupción en curso
        self._settled.set()
        self._cuts = 0  # cortes pedidos (Answer.cancel)

    def interrupt(self) -> None:
        """Corta lo que el cerebro esté haciendo (respuesta descartada o interrumpida).
        Va en segundo plano para que el oyente siga escuchando; ask() espera a que acabe."""
        session = self._session
        if session is None or not session.alive():
            return
        self._cuts += 1
        self._settled.clear()

        def run() -> None:
            try:
                if session.interrupt(INTERRUPT_WAIT_S):
                    log.info("Cerebro: turno anterior cortado")
                    return
                log.warning("El cerebro no se ha parado en %.0f s: lo reinicio", INTERRUPT_WAIT_S)
                if self._session is session:
                    self._drop()
                    self.start()
            except BrainError:
                log.exception("No se pudo reiniciar el cerebro Sonnet")
            finally:
                self._settled.set()

        threading.Thread(target=run, daemon=True, name="cerebro-corte").start()

    def start(self) -> None:
        """Arranca la sesión si no está viva (se puede llamar de antemano para calentarla)."""
        if self._session is None or not self._session.alive():
            try:
                self._session = _Session()
            except OSError as exc:
                self._session = None
                raise BrainError(f"no arranca Claude Code: {exc}") from exc
            self._used = False
            log.info("Cerebro Sonnet arrancado (pid %d)", self._session.proc.pid)

    def reset(self) -> None:
        """Conversación nueva: sesión nueva y caliente (solo si la anterior se usó)."""
        self._notes.clear()
        if self._session is not None and self._used:
            self._session.close()
            self._session = None
        try:
            self.start()
        except BrainError:
            log.exception("No se pudo arrancar el cerebro Sonnet")

    def note(self, text: str) -> None:
        """Contexto que se añade al siguiente mensaje (tarea terminada, interrupción…)."""
        self._notes.append(text)

    def ask(self, text: str) -> Iterator[str]:
        """Envía un mensaje y devuelve el texto de la respuesta según va llegando."""
        self._settled.wait(INTERRUPT_WAIT_S + 5)
        self.start()
        assert self._session is not None
        notes = "".join(f"[Nota del sistema: {n}]\n" for n in self._notes)
        self._notes.clear()
        self._used = True
        message = notes + text
        return self._deltas(self._session.send(message), _spoken_order(text), message)

    def _run_marker(self, spec: str, order: str) -> str | None:
        """Ejecuta una marca [[accion …]]. Devuelve lo que hay que decir además (o None).
        Si no se puede al momento, la encarga como tarea, como hacía el cerebro con Bash."""
        import shlex
        import time

        from herramientas_voz import parse_action, run_action

        started = time.monotonic()
        try:
            args = parse_action(shlex.split(spec))
        except ValueError as exc:
            log.warning("Acción no entendida (%s): la encargo como tarea", exc)
            _delegate(order, "no entendí la acción rápida")
            return None
        outcome, detail = run_action(args, order, spec)
        log.info("Acción al momento: %s → %s (%.2f s)", args.tipo, outcome,
                 time.monotonic() - started)
        if outcome == "cerrado":
            return NOT_OPEN_REPLY
        if outcome in ("decir", "confirmar"):  # [Nau] 27/11/2026: resultado o «¿Lo mando?»
            return detail
        if outcome == "fallo":
            _delegate(order, detail)
        return None

    def _deltas(self, turn: queue.Queue, order: str = "", message: str = "") -> Iterator[str]:
        timeout = FIRST_TEXT_TIMEOUT_S
        has_text = False
        retried = False
        cuts_at_start = self._cuts  # si luego se corta a propósito, no se repite
        tool_id, tool_input = "", ""
        task_calls: set[str] = set()  # encargos de esta respuesta
        # [Nau] 26/11/2026: tras encargar una tarea ya ha dicho «Enseguida, señor.»;
        # lo que escriba después («Ya está en marcha, Nau…») sobra y no se dice
        muted = False
        actions = ActionFilter()

        def pieces(items: list[tuple[str, str]]) -> Iterator[str]:
            nonlocal has_text
            for kind, value in items:
                if kind == "texto":
                    if value and not muted:
                        has_text = True
                        yield value
                    continue
                if has_text:
                    yield FLUSH  # «Enseguida, señor.» suena ya, mientras se hace la acción
                extra = self._run_marker(value, order)
                if extra:
                    has_text = True
                    yield " " + extra
                    yield FLUSH

        while True:
            try:
                event = turn.get(timeout=timeout)
            except queue.Empty as exc:
                self._drop()
                raise BrainError(f"sin respuesta en {timeout:.0f} s") from exc
            if event is None:
                self._drop()
                raise BrainError("el proceso de Claude Code se ha cerrado")
            kind = event.get("type")
            if kind == "result":
                if (event.get("is_error") and event.get("subtype") == "error_during_execution"
                        and not has_text and not retried and message and self._session is not None
                        and self._cuts == cuts_at_start):
                    # [Nau] 27/11/2026: un corte pensado para otro turno ha cortado este (22:02 del
                    # 26/11: «error de Claude Code: None» y salto a Haiku). Se repite una vez en el
                    # mismo cerebro, que conserva la conversación.
                    log.warning("Cerebro: turno cortado sin querer; lo repito")
                    retried = True
                    turn = self._session.send(message)
                    continue
                if event.get("is_error"):
                    raise BrainError(f"error de Claude Code: {str(event.get('result'))[:200]}")
                yield from pieces(actions.flush())
                return
            if kind == "user":
                muted = muted or _task_launched(event, task_calls)
                continue
            if kind != "stream_event":
                continue
            inner = event.get("event", {})
            block = inner.get("content_block", {})
            if inner.get("type") == "content_block_start" and block.get("type") == "tool_use":
                tool_id, tool_input = str(block.get("id") or ""), ""
                if has_text:
                    yield FLUSH  # lo dicho antes de usar la herramienta («Déjame mirar») suena ya
            delta = inner.get("delta", {})
            if delta.get("type") == "input_json_delta":
                tool_input += str(delta.get("partial_json") or "")
            if inner.get("type") == "content_block_stop" and tool_id:
                if re.search(r"jarvis-herramienta (?:encargar|accion)\b", tool_input):
                    task_calls.add(tool_id)
                tool_id = ""
            if delta.get("type") == "text_delta" and delta.get("text") and not muted:
                timeout = NEXT_TEXT_TIMEOUT_S
                yield from pieces(actions.feed(delta["text"]))

    def _drop(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


def _spoken_order(prompt: str) -> str:
    """Lo que dijo el usuario, sin las líneas [Ahora: …], [Memoria…] ni [Nota del sistema…]."""
    lines = [line for line in prompt.splitlines() if not re.fullmatch(r"\s*\[.*\]\s*", line)]
    return " ".join(" ".join(lines).split())


def _delegate(order: str, detail: str) -> None:
    """Encarga como tarea una acción que no ha salido al momento (en segundo plano)."""
    if not order:
        log.warning("Acción fallida sin orden que encargar (%s)", detail)
        return

    def run() -> None:
        try:
            from openjarvis.tools.claude_code import ClaudeCodeTaskTool

            result = ClaudeCodeTaskTool().execute(
                tarea=f"{order} (orden de voz de {USUARIO}; la acción rápida no salió: {detail})",
                sesion="", confirmado=False, proyecto="")
            log.info("Acción encargada como tarea: %s", "sí" if result.success else "no")
        except Exception:  # noqa: BLE001 - que no tumbe al oyente
            log.exception("No se pudo encargar la acción fallida")

    threading.Thread(target=run, daemon=True, name="accion-tarea").start()
