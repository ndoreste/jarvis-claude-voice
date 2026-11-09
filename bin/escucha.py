"""Oyente manos libres de Jarvis.

Escucha el micrófono en local y se activa de dos formas:
  1. Frase en español con «Jarvis» («Hola Jarvis», «Oye Jarvis, ¿qué hora es?»):
     cada frase corta se transcribe con el Whisper local de la API de Jarvis y se
     busca el nombre. Si la pregunta va en la misma frase, responde directamente.
  2. «Hey Jarvis» en inglés con openWakeWord.
Si solo le llaman («¿Qué dices, Jarvis?») saluda con la frase fija; si la pregunta va
en la misma frase, responde directamente. Espera a que el usuario termine (frases a medias o
si sigue hablando mientras piensa) y reproduce la respuesta con ElevenLabs. Tras
responder sigue escuchando (FOLLOW_UP_S) sin repetir el nombre, hasta que el usuario se calla
o se despide («gracias, eso es todo»). Cada conversación empieza de cero y se guarda
en la memoria (memoria_voz.py); solo se usa si el usuario pide retomar algo.
Si algo falla, Jarvis lo dice en voz alta y la conversación sigue.
El audio no sale del PC: la transcripción es local.

Se ejecuta con el Python del entorno de OpenJarvis (ver jarvis-arranque.vbs).
Registro: ..\\data\\escucha.log
"""

from __future__ import annotations

import ctypes
import io
import json
import logging
import difflib
import hashlib
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import unicodedata
import uuid
import wave
import winsound
import queue
import threading
from collections import deque
from collections.abc import Iterable
from itertools import chain
from pathlib import Path

import numpy as np
import sounddevice as sd
from openwakeword.model import Model

from ajustes import USUARIO
from cerebro_claude import ClaudeBrain
from escriba_activo import escriba_dictando
from memoria_voz import RESUME_REQUEST, VoiceMemory
from microfono import FRAME, SAMPLE_RATE, Microphone, MicUnavailable
from respuesta import Answer, speech_chunks

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "escucha.log"
API = "http://127.0.0.1:8000"
# Modelo para la voz: Haiku contesta en ~0,9 s frente a ~1,5 s de Sonnet (la web sigue con Sonnet)
VOICE_MODEL = "openrouter/anthropic/claude-haiku-5.5"

WAKE_MODEL = "hey_jarvis"
WAKE_THRESHOLD = 0.5  # solo despierta con su nombre claro, no con ruidos parecidos
NEAR_MISS = 0.15  # se registra para poder afinar el umbral

# Detección de voz por energía (mismo criterio que la web)
MIN_SPEECH_RMS = 0.012
NOISE_MULTIPLIER = 2.5
FLOOR_RISE = 0.02
MIN_SPEECH_FRAMES = 3
# Pausa que marca el final de la frase ([Nau] 0,7 → 0,6 → 0,5 el 09/11/2026). Si solo era una
# pausa y el usuario sigue hablando, resumed_speech lo nota y vuelve a escuchar.
SILENCE_S = 0.5
NO_SPEECH_S = 6.0
FOLLOW_UP_S = 12.0  # margen para seguir hablando tras cada respuesta
MAX_UTTERANCE_S = 20.0

HISTORY_TURNS = 20  # turnos que recuerda dentro de una misma conversación
# Memoria persistente: cada conversación terminada se guarda aquí y solo se usa
# cuando el usuario pide retomar algo (ver memoria_voz.py)
MEMORY = VoiceMemory(ROOT / "data" / "memoria-voz.jsonl")
LEGACY_HISTORY_FILE = ROOT / "data" / "conversacion-voz.json"
HEARTBEAT_FILE = ROOT / "data" / "escucha.heartbeat"
# Estado en vivo para el HUD de la web (lo sirve la API en /v1/hud/oyente)
STATE_FILE = ROOT / "data" / "escucha-estado.json"
LEVEL_PUBLISH_EVERY_S = 0.25
HEARTBEAT_EVERY_S = 30
MIC_SILENT_RESET_S = 600  # 10 min de silencio digital absoluto: se reabre el micro
RESTART_DELAY_S = 5
MAX_REASKS = 2  # veces que vuelve a escuchar si el usuario sigue hablando mientras piensa
HTTP_TIMEOUT_S = 120
# Voz de respaldo de Windows (sin coste) si ElevenLabs no puede hablar
FALLBACK_VOICE = "Microsoft Helena"
# [Nau] 09/11/2026: la respuesta se sintetiza por frases (respuesta.py) y empieza a sonar
# con la primera; la siguiente se prepara mientras suena la anterior
PLAYBACK_DIR = Path(tempfile.gettempdir()) / "jarvis-voz"
PLAYBACK_MARGIN_S = 0.15
# [Nau] 09/11/2026: si el usuario habla mientras Jarvis contesta, se calla y le escucha
# [Nau] 09/11/2026: 4 de los últimos 6 trozos (~0,3 s de voz en ~0,5 s): una tos o un golpe
# no lo cortan, y las bajadas entre sílabas ya no reinician la cuenta como con «5 seguidos»
BARGE_IN_FRAMES = 4
BARGE_IN_FRAMES_QUIET = 3  # [Nau] sin eco apreciable (cascos): ~0,25 s, lo que dura un «para»
BARGE_IN_WINDOW = 6
BARGE_IN_MIN_RMS = 0.04
# [Nau] 09/11/2026: su propia voz llega al micro (con los BS9655 por Bluetooth, ~0,4 s tarde
# y hasta al 34 % del nivel de lo que suena) y se interrumpía a sí mismo. Solo cuenta como
# interrupción lo que supera ECHO_MARGIN veces el eco esperado de lo que está sonando.
ECHO_WINDOW_FRAMES = 8  # ~0,65 s: cubre el retraso del Bluetooth (~0,4 s) y el de unos con cable
ECHO_MARGIN = 1.5  # simulado con el eco grabado: sin cortes propios; la voz normal le corta en 0,3-0,5 s
ECHO_SIMILARITY = 0.6  # lo «oído» al interrumpir se parece tanto a lo que decía Jarvis que es su eco
ECHO_GAIN_START = 0.5  # cuánto eco llega por cada unidad de voz reproducida; se aprende solo
ECHO_GAIN_FILE = ROOT / "data" / "eco-aprendido.txt"
ECHO_GAIN_RANGE = (0.02, 2.0)  # [Nau] mínimo 0,05 → 0,02: con cascos el eco es ~0,03 de lo que suena
ECHO_LOUD = 0.03  # solo se aprende el eco de los trozos en los que Jarvis suena
# [Nau] 09/11/2026: «para» dura ~0,3 s y, con el altavoz Bluetooth sonando, apenas sobresale
# del eco: por volumen no se distingue (simulado con el eco grabado). Mientras Jarvis habla,
# Whisper escucha sin parar ventanas de ~1 s cada ~0,5 s; si oye una orden de parar que no
# está en lo que Jarvis dice en ese momento, se calla.
STOP_SCAN_FRAMES = 12  # ~1 s de audio por ventana
STOP_SCAN_EVERY = 6  # una ventana nueva cada ~0,5 s (si la anterior ya está transcrita)
STOP_SCAN_MIN_FRAMES = 6  # al empezar a hablar, la primera ventana con ~0,5 s basta
STOP_WORDS = {"para", "parar", "pare", "paren", "calla", "callate", "callese", "espera",
              "esperate", "basta", "stop", "silencio", "alto"}
