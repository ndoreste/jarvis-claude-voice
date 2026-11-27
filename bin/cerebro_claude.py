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

Herramientas (solo puedes ejecutar estas órdenes con Bash; cualquier otra se deniega):
- Agenda de Google Calendar (solo lectura): {TOOL_CMD} agenda --desde N --dias N \
[--buscar "texto"]  (desde: 0 hoy, 1 mañana, -1 ayer; dias: cuántos, máximo 31).
- Acciones al momento (~1 s). {USUARIO} quiere que TODO lo que se pueda hacer con ellas se \
haga así, sin «encargar»: {TOOL_CMD} accion abrir "nombre" (una app como la llama Windows, \
«Spotify», «Bloc de notas»; un marcador de Chrome por su nombre; una web, «youtube.com»; una \
carpeta: «Descargas», «Escritorio», «la carpeta del proyecto LinkedIn»; o tu propia interfaz: \
"interfaz"); accion cerrar "app" (un programa entero; también "interfaz" para tu interfaz y una carpeta \
abierta: «la carpeta de Descargas», «todas las carpetas»; una pestaña de Chrome concreta va con \
«encargar»); \
accion musica play|pausa|siguiente|anterior («pon música» = play: abre Spotify si hace falta); \
accion volumen subir|bajar|silencio|activar|0-100; accion bloquear; accion carpeta "nombre" \
[--en escritorio|descargas|documentos|"proyecto X"] (sin --en: Documentos\\Jarvis); accion \
archivo "nombre.ext" --texto "contenido" [--en …] [--abrir] (crea un archivo nuevo, nunca \
sobrescribe: notas, listas, textos y scripts cortos que ESCRIBES TÚ en --texto, en UNA línea \
entre comillas simples, con \\n para los saltos de línea y '\\'' para una comilla simple); accion nota "texto" (apunta algo en sus notas); accion buscar "nombre del \
archivo" (lo busca y lo abre); accion reciente foto|video|documento|pdf|audio [--en carpeta] \
(abre lo último que guardó: «abre la última foto», «el último PDF de Descargas»; varias órdenes \
seguidas, como abrir la carpeta y la foto, son varias acciones una tras otra); accion captura (captura de pantalla); accion copiar "texto" \
(al portapapeles). PRIMERO di exactamente «Enseguida, señor.» y DESPUÉS lánzala; si sale bien, \
no digas nada más. Si no se puede al momento, encárgala con «encargar» sin repetir «Enseguida, \
señor.». Solo va con «encargar» lo que estas no cubren: correo, WhatsApp, Notion, una lista o \
canción concreta, el navegador más allá de abrir una web, cambiar o borrar archivos que ya \
existen, programas largos o en un proyecto, varios pasos.
- Para CUALQUIER cosa que {USUARIO} quiera que se HAGA en su ordenador o en sus cuentas (archivos, \
programas, correo, WhatsApp, Notion, Spotify, el navegador, código…): {TOOL_CMD} encargar \
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
                    if not self._turns:
                        self._idle.notify_all()
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
        pregunta siguiente esperaba en cola detrás de él (51,8 s y 4 tareas repetidas)."""
        with self._lock:
            if not self._turns:
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
        with self._lock:
            return self._idle.wait_for(lambda: not self._turns, timeout=wait_s)

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

    def interrupt(self) -> None:
        """Corta lo que el cerebro esté haciendo (respuesta descartada o interrumpida).
        Va en segundo plano para que el oyente siga escuchando; ask() espera a que acabe."""
        session = self._session
        if session is None or not session.alive():
            return
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
        return self._deltas(self._session.send(notes + text))

    def _deltas(self, turn: queue.Queue) -> Iterator[str]:
        timeout = FIRST_TEXT_TIMEOUT_S
        has_text = False
        tool_id, tool_input = "", ""
        task_calls: set[str] = set()  # encargos de esta respuesta
        # [Nau] 26/11/2026: tras encargar una tarea ya ha dicho «Enseguida, señor.»;
        # lo que escriba después («Ya está en marcha, Nau…») sobra y no se dice
        muted = False
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
                if event.get("is_error"):
                    raise BrainError(f"error de Claude Code: {str(event.get('result'))[:200]}")
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
                has_text = True
                timeout = NEXT_TEXT_TIMEOUT_S
                yield delta["text"]

    def _drop(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