INTERRUPTED_NOTE = f" [{USUARIO} me ha interrumpido aquí]"
# [Nau] 09/11/2026: tareas que Jarvis encarga a Claude Code (herramienta encargar_a_claude)
TASKS_DIR = ROOT / "data" / "tareas"
TASK_CHECK_EVERY_S = 2.0
# [Nau] 09/11/2026: el cerebro es Sonnet por la suscripción (cerebro_claude.py); Haiku por
# OpenRouter queda de reserva. Si el usuario vuelve a llamarle antes de 5 min, sigue el hilo.
BRAIN = ClaudeBrain()
CONTINUE_WINDOW_S = 300
INTERRUPTED_BRAIN_NOTE = (f"{USUARIO} te ha interrumpido mientras hablabas: no ha oído el final de "
                          "tu respuesta anterior.")
RESUMED_BRAIN_NOTE = (f"{USUARIO} no había terminado de hablar y no ha oído tu respuesta anterior: "
                      "responde a su frase completa, que va a continuación.")

# Frases que Whisper se inventa con ruido o silencio
HALLUCINATIONS = re.compile(
    r"subt[ií]tulos|amara\.org|gracias por ver|suscr[ií]bete|pr[oó]ximo v[ií]deo|dale (?:a )?like"
    r"|^\W*$", re.IGNORECASE
)
THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)
# [Nau] Frases con las que el usuario cierra la conversación: basta con que la frase ACABE así
# («Gracias Jarvis, adiós», «…puedes descansar, hasta luego Jarvis»). Se comparan sin
# tildes, sin signos y sin el nombre. Las preguntas («¿cómo se dice adiós?») no cuentan.
GOODBYE_END = re.compile(
    r"(?:^|\s)(?:adios|hasta luego|hasta manana|hasta otra|hasta despues|hasta la proxima"
    r"|chao|chau|nos vemos|descansa|descansar|eso es todo|eso era todo|nada mas|ya esta"
    r"|ya no necesito nada|no necesito nada mas|buenas noches|me voy|a dormir|no gracias)$"
)
# Palabras de cortesía que pueden ir detrás de la despedida («adiós, gracias, señor»)
GOODBYE_TAIL = {"gracias", "muchas", "senor", "bueno", "vale", "ok", "okay", "perfecto",
                "genial", "pues", "entonces", "ya", "eh"} | {USUARIO.casefold()}
GOODBYE_REPLY = "A su disposición, señor."
ERROR_REPLY = "Disculpe, señor, he tenido un problema técnico con esa petición. ¿Lo intentamos de otra forma?"
# Lo que dice Jarvis (con la voz de respaldo) cuando ElevenLabs no puede sintetizar
# [Nau] 09/11/2026: el 401 suele ser el límite de créditos de la clave «jarvis», no el saldo
# de la cuenta. Tras el aviso se lee la respuesta igualmente, para no perderla.
SALDO_REPLY = (
    f"Aviso, {USUARIO}: ElevenLabs no me deja usar mi voz; seguramente la clave jarvis "
    "ha llegado a su límite de créditos. Te respondo con la voz de Windows. "
)

# Activación por nombre: variantes con las que Whisper suele escribir «Jarvis»
# [Nau] 08/11/2026: es solo el RESPALDO. La activación normal es «Hey Jarvis» (openWakeWord).
# No añadir más variantes: NAME_SIMILARITY ya cubre las parecidas (ver CLAUDE.md, «Activación»).
WAKE_NAME_WORD = re.compile(
    r"\b(?:jarvis|yarvis|harvis|jarbis|yarbis|charvis|javis|jarvi|yarvi|garvis|harvey"
    r"|yarmis|yarmish|yarby|yervis|jervis|jarby|yarbi|jarbi)\b",
    re.IGNORECASE,
)
SEGMENT_PREROLL_FRAMES = 8  # ~0,6 s de audio previo a la voz (no corta la primera sílaba)
SEGMENT_END_SILENCE_S = 0.5  # [Nau] 0,6 → 0,5: el saludo llega antes
SEGMENT_MIN_S = 0.4
SEGMENT_MAX_S = 6.0
SEGMENT_OVERLAP_FRAMES = 25  # ~2 s que se repiten en el trozo siguiente
DIGITAL_SILENCE_RMS = 1e-4
LEVEL_LOG_EVERY_S = 120
NAME_SIMILARITY = 0.6
# [Nau] 09/11/2026: la ÚNICA forma de despertarlo es «¿Qué pasa, Jarvis?» (pedido por Nau).
# Se acepta también «¿Qué dices, Jarvis?»: Whisper suele escribir así «qué pasa».
CALL_WORDS = {"pasa", "paso", "dices", "dice", "dises", "dise", "decis"}
# Lo que puede ir antes del «qué» sin que deje de ser la llamada («Oye, ¿qué pasa, Jarvis?»)
LEAD_WORDS = {"hola", "oye", "hey", "ey", "eh", "vale", "bueno", "pues", "venga", "oiga", "y", "a",
              "no", "buenas"}
# «Hey Jarvis» (openWakeWord) desactivado el 09/11/2026: Nau quiere despertarlo solo con su frase
WAKE_BY_HEY_JARVIS = False
# Frases que solo comprueban si está («Jarvis, ¿estás por ahí?»): se contestan con el saludo fijo
PRESENCE_CHECK = re.compile(
    r"(?:(?:que|y) )?(?:que pasa|que tal|despierta|"
    r"(?:estas|esta|estais|estan) (?:por ahi|ahi|despierto|despiertos|disponible)|"
    r"me (?:oyes|escuchas|oye|escucha))"
)
# [Nau] 09/11/2026: saludo corto (antes «Hola…, aquí estoy como siempre, ¿en qué puedo
# ayudarte?», ~4 s en los que no se le podía hablar)
CALL_REPLY = f"Dime, {USUARIO}."
# Palabras con las que una frase no suele acabar: si termina así, el usuario sigue hablando
UNFINISHED_ENDINGS = {
    "que", "para", "y", "o", "pero", "porque", "como", "de", "del", "el", "la", "los",
    "las", "un", "una", "en", "con", "a", "al", "cuando", "por", "sin", "sobre", "entre",
    "hasta", "desde", "quiero", "puedes",
}
CONTINUE_WAIT_S = 3.0  # cuánto se espera a que termine una frase cortada
MAX_CONTINUATIONS = 3
MIC_RETRY_S = 5
# [Nau] Mientras Escriba dicta (y un poco después) Jarvis no hace caso de lo que oye
ESCRIBA_GRACE_S = 2.0
# [Nau] Un solo oyente: el vigilante y el arranque de sesión lo lanzaban a la vez (08/11/2026)
SINGLE_INSTANCE_MUTEX = "Local\\JarvisEscucha"
ERROR_ALREADY_EXISTS = 183

log = logging.getLogger("escucha")


def http(path: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    req = urllib.request.Request(
        API + path, data=data, headers={"Content-Type": content_type}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def wait_for_api() -> None:
    while True:
        try:
            urllib.request.urlopen(API + "/health", timeout=5).read()
            return
        except (urllib.error.URLError, OSError):
            log.info("Esperando a la API de Jarvis...")
            time.sleep(5)


def to_wav(pcm: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


def transcribe(pcm: np.ndarray) -> str:
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nes\r\n'.encode(),
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="voz.wav"\r\nContent-Type: audio/wav\r\n\r\n'
        ).encode()
        + to_wav(pcm)
        + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body, _ = http(
        "/v1/speech/transcribe", b"".join(parts), f"multipart/form-data; boundary={boundary}"
    )
    return str(json.loads(body).get("text", "")).strip()


DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre")


def clock_stamp() -> str:
    now = time.localtime()
    return (f"{DAYS[now.tm_wday]} {now.tm_mday} de {MONTHS[now.tm_mon - 1]} de {now.tm_year}, "
            f"{now.tm_hour:02d}:{now.tm_min:02d} (hora de Canarias)")


def now_context() -> dict:
    """Fecha y hora locales del PC, para que Jarvis no tenga que ejecutar comandos."""
    stamp = clock_stamp()
    return {
        "role": "system",
        "content": (
            f"Fecha y hora exactas ahora mismo, leídas del reloj del PC en este instante: {stamp}. "
            "Si te preguntan la hora o la fecha, contesta directamente con este dato, "
            "sin usar herramientas ni advertencias. Conversación por voz: habla como una persona, "
            "con frases breves y naturales, y no cierres cada respuesta con una pregunta. "
            "Cada conversación empieza de cero: no retomes temas ni preguntas de otras "
            f"conversaciones salvo que {USUARIO} te lo pida. "
            f"Si {USUARIO} te pide HACER algo en su ordenador o en sus cuentas (archivos, "
            "programas, correo, WhatsApp, Notion, Spotify, el navegador…), usa la "
            "herramienta encargar_a_claude con la orden completa y clara y contesta solo "
            "que te pones con ello; no digas que no puedes. Habla siempre en primera "
            "persona y no menciones nunca a Claude ni a Claude Code."
        ),
    }


def ask(history: list[dict], text: str, model: str) -> str:
    messages = [now_context(), *history, {"role": "user", "content": text}]
    payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    body, _ = http("/v1/chat/completions", payload, "application/json")
    content = json.loads(body)["choices"][0]["message"]["content"] or ""
    return THINK_TAGS.sub("", content).strip()


def synthesize(text: str) -> bytes:
    audio, _ = http(
        "/v1/speech/synthesize", json.dumps({"text": text[:4000]}).encode(), "application/json"
    )
    return audio


_play_slot = 0


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def playback_envelope(path: Path) -> np.ndarray:
    """Nivel (RMS) de lo que va a sonar, en trozos del mismo tamaño que los del micro."""
    try:
        with wave.open(str(path), "rb") as wav:
            rate, channels = wav.getframerate(), wav.getnchannels()
            if wav.getsampwidth() != 2:
                return np.zeros(0)
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    except (OSError, wave.Error, EOFError):
        return np.zeros(0)
    pcm = pcm.reshape(-1, channels).mean(axis=1) / 32768.0
    block = max(1, int(rate * FRAME / SAMPLE_RATE))
    usable = len(pcm) // block * block
    return np.sqrt(np.mean(pcm[:usable].reshape(-1, block) ** 2, axis=1))


class EchoGuard:
    """[Nau] Cuánto de lo que dice Jarvis vuelve al micro. Sigue el percentil alto de la
    relación micro/voz reproducida (sube rápido, baja despacio), así se adapta al volumen
    y a los auriculares sin que la voz del usuario lo dispare."""

    def __init__(self) -> None:
        # [Nau] Recuerda lo aprendido entre reinicios (con cascos baja despacio desde 0,5)
        try:
            saved = float(ECHO_GAIN_FILE.read_text(encoding="utf-8"))
            low, high = ECHO_GAIN_RANGE
            self.gain = min(high, max(low, saved))
        except (OSError, ValueError):
            self.gain = ECHO_GAIN_START
        self._tail = 0.0  # nivel del final del trozo anterior, cuyo eco aún puede llegar
        self._tail_until = 0.0

    def finish(self, envelope: np.ndarray) -> None:
        self._tail = float(envelope[-ECHO_WINDOW_FRAMES:].max()) if envelope.size else 0.0
        self._tail_until = time.monotonic() + ECHO_WINDOW_FRAMES * FRAME / SAMPLE_RATE
        try:
            ECHO_GAIN_FILE.write_text(f"{self.gain:.4f}", encoding="utf-8")
        except OSError:
            pass

    def tail(self) -> float:
        return self._tail if time.monotonic() < self._tail_until else 0.0

    def threshold(self, played: float, base: float, margin: float = ECHO_MARGIN) -> float:
        return max(base, margin * self.gain * played)

    def learn(self, played: float, heard: float) -> None:
        if played < ECHO_LOUD:
            return
        ratio = min(heard / played, 3 * self.gain)  # la voz del usuario no lo dispara
        step = 0.1 if ratio > self.gain else 0.01
        low, high = ECHO_GAIN_RANGE
        self.gain = min(high, max(low, self.gain + step * (ratio - self.gain)))

    def missed(self) -> None:
        """Se ha cortado con su propio eco (p. ej. al subir el volumen de golpe): más margen."""
        self.gain = min(ECHO_GAIN_RANGE[1], self.gain * 1.5)


_echo = EchoGuard()


def is_stop_order(heard: str, said: str) -> bool:
    """[Nau] ¿Hay en lo oído una orden de parar («para», «calla», «espera»…) que no sea eco
    de lo que Jarvis está diciendo («para» también es una preposición)? El nombre no cuenta:
    Whisper tiende a escribir «Jarvis» con el eco (lo empuja la palabra clave)."""
    said_words = {_plain(w) for w in re.findall(r"[\w']+", said)}
    return any(_plain(w) in STOP_WORDS - said_words for w in re.findall(r"[\w']+", heard))


def is_only_stop(text: str) -> bool:
    """«¡Para!», «Calla, Jarvis»: solo pide que se calle, no hay nada que contestar."""
    words = re.findall(r"[\w']+", text)
    fillers = STOP_WORDS | {"ya", "vale", "eh", "oye", "por", "favor"}
    return bool(words) and all(_plain(w) in fillers or is_jarvis(w) for w in words)


class StopCheck:
    """Transcribe en segundo plano una ventana de audio mientras Jarvis sigue hablando."""

    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = list(frames)
        self.heard: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="para")
        self._thread.start()

    def _run(self) -> None:
        try:
            self.heard = transcribe(np.concatenate(self.frames))
        except (urllib.error.URLError, OSError, KeyError, ValueError):
            self.heard = ""

    def finished(self) -> bool:
        return not self._thread.is_alive()


def watch_for_barge_in(stream: Microphone, floor: float | None, end: float,
                       envelope: np.ndarray | None = None, said: str = "") -> list[np.ndarray] | None:
    """Escucha mientras Jarvis habla. Si el usuario habla (~0,3 s claramente por encima del
    eco de la propia voz de Jarvis, o un «para» que confirma Whisper), corta la voz y
    devuelve ese audio (con algo de antes) para que sea el principio de su turno."""
    base = max(speech_threshold(floor), BARGE_IN_MIN_RMS)
    envelope = envelope if envelope is not None else np.zeros(0)
    recent: deque[np.ndarray] = deque(maxlen=SEGMENT_PREROLL_FRAMES + BARGE_IN_WINDOW)
    votes: deque[bool] = deque(maxlen=BARGE_IN_WINDOW)
    scan: deque[np.ndarray] = deque(maxlen=STOP_SCAN_FRAMES)
    echoing: deque[bool] = deque(maxlen=BARGE_IN_WINDOW)  # ¿el eco esperado supera al ruido?
    check: StopCheck | None = None
    since_scan = 0
    index = 0
    while time.monotonic() < end:
        frame = read_frame(stream)
        recent.append(frame)
        scan.append(frame)
        since_scan += 1
        rms = frame_rms(frame)
        window = envelope[max(0, index - ECHO_WINDOW_FRAMES):index + 1]
        played = max(float(window.max()) if window.size else 0.0, _echo.tail())
        index += 1
        threshold = _echo.threshold(played, base)
        votes.append(rms > threshold)
        echoing.append(threshold > base)
        if not votes[-1]:
            _echo.learn(played, rms)
        if escriba_dictando():
            continue
        # [Nau] Con cascos el eco no cuenta (umbral = el del ruido): un «para» de ~0,25 s basta
        needed = BARGE_IN_FRAMES if any(echoing) else BARGE_IN_FRAMES_QUIET
        if sum(votes) >= needed:
            winsound.PlaySound(None, 0)
            level = float(np.median([frame_rms(f) for f in list(recent)[-len(votes):]]))
            log.info("El usuario interrumpe (nivel %.3f, umbral %.3f, eco %.2f): me callo y escucho",
                     level, threshold, _echo.gain)
            return list(recent)
        if check is not None and check.finished():
            if is_stop_order(check.heard or "", said):
                winsound.PlaySound(None, 0)
                log.info("El usuario me pide que pare (%r): me callo y escucho", check.heard)
                return check.frames
            check = None
        # Whisper vigila los «para» solo si hay eco fuerte (altavoz): con cascos no hace falta
        if (check is None and any(echoing) and since_scan >= STOP_SCAN_EVERY
                and len(scan) >= STOP_SCAN_MIN_FRAMES):
            check = StopCheck(list(scan))
            since_scan = 0
    _echo.finish(envelope)
    return None


def play_file(path: Path, stream: Microphone | None = None,
              floor: float | None = None, said: str = "") -> list[np.ndarray] | None:
    """Reproduce un WAV. Con micro, se puede interrumpir hablando (devuelve lo oído)."""
    if stream is None:
        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return None
    end = time.monotonic() + wav_seconds(path) + PLAYBACK_MARGIN_S
    stream.drain()  # lo grabado antes de empezar a hablar no es una interrupción
    envelope = playback_envelope(path)
    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    return watch_for_barge_in(stream, floor, end, envelope, said)


def play_audio(audio: bytes, stream: Microphone | None, floor: float | None,
               said: str = "") -> list[np.ndarray] | None:
    """winsound no reproduce en segundo plano desde memoria: se alternan dos archivos."""
    global _play_slot
    _play_slot ^= 1
    PLAYBACK_DIR.mkdir(exist_ok=True)
    path = PLAYBACK_DIR / f"voz-{_play_slot}.wav"
    path.write_bytes(audio)
    return play_file(path, stream, floor, said)


def say_greeting(stream: Microphone) -> list[np.ndarray] | None:
    """El saludo fijo se sintetiza una vez y se guarda: suena al instante y no gasta saldo."""
    cached = ROOT / "data" / f"saludo-{hashlib.sha1(CALL_REPLY.encode()).hexdigest()[:8]}.wav"
    try:
        if not cached.exists():
            cached.write_bytes(synthesize(CALL_REPLY))
        return play_file(cached, stream, said=CALL_REPLY)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError):
        log.exception("No se pudo reproducir el saludo guardado")
        return say(CALL_REPLY, stream)


def speak_fallback(text: str) -> None:
    """Habla con la voz de Windows. El texto va por variable de entorno para no romper las comillas."""
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"try {{ $s.SelectVoice('{FALLBACK_VOICE}') }} catch {{ }}; "
        "$s.Speak($env:JARVIS_TEXTO); $s.Dispose()"
    )
    env = {**os.environ, "JARVIS_TEXTO": text}
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, timeout=120, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


_last_heartbeat = 0.0
_live_state: dict = {"estado": "idle", "texto": "", "nivel": 0.0, "latencia": None, "ts": 0.0}
_last_level_publish = 0.0


def publish(estado: str | None = None, texto: str | None = None, latencia: float | None = None,
            nivel: float | None = None) -> None:
    """Escribe el estado del oyente para el HUD: idle, listening, thinking o speaking."""
    if estado is not None:
        _live_state["estado"] = estado
    if texto is not None:
        _live_state["texto"] = texto
    if latencia is not None:
        _live_state["latencia"] = round(latencia, 2)
    if nivel is not None:
        _live_state["nivel"] = round(nivel, 4)
    _live_state["ts"] = time.time()
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(_live_state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass  # la API lo está leyendo justo ahora: se escribe en la siguiente


def publish_level(rms: float) -> None:
    """Nivel del micro mientras el usuario habla, como mucho cuatro veces por segundo."""
    global _last_level_publish
    now = time.monotonic()
    if now - _last_level_publish >= LEVEL_PUBLISH_EVERY_S:
        _last_level_publish = now
        publish(nivel=rms)


def heartbeat() -> None:
    """Marca de vida para vigilante.ps1: si deja de actualizarse, reinicia el oyente."""
    global _last_heartbeat
    now = time.time()
    if now - _last_heartbeat < HEARTBEAT_EVERY_S:
        return
    _last_heartbeat = now
    try:
        HEARTBEAT_FILE.write_text(str(int(now)), encoding="utf-8")
    except OSError:
        log.warning("No se pudo escribir la marca de vida")


def read_frame(stream: Microphone) -> np.ndarray:
    heartbeat()
    return stream.read_frame()


def flush(stream: Microphone) -> None:
    """Descarta el audio acumulado mientras Jarvis hablaba (para no oírse a sí mismo)."""
    stream.drain()


def frame_rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))


def speech_threshold(floor: float | None) -> float:
    """Energía a partir de la cual un tramo cuenta como voz."""
    if floor is None or floor <= DIGITAL_SILENCE_RMS:
        return MIN_SPEECH_RMS
    return max(MIN_SPEECH_RMS, floor * NOISE_MULTIPLIER)


def update_floor(floor: float | None, rms: float) -> float | None:
    """Ruido de fondo. [Nau] Tras un rato de silencio digital (el micro entrega ceros)
    la primera voz no se toma como ruido de fondo: antes eso subía el umbral por encima
    de la propia voz y se perdía el principio de la frase («¿Qué dices, Jarvis?»)."""
    if rms <= DIGITAL_SILENCE_RMS:
        return floor
    if floor is None or floor <= DIGITAL_SILENCE_RMS:
        return min(rms, MIN_SPEECH_RMS)
    return rms if rms < floor else floor + (rms - floor) * FLOOR_RISE


def record_utterance(
    stream: Microphone, no_speech_s: float, prefix: list[np.ndarray] | None = None,
    floor: float | None = None,
) -> tuple[np.ndarray | None, float]:
    """Graba hasta que hay silencio tras hablar. Devuelve (audio o None si nadie habla, suelo de ruido).

    prefix: audio en el que ya se ha detectado voz (El usuario ha seguido hablando).
    """
    frames: list[np.ndarray] = list(prefix or [])
    frame_s = FRAME / SAMPLE_RATE
    speech_run = 0
    heard = bool(prefix)
    silent_s = 0.0
    elapsed = 0.0
    while elapsed < MAX_UTTERANCE_S:
        frame = read_frame(stream)
        frames.append(frame)
        elapsed += frame_s
        rms = frame_rms(frame)
        publish_level(rms)
        if rms > speech_threshold(floor):
            speech_run += 1
            heard = heard or speech_run >= MIN_SPEECH_FRAMES
            silent_s = 0.0
        else:
            speech_run = 0
            silent_s += frame_s
        floor = update_floor(floor, rms)
        if heard and silent_s >= SILENCE_S:
            break
        if not heard and elapsed >= no_speech_s:
            return None, floor
    return (np.concatenate(frames) if heard else None), (floor or 0.0)


def resumed_speech(stream: Microphone, floor: float) -> list[np.ndarray] | None:
    """Audio grabado mientras Jarvis transcribía o pensaba, si en él el usuario ha seguido hablando."""
    frames = stream.drain()
    threshold = speech_threshold(floor)
    run = 0
    for i, frame in enumerate(frames):
        run = run + 1 if frame_rms(frame) > threshold else 0
        if run >= MIN_SPEECH_FRAMES:
            return frames[max(0, i - run + 1 - SEGMENT_PREROLL_FRAMES):]
    return None


def useful(text: str) -> bool:
    return bool(text) and not HALLUCINATIONS.search(text)


def say(text: str, stream: Microphone | None = None,
        floor: float | None = None) -> list[np.ndarray] | None:
    return say_chunks(speech_chunks(text), stream, floor)


def _offer(box: queue.Queue, item: object, stop: threading.Event) -> None:
    """Deja un trozo de voz para reproducir, salvo que ya no haga falta."""
    while not stop.is_set():
        try:
            box.put(item, timeout=0.2)
            return
        except queue.Full:
            continue


def say_chunks(chunks: Iterable[str], stream: Microphone | None = None,
               floor: float | None = None) -> list[np.ndarray] | None:
    """Habla con ElevenLabs trozo a trozo: un hilo prepara el siguiente (esperando, si hace
    falta, a que Sonnet lo escriba) mientras suena el actual. Con micro, el usuario puede
    interrumpir (devuelve lo que ha empezado a decir). Si ElevenLabs falla (saldo agotado
    o clave rechazada), sigue con la voz de Windows."""
    source = iter(chunks)
    ready: queue.Queue = queue.Queue(maxsize=1)  # como mucho un trozo sintetizado de más
    stop = threading.Event()

    def synth_ahead() -> None:
        for chunk in source:
            if stop.is_set():
                return
            try:
                _offer(ready, (chunk, synthesize(chunk), None), stop)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                _offer(ready, (chunk, None, exc), stop)
                return
        _offer(ready, None, stop)

    threading.Thread(target=synth_ahead, daemon=True, name="voz").start()
    started = time.monotonic()
    first = True
    while (item := ready.get()) is not None:
        chunk, audio, error = item
        if error is not None:
            log.error("No se pudo sintetizar la voz con ElevenLabs: %s", error)
            stop.set()
            say_fallback(" ".join([chunk, *source]))
            return None
        if first:
            log.info("Voz: primera frase sonando a los %.2f s", time.monotonic() - started)
            first = False
        heard = play_audio(audio, stream, floor, chunk)
        if heard is not None:
            stop.set()
            return heard
    return None


def say_fallback(text: str) -> None:
    try:
        speak_fallback(SALDO_REPLY + text)
    except (OSError, subprocess.SubprocessError):
        log.exception("Tampoco funciona la voz de respaldo")
        winsound.Beep(300, 300)


def is_goodbye(text: str) -> bool:
    """¿Cierra el usuario la conversación? Mira el final de la frase, sin el nombre ni cortesías."""
    if text.rstrip().endswith("?"):
        return False
    words = [_plain(w) for w in re.findall(r"[\w']+", text) if not is_jarvis(w)]
    while words:
        if GOODBYE_END.search(" ".join(words)):
            return True
        if words[-1] not in GOODBYE_TAIL:
            return False
        words.pop()
    return False


def looks_unfinished(text: str) -> bool:
    """¿Se ha cortado la frase a medias («Quiero que...», «cómo puedo configurarte para»)?"""
    stripped = text.strip()
    if stripped.endswith(("...", "…")):
        return True
    words = re.findall(r"[\w']+", stripped)
    return bool(words) and _plain(words[-1]) in UNFINISHED_ENDINGS and not stripped.endswith(("?", "!"))


class Turn:
    """Lo que el usuario dice en un turno: el audio acumulado y su transcripción."""

    def __init__(self, pcm: np.ndarray, floor: float) -> None:
        self.pcm = pcm
        self.floor = floor
        self.heard_at = time.monotonic()  # el usuario acaba de callar: aquí empieza la espera
        self.text = transcribe(pcm)
        self.transcribed_s = time.monotonic() - self.heard_at

    def keep_listening(self, stream: Microphone, resumed: list[np.ndarray] | None = None) -> None:
        """Mientras el usuario siga hablando (o la frase se haya quedado a medias), sigue grabando."""
        for _ in range(MAX_CONTINUATIONS):
            if resumed is None:
                resumed = resumed_speech(stream, self.floor)
            if resumed is None and not looks_unfinished(self.text):
                return
            log.info("El usuario sigue hablando: %r", self.text)
            more, self.floor = record_utterance(stream, CONTINUE_WAIT_S, resumed, self.floor)
            resumed = None
            if more is None:
                return
            self.pcm = np.concatenate([self.pcm, more])
            self.text = transcribe(self.pcm)


def get_reply(messages: list[dict], model: str, text: str, recalled: dict | None) -> str | None:
    context = [*messages, recalled] if recalled else messages
    try:
        return ask(context, text, model)
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        log.exception("Fallo al pedir la respuesta")
        return None


Heard = list[np.ndarray] | None  # audio con el que el usuario ha interrumpido a Jarvis


def done_speaking(stream: Microphone, heard: Heard) -> Heard:
    """Tras hablar: si el usuario no ha interrumpido, se descarta lo grabado mientras tanto."""
    if heard is None:
        flush(stream)
    publish("listening", "")
    return heard


def start_answer(messages: list[dict], model: str, text: str,
                 recalled: dict | None) -> Answer:
    """Pide la respuesta a Sonnet (por trozos); si falla, a Haiku con el historial."""
    memory = f"[Memoria de conversaciones anteriores: {recalled['content']}]\n" if recalled else ""
    prompt = f"[Ahora: {clock_stamp()}]\n{memory}{text}"
    return Answer(BRAIN, prompt, lambda: get_reply(messages, model, text, recalled))


def deliver(stream: Microphone, messages: list[dict], text: str, answer: Answer,
            first: str | None, recalled: dict | None, latency: float | None = None,
            floor: float | None = None) -> Heard:
    """Dice la respuesta según llega (first ya está listo) y la apunta en el historial."""
    log.info("%s: %s", USUARIO, text)
    if first is None:
        publish("speaking", ERROR_REPLY)
        return done_speaking(stream, say(ERROR_REPLY, stream, floor))
    publish("speaking", first, latencia=latency)
    heard = say_chunks(chain([first], answer), stream, floor)
    if heard is not None:
        answer.cancel()
        BRAIN.note(INTERRUPTED_BRAIN_NOTE)
    reply = answer.text
    log.info("Jarvis (%s, %.1f s)%s: %s", answer.source, latency or 0.0,
             " [interrumpido]" if heard else "", reply)
    if recalled:
        messages.append(recalled)
    messages.append({"role": "user", "content": text})
    messages.append({"role": "assistant", "content": reply + (INTERRUPTED_NOTE if heard else "")})
    return done_speaking(stream, heard)


def _last_said(messages: list[dict]) -> str:
    said = next((m["content"] for m in reversed(messages) if m.get("role") == "assistant"), "")
    return said.replace(INTERRUPTED_NOTE, "")


def is_own_echo(heard: str, said: str) -> bool:
    """[Nau] ¿Lo que «interrumpió» a Jarvis es su propia voz vuelta al micro? Whisper la
    transcribe parecida a lo que decía («pola naú» por «Hola Nau», «Claro, no» por «Claro, Nau»)."""
    heard_words = [_plain(w) for w in re.findall(r"[\w']+", heard)]
    said_words = [_plain(w) for w in re.findall(r"[\w']+", said)]
    if not heard_words or len(said_words) < len(heard_words):
        return False
    target = " ".join(heard_words)
    size = len(heard_words)
    return any(
        difflib.SequenceMatcher(None, target, " ".join(said_words[i:i + size])).ratio() >= ECHO_SIMILARITY
        for i in range(len(said_words) - size + 1)
    )


def respond(stream: Microphone, messages: list[dict], model: str, turn: Turn,
            interrupted: bool = False) -> tuple[bool, Heard]:
    """Responde cuando el usuario ha terminado de hablar. False si se despide o no dice nada útil."""
    resumed = None
    answer, first, recalled = None, None, None
    publish("thinking", "")
    for _ in range(MAX_REASKS + 1):
        turn.keep_listening(stream, resumed)
        publish("thinking", turn.text)
        if not useful(turn.text):
            log.info("Ignorado (sin texto útil): %r", turn.text)
            return False, None
        if interrupted and is_own_echo(turn.text, _last_said(messages)):
            log.info("Lo que me interrumpió era mi propio eco (%r): no contesto y sigo escuchando", turn.text)
            _echo.missed()
            publish("listening", "")
            return True, None
        if interrupted and is_only_stop(turn.text):
            log.info("%s: %s (solo me pide que pare: me callo y escucho)", USUARIO, turn.text)
            publish("listening", "")
            return True, None
        interrupted = False
        if is_goodbye(turn.text):
            log.info("%s: %s (fin de la conversación)", USUARIO, turn.text)
            publish("speaking", GOODBYE_REPLY)
            say(GOODBYE_REPLY)
            flush(stream)
            return False, None
        recalled = MEMORY.recall(turn.text) if RESUME_REQUEST.search(turn.text) else None
        answer = start_answer(messages, model, turn.text, recalled)
        first = next(answer, None)
        # Si el usuario ha seguido hablando mientras Jarvis pensaba, no le interrumpe:
        # sigue escuchando y responde a la frase entera.
        resumed = resumed_speech(stream, turn.floor)
        if resumed is None:
            break
        log.info("El usuario ha seguido hablando mientras pensaba; descarto la respuesta")
        answer.cancel()
        BRAIN.note(RESUMED_BRAIN_NOTE)
    assert answer is not None
    latency = time.monotonic() - turn.heard_at
    # [Nau] para medir dónde se va el tiempo (sin contar los SILENCE_S de detectar el final)
    log.info("Tiempos: transcripción %.2f s, primera frase lista a los %.2f s",
             turn.transcribed_s, latency)
    heard = deliver(stream, messages, turn.text, answer, first, recalled, latency, turn.floor)
    return True, heard


def handle_turn(stream: Microphone, messages: list[dict], model: str, wait_s: float,
                prefix: Heard = None) -> tuple[bool, Heard]:
    """Graba lo que dice el usuario y lo responde. False si el usuario calla o se despide.

    prefix: lo que el usuario dijo al interrumpir a Jarvis (su turno empieza ahí)."""
    pcm, floor = record_utterance(stream, wait_s, prefix)
    if pcm is None:
        return False, None
    if escriba_dictando():
        log.info("El usuario está dictando con Escriba: cierro la conversación sin responder")
        return False, None
    return respond(stream, messages, model, Turn(pcm, floor), interrupted=prefix is not None)


def finished_tasks() -> list[dict]:
    """Tareas de Claude Code terminadas que aún no se han contado al usuario (y las marca)."""
    done = []
    for path in sorted(TASKS_DIR.glob("*.json")):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # se está escribiendo justo ahora: se lee en la siguiente vuelta
        if task.get("anunciado") or task.get("estado") not in ("hecha", "error"):
            continue
        task["anunciado"] = True
        tmp = path.with_suffix(".anuncio")
        try:
            tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.exception("No se pudo marcar como anunciada la tarea %s", path.name)
            continue
        done.append(task)
    return done


def task_announcement(task: dict) -> tuple[str, dict]:
    """Lo que Jarvis dice al acabar una tarea y el contexto para seguir hablando de ella."""
    result = str(task.get("resultado") or "").strip()
    fact = result.rstrip(".")  # para citarlo dentro de otra frase
    session = task.get("session_id") or ""
    order = task.get("tarea", "")
    if task.get("estado") == "error":
        text = f"{USUARIO}, no he podido terminar lo que me pediste. {result}"
        note = f"La tarea «{order}» ha fallado: {fact}."
    elif task.get("confirmar"):
        text = f"{USUARIO}, antes de seguir necesito tu permiso. {result} ¿Lo hago?"
        note = (f"Tu tarea en segundo plano «{order}» espera el permiso de {USUARIO}. Lo que harás: "
                f"{fact}. Si {USUARIO} dice que sí, vuelve a encargarla con "
                f"sesion=\"{session}\", confirmado=true y tarea=\"Sí, adelante, hazlo.\". "
                "Si dice que no o duda, no la llames y díselo.")
    else:
        text = f"{USUARIO}, ya está. {result}"
        note = f"Has terminado en segundo plano la tarea «{order}»: {fact}."
    if session:
        note += (f" Si {USUARIO} quiere cambiar o seguir con esa tarea, vuelve a encargarla "
                 f"con sesion=\"{session}\".")
    return text, {"role": "system", "content": note}


def announce_tasks(stream: Microphone, messages: list[dict], tasks: list[dict]) -> Heard:
    """Cuenta al usuario cómo han acabado las tareas que encargó a Claude Code."""
    heard = None
    for task in tasks:
        text, note = task_announcement(task)
        log.info("Jarvis (tarea %s): %s", task.get("id"), text)
        messages.extend([note, {"role": "assistant", "content": text}])
        BRAIN.note(f"{note['content']} Ya se lo has dicho a {USUARIO} así: «{text}»")
        publish("speaking", text)
        heard = done_speaking(stream, say(text, stream))
        if heard is not None:
            break  # el usuario interrumpe: las demás se cuentan después
    return heard


def conversation(stream: Microphone, model: str, first_question: str = "",
                 tasks: list[dict] | None = None, messages: list[dict] | None = None) -> None:
    """Una conversación; al terminar se guarda en la memoria lo dicho en ella.

    messages: el hilo de los últimos minutos (vacío si es una conversación nueva).
    Con pregunta en la misma frase («Jarvis, ¿qué hora es?») responde directamente;
    si solo le llaman, saluda con la frase fija; si empieza por tareas terminadas
    de Claude Code, las cuenta.
    """
    started = time.time()
    messages = [] if messages is None else messages
    del messages[: max(0, len(messages) - 2 * HISTORY_TURNS)]
    first_new = len(messages)
    try:
        if tasks:
            heard = announce_tasks(stream, messages, tasks)
        elif first_question:
            started_at = time.monotonic()
            publish("thinking", first_question)
            recalled = MEMORY.recall(first_question) if RESUME_REQUEST.search(first_question) else None
            answer = start_answer(messages, model, first_question, recalled)
            first = next(answer, None)
            heard = deliver(stream, messages, first_question, answer, first, recalled,
                            time.monotonic() - started_at)
        else:
            log.info("Jarvis: %s", CALL_REPLY)
            messages.append({"role": "assistant", "content": CALL_REPLY})
            publish("speaking", CALL_REPLY)
            heard = done_speaking(stream, say_greeting(stream))
        while True:
            if heard is None:
                heard = announce_tasks(stream, messages, finished_tasks())
            keep_going, heard = handle_turn(stream, messages, model, FOLLOW_UP_S, heard)
            if not keep_going:
                break
        winsound.Beep(440, 120)
    finally:
        publish("idle", "")
        MEMORY.save(started, messages[first_new:])
        log.info("Conversación terminada y guardada (%d mensajes)", len(messages) - first_new)


def _plain(word: str) -> str:
    decomposed = unicodedata.normalize("NFD", word.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _consonant_skeleton(word: str) -> str:
    """Consonantes tal como suenan: h/y/g/j → j, b/v → b, z → s; sin vocales ni dobles."""
    w = re.sub(r"ll|ch|sh|[hygjx]", "j", word)
    w = re.sub(r"[vbw]", "b", w)
    w = re.sub(r"[aeiou]", "", w.replace("z", "s"))
    return re.sub(r"(.)\1+", r"\1", w)


def is_jarvis(word: str) -> bool:
    """¿Es «Jarvis» tal como Whisper suele escribirlo («Yarmis», «Jarbis», «Harvis»…)?"""
    w = _plain(word)
    if WAKE_NAME_WORD.fullmatch(w):
        return True
    # Esqueleto fonético: «Hierbis», «Gervis», «Yarbis»… suenan igual que «Jarvis».
    # [Nau] 09/11/2026: sin «jbs», que dejaba pasar «habéis» («Javis» ya está en WAKE_NAME_WORD).
    if w.endswith(("is", "ys")) and _consonant_skeleton(w) == "jrbs":
        return True
    # Parecido fonético: 5-7 letras, acaba en -is/-ys y se parece a «jarvis».
    return (
        5 <= len(w) <= 7
        and w.endswith(("is", "ys"))
        and difflib.SequenceMatcher(None, w, "jarvis").ratio() >= NAME_SIMILARITY
    )


def _wake_hit(text: str) -> re.Match | None:
    """[Nau] 09/11/2026: solo despierta con «¿Qué pasa, Jarvis?» (o «¿Qué dices, Jarvis?»,
    como suele escribirlo Whisper), al principio de la frase: «Oye, ¿qué pasa, Jarvis?» sí;
    «Hola Jarvis», «Jarvis, mira…» o hablar de él a mitad de frase, no."""
    words = list(re.finditer(r"[\w']+", text))
    for i, match in enumerate(words):
        if i < 2 or not is_jarvis(match.group()):
            continue
        if _plain(words[i - 2].group()) != "que" or _plain(words[i - 1].group()) not in CALL_WORDS:
            continue
        start = max((m.end() for m in re.finditer(r"[.?!¿¡…;]", text[:words[i - 2].start()])), default=0)
        before = re.findall(r"[\w']+", text[start:words[i - 2].start()])
        if all(_plain(w) in LEAD_WORDS for w in before):
            return match
    return None


def wake_by_name(text: str) -> str | None:
    """Si la frase es «¿Qué pasa, Jarvis?» devuelve lo que pide después ('' si solo le llama)."""
    hit = _wake_hit(text)
    if hit is None:
        return None
    rest = text[hit.end():].strip(" ,.;:!?¡¿")
    # «¿Qué pasa, Jarvis? ¿Estás por ahí?»: solo le llama → saludo fijo
    if len(rest.split()) < 2 or PRESENCE_CHECK.fullmatch(_plain(rest)):
        return ""
    return rest


class Segmenter:
    """Corta el audio continuo en frases cortas usando la energía de la señal."""

    def __init__(self) -> None:
        self.floor: float | None = None
        self.preroll: list[np.ndarray] = []
        self.frames: list[np.ndarray] = []
        self.speech_run = 0
        self.silent_s = 0.0

    def push(self, frame: np.ndarray, rms: float) -> np.ndarray | None:
        """Devuelve una frase completa cuando termina, o None."""
        frame_s = FRAME / SAMPLE_RATE
        # El micro entrega silencio digital (0.000) en ratos de calma: no cuenta como ruido de fondo
        loud = rms > speech_threshold(self.floor)
        self.floor = update_floor(self.floor, rms)

        if not self.frames:
            self.preroll = [*self.preroll[-(SEGMENT_PREROLL_FRAMES - 1):], frame]
            self.speech_run = self.speech_run + 1 if loud else 0
            if self.speech_run >= MIN_SPEECH_FRAMES:
                self.frames = list(self.preroll)
                self.silent_s = 0.0
            return None

        self.frames.append(frame)
        self.silent_s = 0.0 if loud else self.silent_s + frame_s
        if len(self.frames) * frame_s > SEGMENT_MAX_S:
            # Ruido o charla continua: se transcribe este trozo y se sigue con un
            # solape, para no perder un «Hola Jarvis» que caiga en medio o en el corte.
            segment = np.concatenate(self.frames)
            self.frames = self.frames[-SEGMENT_OVERLAP_FRAMES:]
            log.info("Tramo largo troceado (suelo de ruido %.3f)", self.floor)
            return segment
        if self.silent_s < SEGMENT_END_SILENCE_S:
            return None
        segment = np.concatenate(self.frames)
        self.frames, self.preroll, self.speech_run = [], [], 0
        if len(segment) / SAMPLE_RATE < SEGMENT_MIN_S:
            return None
        return segment


class ConversationThread:
    """[Nau] El hilo de la charla: si el usuario vuelve a llamar a Jarvis antes de
    CONTINUE_WINDOW_S, sigue donde lo dejaron. Pasado ese rato, Sonnet empieza una
    sesión nueva (ya arrancada, para que la primera respuesta no espere)."""

    def __init__(self) -> None:
        self._messages: list[dict] = []
        self._expires: float | None = None

    def messages(self) -> list[dict]:
        return self._messages

    def touch(self) -> None:
        self._expires = time.monotonic() + CONTINUE_WINDOW_S

    def expire_if_old(self) -> None:
        if self._expires is not None and time.monotonic() >= self._expires:
            self._expires = None
            self._messages = []
            BRAIN.reset()
            log.info("Hilo de la conversación cerrado: la próxima empieza de cero")


_thread = ConversationThread()


def listen(stream: Microphone, wake: Model, model_name: str) -> None:
    """Bucle principal: espera a oír su nombre y lleva la conversación."""
    segmenter = Segmenter()
    peak_rms = 0.0
    last_level_log = time.monotonic()
    last_sound = time.monotonic()
    deaf_until = 0.0  # [Nau] hasta cuándo se ignora lo oído por un dictado de Escriba
    last_task_check = time.monotonic()
    log.info("Escuchando «¿Qué pasa, Jarvis?» (cerebro Sonnet; reserva %s)", model_name)
    publish("idle", "")
    while True:
        frame = read_frame(stream)
        rms = frame_rms(frame)
        peak_rms = max(peak_rms, rms)
        now = time.monotonic()
        if rms > DIGITAL_SILENCE_RMS:
            last_sound = now
        elif now - last_sound > MIC_SILENT_RESET_S:
            raise MicUnavailable(f"{MIC_SILENT_RESET_S}s de silencio digital absoluto")
        if now - last_level_log > LEVEL_LOG_EVERY_S:
            log.info("Nivel máximo del micro en los últimos %ds: %.3f", LEVEL_LOG_EVERY_S, peak_rms)
            peak_rms, last_level_log = 0.0, now

        question: str | None = None
        tasks: list[dict] = []
        score = wake.predict(frame).get(WAKE_MODEL, 0.0) if WAKE_BY_HEY_JARVIS else 0.0
        # [Nau] el usuario dicta con Escriba: ni «Hey Jarvis» ni nombre; el audio se descarta sin transcribir
        if escriba_dictando():
            if now >= deaf_until:
                log.info("Escriba está dictando: Jarvis no escucha")
            deaf_until = now + ESCRIBA_GRACE_S
        if now < deaf_until:
            if segmenter.frames:
                segmenter = Segmenter()
            continue
        if score >= WAKE_THRESHOLD:
            log.info("Activado por «Hey Jarvis» (%.2f)", score)
            question = ""
        elif score >= NEAR_MISS:
            log.info("Casi (%.2f)", score)

        segment = segmenter.push(frame, rms)
        if question is None and segment is not None:
            try:
                heard = transcribe(segment)
            except (urllib.error.URLError, OSError, KeyError, ValueError):
                log.exception("No se pudo transcribir una frase")
                heard = ""
            question = wake_by_name(heard) if useful(heard) else None
            if question is not None:
                log.info("Activado por nombre: %r", heard)
            else:
                # Solo métricas: lo que no va dirigido a Jarvis no se guarda (privacidad)
                log.info("Oído (sin «Jarvis»): %d palabras (pico %.3f)",
                         len(heard.split()), float(np.abs(segment).max()) / 32768)
        # [Nau] Tarea de Claude Code terminada: se avisa si nadie está hablando
        if question is None and not segmenter.frames and now - last_task_check >= TASK_CHECK_EVERY_S:
            last_task_check = now
            tasks = finished_tasks()
        if question is None and not tasks:
            _thread.expire_if_old()
            continue

        try:
            conversation(stream, model_name, question or "", tasks, _thread.messages())
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            log.exception("Fallo en la conversación: %s", exc)
            winsound.Beep(300, 300)
            flush(stream)
        _thread.touch()
        wake.reset()
        segmenter = Segmenter()
        # La conversación lee el micro por su cuenta: sin esto, al acabar una de más de
        # 10 min se tomaba como «silencio digital» y se reabría el micro sin motivo
        last_sound = time.monotonic()


def run() -> None:
    wait_for_api()
    try:
        BRAIN.start()  # en caliente para la primera pregunta
    except Exception:  # noqa: BLE001 - sin Sonnet, Jarvis contesta con la reserva Haiku
        log.exception("No arranca el cerebro Sonnet; usaré la reserva Haiku")
    model_name = VOICE_MODEL
    wake = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
    MEMORY.import_legacy(LEGACY_HISTORY_FILE)
    # Si Windows pierde el micro (auriculares USB desconectados, vuelta de
    # suspensión, cambio de dispositivo), se vuelve a abrir en vez de dejar de escuchar.
    while True:
        try:
            with Microphone() as stream:
                listen(stream, wake, model_name)
        except (sd.PortAudioError, MicUnavailable) as exc:
            log.warning("Micrófono no disponible (%s); reintento en %ds", exc, MIC_RETRY_S)
            time.sleep(MIC_RETRY_S)
            sd._terminate()  # refresca la lista de dispositivos de audio
            sd._initialize()
            wake.reset()


def main() -> None:
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )
    # [Nau] Si ya hay un oyente, este se cierra: dos a la vez contestaban dos veces
    mutex = ctypes.WinDLL("kernel32", use_last_error=True).CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        log.info("Ya hay otro oyente en marcha; este se cierra")
        return
    log.info("Oyente iniciado")
    # Siempre encendido: cualquier fallo inesperado se registra y el oyente vuelve a empezar.
    while True:
        try:
            run()
        except Exception:
            log.exception("Fallo inesperado en el oyente; vuelvo a empezar en %ds", RESTART_DELAY_S)
            time.sleep(RESTART_DELAY_S)


if __name__ == "__main__":
    main()
