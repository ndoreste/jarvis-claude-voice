"""Oyente manos libres de Jarvis.

Escucha el micrófono en local y se activa solo con «Hola Jarvis» (desde el 21/11/2026):
cada frase corta se transcribe con el Whisper local de la API de Jarvis y se busca la
llamada (_wake_hit). «Hey Jarvis» por openWakeWord
está desactivado desde el 09/11/2026 (WAKE_BY_HEY_JARVIS = False).
Si solo le llaman saluda con la frase fija; si la pregunta va
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
import shutil
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
from datetime import datetime
from itertools import chain
from pathlib import Path

import numpy as np
import sounddevice as sd
from openwakeword.model import Model

from acciones_pc import duck_media, restore_media
from ajustes import USUARIO
from cerebro_claude import ClaudeBrain
from escriba_activo import escriba_dictando
from memoria_voz import RESUME_REQUEST, VoiceMemory
from eco_aec import EchoCanceller
from microfono import FRAME, SAMPLE_RATE, Microphone, MicUnavailable
from respuesta import Answer, speech_chunks
from trabajador_claude import ensure_running as ensure_worker

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
# 0,5 → 0,4 el 24/11/2026 (-0,1 s por turno; la red de seguridad de arriba sigue).
SILENCE_S = 0.4
# [Nau] 26/11/2026: a los 0,15 s de pausa ya se empieza a transcribir (ver EarlyTranscript)
EARLY_STT_SILENCE_S = 0.15
EARLY_STT_TAIL_RATIO = 0.35  # sonido tras la anticipada por encima de esto (× umbral de voz): se descarta
NO_SPEECH_S = 6.0
FOLLOW_UP_S = 40.0  # margen para seguir hablando tras cada respuesta ([Nau] 12 → 20 s el 26/11; 20 → 40 s el 27/11/2026)
MAX_UTTERANCE_S = 20.0
# [Nau] 26/11/2026: lo que Nau le pide se transcribe con Whisper «small»: entiende el habla a
# ritmo normal (con «base», «Hola Jarvis, ábreme…» dicho seguido salía «Por la Jarvis…»).
# Tarda ~1,2 s en vez de ~0,5 s (medido); Nau eligió entender mejor. Despertar sigue con «base».
CONVERSATION_STT_MODEL = "small"

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
# [Nau] 10/11/2026: con la voz por el altavoz (BS9655) y el micro USB, el eco vuelve con
# ~0,41 s de retraso y a ~0,5 del volumen que suena (medido): superaba el umbral y Jarvis se
# cortaba solo a mitad de frase, y Whisper lo transcribía como frases sin sentido. Ahora el
# micro pasa por la cancelación de eco (eco_aec.py, -17 dB) mientras Jarvis habla. Si aun así
# el eco que queda es fuerte (eco esperado > umbral de voz con una frase normal, o sin AEC),
# hablar no le corta: solo una orden de parar («para», «espera», «calla»…) que oiga Whisper.
ECHO_STRONG_GAIN = 0.13  # 1,5 × 0,13 × 0,2 (voz fuerte de Jarvis) ≈ 0,04 = BARGE_IN_MIN_RMS
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
# [Nau] 26/11/2026: con una tarea en marcha la conversación sigue abierta (wait_for_tasks)
TASK_POLL_S = 2.0  # cada cuánto mira si la tarea ha acabado mientras escucha
TASK_WAIT_MAX_S = 5 * 60  # como mucho; después se cierra y el resultado llega suelto, como antes
TASK_PROGRESS_AFTER_S = 30.0  # si tarda más, avisa una vez de que sigue con ello
TASK_PROGRESS_REPLY = "Sigo con ello, señor."
TASK_STALE_S = 30 * 60  # como _STALE_S de claude_code.py: más tiempo «en curso» = proceso muerto
STALE_CHECK_EVERY_S = 10 * 60
# [Nau] 26/11/2026: en 14 de 20 despertares tras 15+ min parado hubo antes una frase corta sin
# reconocer: ¿se pierde el principio de «Hola Jarvis» tras mucho silencio? Se apunta (sin texto)
MISSED_CALL_MAX_WORDS = 5
MISSED_CALL_WINDOW_S = 60.0
# [Nau] 26/11/2026: en la conversación, lo que no lleva su nombre se confirma (screen_addressee)
ADDRESS_QUESTION = "¿Es a mí, señor?"
ADDRESS_NO_REPLY = "Entendido, señor."
ADDRESS_QUIET_S = 20.0  # tras preguntar, no vuelve a preguntar en este rato
ADDRESS_ANSWER_S = 10.0  # lo que diga en este rato tras la pregunta es la respuesta (va para él)
ADDRESS_FOLLOW_S = 10.0  # si empieza a hablar en este rato tras su respuesta, va para él
JUNK_TURNS_MAX = 2  # frases de ruido seguidas que no cierran la conversación
YES_WORDS = {"si", "claro", "exacto", "correcto", "afirmativo", "efectivamente", "eso", "obvio"}
NO_REPLY = re.compile(r"^(?:no|nada|tranquilo|olvidalo|dejalo|da igual|era otra|hablaba con)\b")
# Palabras de la respuesta que no son una petición nueva («No, no era para ti, Jarvis»)
ADDRESS_REPLY_FILLER = {"no", "si", "era", "es", "para", "ti", "a", "te", "contigo", "hablaba",
                        "nada", "tranquilo", "senor", "con", "otra", "otro", "persona", "claro",
                        "eso", "exacto", "correcto", "vale", "sigue", "tu", "olvidalo", "dejalo"}
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
# [Nau] 26/11/2026: con música Whisper entra en bucle («No, no, no…» ×200). Una palabra repetida
# más de REPEAT_WORD_MAX veces seguidas (o 2–6 palabras más de REPEAT_PHRASE_MAX veces) se deja en
# una; si lo repetido era casi todo (REPEAT_NOISE_RATIO), la frase entera se trata como ruido.
REPEAT_WORD_MAX = 8
REPEAT_PHRASE_MAX = 4
REPEAT_PHRASE_MAX_WORDS = 6
REPEAT_NOISE_RATIO = 0.6
# [Nau] 26/11/2026: un fallo pasajero de la API (500 al reiniciarse) perdía la frase entera
TRANSCRIBE_RETRY_S = 0.5
THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)
# [Nau] Frases con las que el usuario cierra la conversación: basta con que la frase ACABE así
# («Gracias Jarvis, adiós», «…puedes descansar, hasta luego Jarvis»). Se comparan sin
# tildes, sin signos y sin el nombre. Las preguntas («¿cómo se dice adiós?») no cuentan.
GOODBYE_END = re.compile(
    r"(?:^|\s)(?:adios|adio|hasta luego|hasta manana|hasta otra|hasta despues|hasta la proxima"
    r"|chao|chau|nos vemos|descansa|descansar|descanza|descanzar|eso es todo|eso era todo"
    r"|nada mas|ya esta|ya no necesito nada|no necesito nada mas|buenas noches|me voy"
    r"|a dormir|puedes dormir|duermete|no gracias|hasta pronto|luego hablamos|hablamos luego"
    r"|apagate|desconectate|gracias por todo|ya puedes irte|puedes irte|retirate|a descansar)$"
    # [Nau] 25/11/2026: «duérmete», «hasta pronto». 26/11/2026: «adió» (Whisper se come la s),
    # «descanzar», «puedes dormir», «apágate», «luego hablamos», «gracias por todo», «puedes irte»
)
# [Nau] 25/11/2026: la despedida solo cierra si la frase es corta (sin contar el nombre):
# un «adiós» dicho a otra persona dentro de una frase larga no apaga a Jarvis.
# 26/11/2026: 6 → 10 palabras («No, gracias, ya voy a descansar, hasta luego» no cerraba).
GOODBYE_MAX_WORDS = 10
# Palabras de cortesía que pueden ir detrás de la despedida («adiós, gracias, señor»)
GOODBYE_TAIL = {"gracias", "muchas", "senor", "bueno", "vale", "ok", "okay", "perfecto",
                "genial", "pues", "entonces", "ya", "eh", "venga", "nada"} | {USUARIO.casefold()}
# [Nau] 26/11/2026: «Adiós, Jean-Pierre», «Adiós, YAMI»: el nombre mal oído detrás de una
# despedida que ABRE una frase muy corta también cierra
GOODBYE_START = re.compile(r"^(?:adios|adio|chao|chau|hasta luego|hasta manana|buenas noches)\b")
GOODBYE_START_MAX_WORDS = 3
GOODBYE_WORDS = {"adios", "adio", "chao", "chau"}
GOODBYE_ADIOS_MAX_WORDS = 5
GOODBYE_NAME_TAIL_MAX_WORDS = 6
# «Vale, gracias Jarvis»: una frase que es SOLO agradecimiento también cierra
THANKS_ONLY = {"gracias", "muchas", "vale", "perfecto", "genial", "ok", "okay", "bueno", "pues",
               "a", "senor", "mil", "muy", "bien", "estupendo", "guay"}
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
# [Nau] 09/11/2026: es la vía PRINCIPAL («Hola Jarvis» por Whisper desde el 21/11); «Hey Jarvis» está apagado.
# Si el nombre falla, la salida es un modelo propio de openWakeWord, no más variantes.
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
# [Nau] 21/11/2026: la ÚNICA forma de despertarlo es «Hola Jarvis» (pedido por Nau; del 09/11
# al 21/11 fue «¿Qué pasa, Jarvis?»). «Ola» es como Whisper escribe a veces «hola».
CALL_WORDS = {"hola", "ola"}
# [Nau] 26/11/2026: cómo escribe Whisper «hola» cuando Nau habla seguido («Por la Jarvis,
# ábreme…», medido) o lo pega al nombre; solo valen justo delante del nombre y al principio
CALL_LIKE_WORDS = CALL_WORDS | {"olla", "holla", "jola", "bola", "cola", "pola", "mola", "gola",
                                "la", "olaa", "holaa", "hello", "holi"}
CALL_SPLIT_LEADS = {"por", "o", "a"}  # «por la» / «o la» / «a la» = «hola» partido en dos
# «Hola Javier», «Hola Travis»: si la frase es SOLO «hola» + nombre, el nombre puede parecerse menos
SHORT_CALL_NAME_SIMILARITY = 0.6
# Segunda opinión: si «base» oye algo como «Jarvis» al principio pero no la llamada entera,
# se vuelve a transcribir ese trozo con «small» antes de darlo por perdido
WAKE_RECHECK_MAX_S = 6.5
# [Nau] 26/11/2026: reenganche con el hilo abierto (rejoin_by_name); frases más largas no cuentan.
# 25 → 15 la misma noche: «…voy a mejorar este momento de Jarvis y aquí estoy con Jarvis
# todavía» (hablando DE él) lo despertó. Ahora el nombre va primero (o tras REJOIN_LEADS) o
# último (salvo cortesías de REJOIN_TAIL), y no cuenta detrás de una preposición.
REJOIN_MAX_WORDS = 15
REJOIN_LEADS = {"oye", "vale", "eh", "ey", "hey", "bueno", "venga", "pues", "y", "oiga"}
REJOIN_TAIL = {"por", "favor", "porfa", "porfavor", "gracias"}
# Órdenes de una palabra que, con su nombre, no son una simple llamada («Jarvis, pausa»)
SHORT_ORDERS = {"pausa", "pausar", "para", "stop", "siguiente", "anterior", "musica", "play",
                "continua", "sigue", "silencio", "calla", "callate", "volumen", "bloquea",
                "bloquear", "sube", "baja"}
NAME_PREPOSITIONS = {"de", "del", "con", "a", "al", "para", "sobre", "en", "por", "sin", "este",
                     "ese", "el", "mi", "tu", "su", "que"}
# Lo que puede ir antes del «hola» sin que deje de ser la llamada («Oye, hola Jarvis»)
LEAD_WORDS = {"oye", "hey", "ey", "eh", "vale", "bueno", "pues", "venga", "oiga", "y", "a",
              "no", "buenas"}
# «Hey Jarvis» (openWakeWord) desactivado el 09/11/2026: Nau quiere despertarlo solo con su frase
WAKE_BY_HEY_JARVIS = False
# Frases que solo comprueban si está («Jarvis, ¿estás por ahí?»): se contestan con el saludo fijo
PRESENCE_CHECK = re.compile(
    r"(?:(?:que|y) )?(?:que pasa|que tal|despierta|"
    r"(?:estas|esta|estais|estan) (?:por ahi|ahi|despierto|despiertos|disponible)|"
    r"me (?:oyes|escuchas|oye|escucha))"
)
# [Nau] 24/11/2026: saludo según la hora, pedido por Nau (del 09/11 al 24/11 fue «Dime, Nau.»).
# Días de 6:00 a 12:59, tardes de 13:00 a 20:59 y noches el resto.
GREETING_TEMPLATE = "{saludo}, señor. ¿En qué puedo ayudarle?"


def call_reply(now: datetime | None = None) -> str:
    """Saludo a «Hola Jarvis» a secas, según la hora del día."""
    hour = (now or datetime.now()).hour
    if 6 <= hour < 13:
        saludo = "Buenos días"
    elif 13 <= hour < 21:
        saludo = "Buenas tardes"
    else:
        saludo = "Buenas noches"
    return GREETING_TEMPLATE.format(saludo=saludo)
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


def transcribe(pcm: np.ndarray, model: str = "") -> str:
    """model: '' = el rápido (base, para despertar); CONVERSATION_STT_MODEL para lo que se le pide."""
    boundary = uuid.uuid4().hex
    fields = {"language": "es", **({"model": model} if model else {})}
    parts = [
        *(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
          for k, v in fields.items()),
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="voz.wav"\r\nContent-Type: audio/wav\r\n\r\n'
        ).encode()
        + to_wav(pcm)
        + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    payload = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    try:
        body, _ = http("/v1/speech/transcribe", payload, content_type)
    except (urllib.error.URLError, OSError) as exc:
        # [Nau] 26/11/2026: un reintento si la API falla un momento (500 o conexión cortada)
        log.warning("La transcripción ha fallado (%s); reintento", exc)
        time.sleep(TRANSCRIBE_RETRY_S)
        body, _ = http("/v1/speech/transcribe", payload, content_type)
    return drop_repetition(str(json.loads(body).get("text", "")).strip())


def drop_repetition(text: str) -> str:
    """[Nau] Quita los bucles de Whisper («no, no, no…» ×200); '' si la frase era solo eso.
    Por privacidad solo se registra cuántas palabras se han quitado."""
    words = text.split()
    keys = [_plain(w.strip(".,;:!?¡¿…\"'")) for w in words]
    keep = [True] * len(words)
    for size in range(1, REPEAT_PHRASE_MAX_WORDS + 1):
        limit = REPEAT_WORD_MAX if size == 1 else REPEAT_PHRASE_MAX
        i = 0
        while i + size <= len(keys):
            gram = keys[i:i + size]
            reps = 1
            while keys[i + reps * size:i + (reps + 1) * size] == gram:
                reps += 1
            if reps > limit and any(gram):
                for j in range(i + size, i + reps * size):
                    keep[j] = False
                i += reps * size
            else:
                i += 1
    removed = keep.count(False)
    if not removed:
        return text
    if removed / len(words) >= REPEAT_NOISE_RATIO:
        log.info("Transcripción repetitiva descartada (%d palabras): es ruido", len(words))
        return ""
    log.info("Transcripción repetitiva: quito %d de %d palabras", removed, len(words))
    return " ".join(w for w, k in zip(words, keep) if k)


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
            "«Enseguida, señor.»; no digas que no puedes ni que ya estaba hecho (hazlo otra "
            "vez: el usuario puede haberlo cerrado o borrado). Habla siempre en primera "
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
_aec = EchoCanceller()


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
        frame = _aec.clean(read_frame(stream))  # [Nau] sin el eco de lo que está sonando
        recent.append(frame)
        scan.append(frame)
        since_scan += 1
        rms = frame_rms(frame)
        window = envelope[max(0, index - ECHO_WINDOW_FRAMES):index + 1]
        played = max(float(window.max()) if window.size else 0.0, _echo.tail())
        index += 1
        threshold = _echo.threshold(played, base)
        loud_room = _echo.gain >= ECHO_STRONG_GAIN  # [Nau] altavoz: su voz vuelve fuerte al micro
        votes.append(rms > threshold)
        echoing.append(loud_room or threshold > base)
        if loud_room or not votes[-1]:
            _echo.learn(played, rms)
        if escriba_dictando():
            continue
        # [Nau] Con cascos el eco no cuenta (umbral = el del ruido): un «para» de ~0,25 s basta
        needed = BARGE_IN_FRAMES if any(echoing) else BARGE_IN_FRAMES_QUIET
        if not loud_room and sum(votes) >= needed:
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
    _aec.start(path)
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


def say_greeting(stream: Microphone, text: str) -> list[np.ndarray] | None:
    """Cada saludo se sintetiza una vez y se guarda: suena al instante y no gasta saldo."""
    cached = ROOT / "data" / f"saludo-{hashlib.sha1(text.encode()).hexdigest()[:8]}.wav"
    try:
        if not cached.exists():
            cached.write_bytes(synthesize(text))
        return play_file(cached, stream, said=text)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError):
        log.exception("No se pudo reproducir el saludo guardado")
        return say(text, stream)


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


class EarlyTranscript:
    """[Nau] 26/11/2026: transcripción empezada en la primera pausa corta (EARLY_STT_SILENCE_S),
    antes de dar la frase por acabada. Si el usuario vuelve a hablar se descarta; si no, cuando
    se confirma el final ya lleva ese tiempo adelantado («small» tarda ~1,3 s, medido)."""

    def __init__(self, pcm: np.ndarray) -> None:
        self.text: str | None = None
        self._done = threading.Event()
        threading.Thread(target=self._run, args=(pcm,), daemon=True, name="stt-anticipado").start()

    def _run(self, pcm: np.ndarray) -> None:
        try:
            self.text = transcribe(pcm, CONVERSATION_STT_MODEL)
        except (urllib.error.URLError, OSError, KeyError, ValueError):
            log.exception("Falló la transcripción anticipada")
        finally:
            self._done.set()

    def running(self) -> bool:
        return not self._done.is_set()

    def result(self) -> str | None:
        self._done.wait(HTTP_TIMEOUT_S)
        return self.text


_early_in_flight: EarlyTranscript | None = None  # la última anticipada (para no solapar dos)


def record_utterance(
    stream: Microphone, no_speech_s: float, prefix: list[np.ndarray] | None = None,
    floor: float | None = None, early: list[EarlyTranscript] | None = None,
) -> tuple[np.ndarray | None, float]:
    """Graba hasta que hay silencio tras hablar. Devuelve (audio o None si nadie habla, suelo de ruido).

    prefix: audio en el que ya se ha detectado voz (El usuario ha seguido hablando).
    early: si se pasa una lista, se deja en ella la transcripción anticipada que siga valiendo.
    """
    global _early_in_flight
    frames: list[np.ndarray] = list(prefix or [])
    frame_s = FRAME / SAMPLE_RATE
    speech_run = 0
    heard = bool(prefix)
    silent_s = 0.0
    elapsed = 0.0
    pending: EarlyTranscript | None = None
    pending_at = 0
    quiet_s = 0.0
    # [Nau] 27/11/2026: el tope de MAX_UTTERANCE_S cuenta desde que empieza a hablar. Antes
    # contaba desde el principio y cortaba la espera a los 20 s: FOLLOW_UP_S de 40 s no hacía nada
    speaking_s = 0.0
    while not heard or speaking_s < MAX_UTTERANCE_S:
        frame = read_frame(stream)
        frames.append(frame)
        elapsed += frame_s
        if heard:
            speaking_s += frame_s
        elif len(frames) > SEGMENT_PREROLL_FRAMES + MIN_SPEECH_FRAMES:
            del frames[0]  # mientras espera, solo se guarda el audio justo antes de la voz
        rms = frame_rms(frame)
        publish_level(rms)
        if rms > speech_threshold(floor):
            speech_run += 1
            heard = heard or speech_run >= MIN_SPEECH_FRAMES
            silent_s = 0.0
            pending = None  # sigue hablando: la anticipada ya no vale
        else:
            speech_run = 0
            silent_s += frame_s
        floor = update_floor(floor, rms)
        # Silencio de verdad (no la cola suave de una palabra): por debajo del mismo nivel con el
        # que luego se comprueba que no ha seguido hablando
        quiet_s = quiet_s + frame_s if rms <= EARLY_STT_TAIL_RATIO * speech_threshold(floor) else 0.0
        if (early is not None and heard and pending is None and quiet_s >= EARLY_STT_SILENCE_S
                and not (_early_in_flight and _early_in_flight.running())):
            pending = _early_in_flight = EarlyTranscript(np.concatenate(frames))
            pending_at = len(frames)
        # Una sílaba final suave («…por la tarde») queda bajo el umbral de voz pero no está en
        # la anticipada: si después suena algo más que el ruido de fondo, no vale
        if pending is not None and len(frames) > pending_at and rms > EARLY_STT_TAIL_RATIO * speech_threshold(floor):
            pending = None
        if heard and silent_s >= SILENCE_S:
            break
        if not heard and elapsed >= no_speech_s:
            return None, floor
    if early is not None and heard and pending is not None:
        early.append(pending)
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
            if not re.search(r"\w", chunk):
                continue  # [Nau] 27/11/2026: el cerebro a veces cierra con «∎»; no se dice
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
    if len(words) > GOODBYE_MAX_WORDS:  # [Nau] 25/11/2026: frase larga, no es una despedida
        return False
    if "gracias" in words and all(w in THANKS_ONLY for w in words):
        return True
    if len(words) <= GOODBYE_START_MAX_WORDS and GOODBYE_START.search(" ".join(words)):
        return True
    # [Nau] 26/11/2026: «Adiós, ya lo vi.» no cerraba; con «adiós» delante basta una frase corta
    if words and words[0] in GOODBYE_WORDS and len(words) <= GOODBYE_ADIOS_MAX_WORDS:
        return True
    # «…por cierto, adiós, YAMI»: «adiós» + el nombre mal oído al final de una frase corta
    if 2 <= len(words) <= GOODBYE_NAME_TAIL_MAX_WORDS and words[-2] in GOODBYE_WORDS:
        return True
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

    def __init__(self, pcm: np.ndarray, floor: float, early: EarlyTranscript | None = None) -> None:
        self.pcm = pcm
        self.floor = floor
        self.heard_at = time.monotonic()  # el usuario acaba de callar: aquí empieza la espera
        text = early.result() if early is not None else None
        self.text = text if text is not None else transcribe(pcm, CONVERSATION_STT_MODEL)
        self.transcribed_s = time.monotonic() - self.heard_at
        self.continuations = 0  # veces que siguió hablando tras una pausa

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
            # [Nau] 27/11/2026: la espera se cuenta desde que calla DE VERDAD (la última vez);
            # antes contaba desde la primera pausa y el log daba «23 s» que eran él hablando
            self.heard_at = time.monotonic()
            # [Nau] 26/11/2026: con música, la frase entera puede salir en bucle (se descarta
            # como ruido): entonces se queda lo que ya se había entendido
            self.text = transcribe(self.pcm, CONVERSATION_STT_MODEL) or self.text
            self.transcribed_s = time.monotonic() - self.heard_at
            self.continuations += 1


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
    _addressee.replied_at = time.monotonic()
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


class _Addressee:
    """[Nau] 26/11/2026: ¿va dirigido a Jarvis lo que oye en la conversación?
    pending: frase sin su nombre por la que ha preguntado «¿Es a mí, señor?».
    ignore_others: Nau ha dicho que no; no pregunta más hasta que acabe la tarea o la charla."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pending: str | None = None
        self.ignore_others = False
        self.asked_at = 0.0  # cuándo preguntó «¿Es a mí, señor?» por última vez
        self.replied_at = 0.0  # cuándo acabó de contestarle (respuesta o aviso de tarea)

    def follows_reply(self, spoke_at: float | None) -> bool:
        """[Nau] 26/11/2026: si empieza a hablar poco después de que Jarvis le conteste, es la
        charla con él (preguntaba «¿Es a mí?» antes de cada orden sin nombre)."""
        # Sin límite por abajo: la grabación puede empezar antes de que acabe de cerrar su turno
        # (tras «Enseguida, señor.» espera a que termine la acción), y eso también es seguirle
        return (spoke_at is not None and self.replied_at > 0
                and spoke_at - self.replied_at <= ADDRESS_FOLLOW_S)

    def asked_recently(self) -> bool:
        """[Nau] 26/11/2026: con música de fondo preguntaba dos veces en 4 s."""
        return time.monotonic() - self.asked_at < ADDRESS_QUIET_S


_addressee = _Addressee()


def names_jarvis(text: str) -> bool:
    return any(is_jarvis(w) for w in re.findall(r"[\w']+", text))


def addresses_jarvis(text: str) -> bool:
    """[Nau] 26/11/2026: ¿le habla A él? Su nombre detrás de una preposición («el código de
    Jarvis», «hablaba con Jarvis») es hablar DE él: eso se pregunta con «¿Es a mí, señor?»."""
    words = re.findall(r"[\w']+", text)
    return any(is_jarvis(w) and (i == 0 or _plain(words[i - 1]) not in NAME_PREPOSITIONS)
               for i, w in enumerate(words))


def speak_short(stream: Microphone, text: str) -> Heard:
    """Frase fija («¿Es a mí, señor?»): se guarda la primera vez y suena al instante."""
    log.info("Jarvis: %s", text)
    publish("speaking", text)
    return done_speaking(stream, say_greeting(stream, text))


def screen_addressee(stream: Microphone, messages: list[dict], text: str,
                     interrupted: bool, spoke_at: float | None = None) -> tuple[str | None, Heard]:
    """Devuelve la frase a la que contestar, o None si ha preguntado o no era para él.

    Va dirigido a Jarvis si lleva su nombre, si le interrumpe mientras habla o si Jarvis
    acaba de hacerle una pregunta. Si no, pregunta «¿Es a mí, señor?»: con un no, sigue a lo
    suyo; con un sí, contesta a la frase que había oído."""
    words = [_plain(w) for w in re.findall(r"[\w']+", text) if not is_jarvis(w)]
    if _addressee.pending is not None:
        pending, _addressee.pending = _addressee.pending, None
        content = [w for w in words if w not in ADDRESS_REPLY_FILLER]
        if words and NO_REPLY.match(" ".join(words)) and len(content) < 2:
            _addressee.ignore_others = True
            log.info("No era para mí: sigo a lo mío y no pregunto más hasta que acabe")
            return None, speak_short(stream, ADDRESS_NO_REPLY)
        if words and words[0] in YES_WORDS:
            log.info("Sí era para mí")
            _addressee.asked_at = 0.0  # confirmado: la siguiente frase sin nombre se vuelve a preguntar
            return (f"{pending} {text}" if len(content) >= 2 else pending), None
        # [Nau] 26/11/2026: lo que contesta justo después de la pregunta va para él aunque no
        # sea «sí» (repitió «Pon música» y se ignoraba durante 20 s)
        if time.monotonic() - _addressee.asked_at <= ADDRESS_ANSWER_S:
            log.info("Me contesta a «¿Es a mí?»: era para mí")
            _addressee.asked_at = 0.0
            return text, None
        # Fuera de ese rato: es una frase nueva y se mira como cualquier otra
    if addresses_jarvis(text) or interrupted or _last_said(messages).rstrip().endswith("?"):
        return text, None
    if _addressee.follows_reply(spoke_at) and not _addressee.ignore_others:
        log.info("Me habla justo después de mi respuesta: es para mí")
        return text, None
    if _addressee.ignore_others:
        # Privacidad: no era para él, no se registra el texto
        log.info("Frase sin mi nombre (%d palabras): ya me dijo que no era para mí", len(words))
        return None, None
    if _addressee.asked_recently():
        log.info("Frase sin mi nombre (%d palabras): acabo de preguntar, no repito", len(words))
        return None, None
    _addressee.pending = text
    _addressee.asked_at = time.monotonic()
    log.info("Frase sin mi nombre (%d palabras): pregunto si es para mí", len(words))
    return None, speak_short(stream, ADDRESS_QUESTION)


def respond(stream: Microphone, messages: list[dict], model: str, turn: Turn,
            interrupted: bool = False) -> tuple[bool, Heard]:
    """Responde cuando el usuario ha terminado de hablar. False si se despide o no dice nada útil."""
    resumed = None
    answer, first, recalled = None, None, None
    screened = False
    publish("thinking", "")
    for _ in range(MAX_REASKS + 1):
        turn.keep_listening(stream, resumed)
        publish("thinking", turn.text)
        if not useful(turn.text):
            log.info("Ignorado (sin texto útil): %r", turn.text)
            # [Nau] 26/11/2026: un «¡Suscríbete!» inventado por Whisper cerraba la charla;
            # ahora sigue escuchando, salvo si solo llega ruido varias veces seguidas
            _end.junk += 1
            return _end.junk <= JUNK_TURNS_MAX, None
        _end.junk = 0
        if interrupted and is_own_echo(turn.text, _last_said(messages)):
            log.info("Lo que me interrumpió era mi propio eco (%r): no contesto y sigo escuchando", turn.text)
            _echo.missed()
            publish("listening", "")
            return True, None
        if interrupted and is_only_stop(turn.text):
            log.info("%s: %s (solo me pide que pare: me callo y escucho)", USUARIO, turn.text)
            publish("listening", "")
            return True, None
        was_interrupted, interrupted = interrupted, False
        if is_goodbye(turn.text):
            _end.goodbye = True
            log.info("%s: %s (fin de la conversación)", USUARIO, turn.text)
            publish("speaking", GOODBYE_REPLY)
            say(GOODBYE_REPLY)
            flush(stream)
            return False, None
        # [Nau] 26/11/2026: solo la primera vez; si siguió hablando, la frase ya se aceptó
        # (al volver a mirarla sin la pregunta pendiente se perdía «Sí… pon música»)
        if not screened:
            screened = True
            spoke_at = turn.heard_at - len(turn.pcm) / SAMPLE_RATE  # cuándo empezó a hablar
            question, heard = screen_addressee(stream, messages, turn.text, was_interrupted,
                                               spoke_at)
            if question is None:  # ha preguntado «¿Es a mí, señor?» o no era para él
                publish("listening", "")
                return True, heard
            turn.text = question
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
    log.info("Tiempos: transcripción %.2f s, primera frase lista a los %.2f s%s",
             turn.transcribed_s, latency,
             f" (siguió hablando {turn.continuations} veces)" if turn.continuations else "")
    heard = deliver(stream, messages, turn.text, answer, first, recalled, latency, turn.floor)
    return True, heard


def handle_turn(stream: Microphone, messages: list[dict], model: str, wait_s: float,
                prefix: Heard = None) -> tuple[bool, Heard]:
    """Graba lo que dice el usuario y lo responde. False si el usuario calla o se despide.

    prefix: lo que el usuario dijo al interrumpir a Jarvis (su turno empieza ahí)."""
    early: list[EarlyTranscript] = []
    pcm, floor = record_utterance(stream, wait_s, prefix, early=early)
    if pcm is None:
        return False, None
    if escriba_dictando():
        log.info("El usuario está dictando con Escriba: cierro la conversación sin responder")
        return False, None
    turn = Turn(pcm, floor, early[0] if early else None)
    return respond(stream, messages, model, turn, interrupted=prefix is not None)


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


def expire_stale_tasks() -> None:
    """[Nau] 26/11/2026: una tarea «en curso» de hace más de TASK_STALE_S se da por perdida (su
    proceso murió; una llevaba un día así). Se marca como anunciada para no leerla de golpe."""
    now = time.time()
    for path in TASKS_DIR.glob("*.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (task.get("estado") not in ("pendiente", "en_curso")
                or now - float(task.get("creada") or 0) < TASK_STALE_S):
            continue
        task.update(estado="error", anunciado=True, terminada=now,
                    resultado="Se quedó colgada y la di por perdida.")
        tmp = path.with_suffix(".caducada")
        try:
            tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            log.info("Tarea %s colgada desde hace más de %d min: la doy por perdida",
                     task.get("id"), TASK_STALE_S // 60)
        except OSError:
            log.exception("No se pudo cerrar la tarea colgada %s", path.name)


def task_announcement(task: dict) -> tuple[str, dict]:
    """Lo que Jarvis dice al acabar una tarea y el contexto para seguir hablando de ella."""
    result = str(task.get("resultado") or "").strip()
    fact = result.rstrip(".")  # para citarlo dentro de otra frase
    session = task.get("session_id") or ""
    order = task.get("tarea", "")
    # [Nau] 26/11/2026: «Ya está, señor.» en vez de «Nau, ya está.», como el resto de frases fijas
    if task.get("estado") == "error":
        text = f"Disculpa, señor, no he podido terminar lo que me pediste. {result}"
        note = f"La tarea «{order}» ha fallado: {fact}."
    elif task.get("confirmar"):
        text = f"Antes de seguir necesito tu permiso, señor. {result} ¿Lo hago?"
        note = (f"Tu tarea en segundo plano «{order}» espera el permiso de {USUARIO}. Lo que harás: "
                f"{fact}. Si {USUARIO} dice que sí, vuelve a encargarla con "
                f"sesion=\"{session}\", confirmado=true y tarea=\"Sí, adelante, hazlo.\". "
                "Si dice que no o duda, no la llames y díselo.")
    else:
        text = f"Ya está, señor. {result}"
        note = f"Has terminado en segundo plano la tarea «{order}»: {fact}."
    if session:
        note += (f" Si {USUARIO} quiere cambiar o seguir con esa tarea, vuelve a encargarla "
                 f"con sesion=\"{session}\".")
    return text, {"role": "system", "content": note}


class _EndReason:
    """[Nau] Por qué acabó el último turno: si fue una despedida, no se espera a las tareas.
    junk: turnos seguidos que solo traían ruido o frases inventadas por Whisper."""
    goodbye = False
    junk = 0


_end = _EndReason()


def tasks_in_progress() -> bool:
    """¿Hay alguna tarea encargada hace poco que aún no ha terminado?"""
    now = time.time()
    for path in TASKS_DIR.glob("*.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (task.get("estado") in ("pendiente", "en_curso")
                and now - float(task.get("creada") or 0) < TASK_WAIT_MAX_S):
            return True
    return False


def wait_for_tasks(stream: Microphone, messages: list[dict], model: str) -> tuple[bool, Heard]:
    """[Nau] 26/11/2026: con una tarea en marcha la conversación no se cierra por silencio
    (antes se cerraba a los 20 s y el resultado llegaba suelto). Se sigue escuchando a ratos
    cortos; si habla, se le contesta; si la tarea acaba, se cuenta y la charla sigue."""
    log.info("Tarea en marcha: mantengo la conversación abierta")
    publish("listening", "")
    started = time.monotonic()
    told_progress = False
    while tasks_in_progress() and time.monotonic() - started < TASK_WAIT_MAX_S:
        _end.goodbye = False
        keep_going, heard = handle_turn(stream, messages, model, TASK_POLL_S, None)
        if keep_going or _end.goodbye:
            return keep_going, heard
        done = finished_tasks()
        if done:
            return True, announce_tasks(stream, messages, done)
        if not told_progress and time.monotonic() - started >= TASK_PROGRESS_AFTER_S:
            told_progress = True
            log.info("Jarvis: %s", TASK_PROGRESS_REPLY)
            publish("speaking", TASK_PROGRESS_REPLY)
            heard = done_speaking(stream, say(TASK_PROGRESS_REPLY, stream))
            if heard is not None:
                return True, heard
    done = finished_tasks()  # justo acaba al salir del bucle
    return (True, announce_tasks(stream, messages, done)) if done else (False, None)


def announce_tasks(stream: Microphone, messages: list[dict], tasks: list[dict]) -> Heard:
    """Cuenta al usuario cómo han acabado las tareas que encargó a Claude Code."""
    heard = None
    for task in tasks:
        _addressee.ignore_others = False  # acabó la tarea: vuelve a preguntar si no lleva su nombre
        text, note = task_announcement(task)
        log.info("Jarvis (tarea %s): %s", task.get("id"), text)
        messages.extend([note, {"role": "assistant", "content": text}])
        BRAIN.note(f"{note['content']} Ya se lo has dicho a {USUARIO} así: «{text}»")
        publish("speaking", text)
        heard = done_speaking(stream, say(text, stream))
        _addressee.replied_at = time.monotonic()
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
    _addressee.reset()
    _end.junk = 0
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
            greeting = call_reply()
            log.info("Jarvis: %s", greeting)
            messages.append({"role": "assistant", "content": greeting})
            publish("speaking", greeting)
            heard = done_speaking(stream, say_greeting(stream, greeting))
        while True:
            if heard is None:
                heard = announce_tasks(stream, messages, finished_tasks())
            _end.goodbye = False
            keep_going, heard = handle_turn(stream, messages, model, FOLLOW_UP_S, heard)
            if not keep_going and not _end.goodbye and tasks_in_progress():
                keep_going, heard = wait_for_tasks(stream, messages, model)
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
    """[Nau] 21/11/2026: solo despierta con «Hola Jarvis», al principio de la frase:
    «Oye, hola Jarvis» sí; «¿Qué pasa, Jarvis?», «Jarvis, mira…» o hablar de él a mitad
    de frase, no."""
    words = list(re.finditer(r"[\w']+", text))
    short_call = len(words) <= 3  # solo «(oye) hola Jarvis»: el nombre puede parecerse menos
    for i, match in enumerate(words):
        name = _plain(match.group())
        # «Holajarvis» / «Olajarvis»: Whisper lo junta todo
        if any(name.startswith(c) and is_jarvis(name[len(c):]) for c in ("hola", "ola")):
            first = i
        elif i >= 1 and _plain(words[i - 1].group()) in CALL_LIKE_WORDS and (
                is_jarvis(match.group()) or (short_call and _sounds_like_jarvis(name))):
            first = i - 1
            # «Por la Jarvis»: «hola» partido en dos palabras
            if (_plain(words[first].group()) == "la" and first >= 1
                    and _plain(words[first - 1].group()) in CALL_SPLIT_LEADS):
                first -= 1
        else:
            continue
        start = max((m.end() for m in re.finditer(r"[.?!¿¡…;]", text[:words[first].start()])), default=0)
        before = re.findall(r"[\w']+", text[start:words[first].start()])
        if all(_plain(w) in LEAD_WORDS for w in before):
            return match
    return None


def _sounds_like_jarvis(word: str) -> bool:
    """«Javier», «Travis», «Jarbas»: vale solo en una llamada corta («Hola Javier.»)."""
    return (4 <= len(word) <= 7
            and difflib.SequenceMatcher(None, word, "jarvis").ratio() >= SHORT_CALL_NAME_SIMILARITY)


DIAGNOSTIC_FILE = ROOT / "data" / "diagnostico-voz.txt"
DIAGNOSTIC_MAX_S = 10 * 60
DIAGNOSTIC_DIR = ROOT / "data" / "diagnostico-voz"  # audios del diagnóstico (se borran al terminar)


def diagnostic_mode() -> bool:
    """[Nau] ¿Hay un diagnóstico de voz pedido por Nau y reciente? Caduca solo a los 10 min
    (y se borra el archivo), para que el registro vuelva a no guardar lo que se oye."""
    try:
        age = time.time() - DIAGNOSTIC_FILE.stat().st_mtime
    except OSError:
        return False
    if age <= DIAGNOSTIC_MAX_S:
        return True
    DIAGNOSTIC_FILE.unlink(missing_ok=True)
    shutil.rmtree(DIAGNOSTIC_DIR, ignore_errors=True)
    log.info("Diagnóstico de voz terminado: vuelvo a no registrar lo que oigo")
    return False


def called(text: str) -> str | None:
    """¿Le llaman? «Hola Jarvis» siempre; justo después de una conversación, también su
    nombre al principio o al final de la frase (rejoin_by_name)."""
    question = wake_by_name(text)
    if question is None and _thread.active() and not is_goodbye(text):
        # [Nau] 26/11/2026: «Adiós, Jarvis» dicho tarde (ya dormido) no lo vuelve a despertar
        question = rejoin_by_name(text)
    return question


def worth_second_opinion(text: str, segment: np.ndarray) -> bool:
    """[Nau] 26/11/2026: ¿parece una llamada mal transcrita? Frase corta que empieza por algo
    como «hola», o que tiene algo como «Jarvis» al principio, en un trozo no muy largo."""
    if len(segment) / SAMPLE_RATE > WAKE_RECHECK_MAX_S:
        return False
    words = [_plain(w) for w in re.findall(r"[\w']+", text)]
    starts_like_call = bool(words) and words[0] in CALL_LIKE_WORDS and len(words) <= 4
    # Con el hilo abierto también vale el nombre al final («…ponme música, Jarvis»); [Nau] 27/11:
    # solo si la frase cabe en un reenganche (más larga, rejoin_by_name nunca la acepta)
    ends_like_call = _thread.active() and 0 < len(words) <= REJOIN_MAX_WORDS and any(
        is_jarvis(w) or _sounds_like_jarvis(w) for w in words[-2:])
    return starts_like_call or ends_like_call or mentions_jarvis_early(text)


def mentions_jarvis_early(text: str) -> bool:
    """¿Hay algo parecido a «Jarvis» entre las 3 primeras palabras? (para la segunda opinión)"""
    words = [_plain(w) for w in re.findall(r"[\w']+", text)[:3]]
    return any(is_jarvis(w) or _sounds_like_jarvis(w) or w.endswith(("jarvis", "arvis"))
               for w in words)


def rejoin_by_name(text: str) -> str | None:
    """[Nau] 26/11/2026: poco después de una conversación (hilo abierto), basta con que la frase
    EMPIECE o ACABE con su nombre («Jarvis, ¿cuánto falta?», «…ponme música, Jarvis»), sin el
    «Hola»: Nau ahora le habla así y, si la charla ya se había cerrado, no despertaba."""
    words = list(re.finditer(r"[\w']+", text))
    if not words or len(words) > REJOIN_MAX_WORDS:
        return None
    if not (_name_opens(words) or _name_closes(words)):
        return None
    rest = " ".join(w.group() for w in words if not is_jarvis(w.group()))
    if _is_short_order(rest):
        return text.strip()
    if len(rest.split()) < 2 or PRESENCE_CHECK.fullmatch(_plain(rest)):
        return ""
    return text.strip()


def _is_short_order(rest: str) -> bool:
    """[Nau] 26/11/2026: «Pausa, Jarvis» es una orden, no solo una llamada (saludaba)."""
    return _plain(rest.strip(" ,.;:!?¡¿")) in SHORT_ORDERS


def _name_opens(words: list[re.Match]) -> bool:
    """«Jarvis, …» u «Oye, Jarvis, …»: el nombre abre la frase."""
    first = [w.group() for w in words[:2]]
    return is_jarvis(first[0]) or (len(first) == 2 and _plain(first[0]) in REJOIN_LEADS
                                   and is_jarvis(first[1]))


def _name_closes(words: list[re.Match]) -> bool:
    """«…ponme música, Jarvis (por favor)»: el nombre cierra la frase y no va tras una
    preposición («…aquí estoy con Jarvis» habla DE él, no le llama)."""
    plain = [_plain(w.group()) for w in words]
    while plain and plain[-1] in REJOIN_TAIL:
        plain.pop()
        words = words[:-1]
    if not words or not is_jarvis(words[-1].group()):
        return False
    return len(plain) < 2 or plain[-2] not in NAME_PREPOSITIONS


def wake_by_name(text: str) -> str | None:
    """Si la frase es «Hola Jarvis» devuelve lo que pide después ('' si solo le llama)."""
    hit = _wake_hit(text)
    if hit is None:
        return None
    rest = text[hit.end():].strip(" ,.;:!?¡¿")
    if _is_short_order(rest):
        return rest
    # «Hola Jarvis, ¿estás por ahí?»: solo le llama → saludo fijo
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

    def active(self) -> bool:
        """¿Ha habido conversación hace menos de CONTINUE_WINDOW_S?"""
        return self._expires is not None and time.monotonic() < self._expires

    def expire_if_old(self) -> None:
        if self._expires is not None and time.monotonic() >= self._expires:
            self._expires = None
            self._messages = []
            BRAIN.reset()
            log.info("Hilo de la conversación cerrado: la próxima empieza de cero")


_thread = ConversationThread()


def onset_ratio(segment: np.ndarray) -> float:
    """Volumen de los primeros 0,1 s respecto al de toda la frase. Con el preroll, una frase
    entera empieza en silencio (cerca de 0); cerca de 1 o más, el audio llegó ya empezado."""
    head = segment[: SAMPLE_RATE // 10].astype(np.float64)
    whole = segment.astype(np.float64)
    total = float(np.sqrt(np.mean(whole ** 2))) if whole.size else 0.0
    return float(np.sqrt(np.mean(head ** 2))) / total if total and head.size else 0.0


def listen(stream: Microphone, wake: Model, model_name: str) -> None:
    """Bucle principal: espera a oír su nombre y lleva la conversación."""
    segmenter = Segmenter()
    peak_rms = 0.0
    last_level_log = time.monotonic()
    last_sound = time.monotonic()
    deaf_until = 0.0  # [Nau] hasta cuándo se ignora lo oído por un dictado de Escriba
    last_task_check = time.monotonic()
    last_stale_check = 0.0  # la primera vuelta ya limpia las tareas colgadas
    missed: tuple[float, int, float] | None = None  # última frase corta que no le despertó
    log.info("Escuchando «Hola Jarvis» (cerebro Sonnet; reserva %s)", model_name)
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
            question = called(heard) if useful(heard) else None
            if question is None and useful(heard) and worth_second_opinion(heard, segment):
                second = transcribe(segment, CONVERSATION_STT_MODEL)
                question = called(second) if useful(second) else None
                if question is not None:  # va dirigido a él: se puede registrar
                    log.info("Segunda opinión: %r → %r", heard, second)
                    heard = second
                elif diagnostic_mode() or names_jarvis(heard) or names_jarvis(second):
                    # Lleva su nombre: le estaban llamando y no lo ha reconocido (se registra
                    # para poder arreglarlo; lo que no le nombra sigue sin guardarse)
                    log.info("Llamada no reconocida: %r / %r", heard, second)
                else:  # privacidad (08/11): sin el texto de lo que no era para él
                    log.info("Segunda opinión sin llamada (%d palabras)", len(second.split()))
            if question is not None:
                log.info("Activado por nombre: %r", heard)
                if missed is not None and time.monotonic() - missed[0] <= MISSED_CALL_WINDOW_S:
                    log.info("Posible llamada perdida: %d palabras hace %.0f s sin despertar "
                             "(arranque del audio %.2f; cerca de 1 = empezó cortado)",
                             missed[1], time.monotonic() - missed[0], missed[2])
                missed = None
            elif diagnostic_mode():
                # [Nau] 26/11/2026: SOLO con data\diagnostico-voz.txt (lo crea Claude a petición
                # de Nau, caduca a los 10 min): el texto, para ver por qué no despierta
                log.info("DIAGNÓSTICO oído: %r (pico %.3f)", heard, float(np.abs(segment).max()) / 32768)
                DIAGNOSTIC_DIR.mkdir(exist_ok=True)
                (DIAGNOSTIC_DIR / f"{time.strftime('%H%M%S')}.wav").write_bytes(to_wav(segment))
            else:
                # Solo métricas: lo que no va dirigido a Jarvis no se guarda (privacidad)
                log.info("Oído (sin «Jarvis»): %d palabras (pico %.3f)",
                         len(heard.split()), float(np.abs(segment).max()) / 32768)
                if len(heard.split()) <= MISSED_CALL_MAX_WORDS:
                    missed = (time.monotonic(), len(heard.split()), onset_ratio(segment))
        # [Nau] Tarea de Claude Code terminada: se avisa si nadie está hablando
        if question is None and not segmenter.frames and now - last_task_check >= TASK_CHECK_EVERY_S:
            last_task_check = now
            if now - last_stale_check >= STALE_CHECK_EVERY_S:
                last_stale_check = now
                expire_stale_tasks()
            tasks = finished_tasks()
        if question is None and not tasks:
            _thread.expire_if_old()
            continue

        # [Nau] 26/11/2026: el trabajador de tareas se enciende ya, para que si le pide algo
        # empiece al momento (se apaga solo tras 15 min sin tareas)
        try:
            ensure_worker()
        except OSError:
            log.exception("No arranca el trabajador de tareas")
        # [Nau] 26/11/2026: la música baja mientras conversa (se le oye y no se confunde)
        if duck_media():
            log.info("Bajo la música mientras hablamos")
        try:
            conversation(stream, model_name, question or "", tasks, _thread.messages())
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            log.exception("Fallo en la conversación: %s", exc)
            winsound.Beep(300, 300)
            flush(stream)
        finally:
            if restore_media():
                log.info("Música a su volumen")
        _thread.touch()
        wake.reset()
        segmenter = Segmenter()
        # La conversación lee el micro por su cuenta: sin esto, al acabar una de más de
        # 10 min se tomaba como «silencio digital» y se reabría el micro sin motivo
        last_sound = time.monotonic()


def warm_up_conversation_stt() -> None:
    """[Nau] 26/11/2026: carga «small» en la API de antemano (la primera vez tarda unos segundos)."""
    try:
        transcribe(np.zeros(SAMPLE_RATE, dtype=np.int16), CONVERSATION_STT_MODEL)
        log.info("Transcripción de conversación lista (Whisper %s)", CONVERSATION_STT_MODEL)
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        log.exception("No se pudo precargar Whisper %s", CONVERSATION_STT_MODEL)


def run() -> None:
    restore_media()  # si el oyente se cayó en plena charla, la música no se queda baja
    wait_for_api()
    threading.Thread(target=warm_up_conversation_stt, daemon=True, name="stt-small").start()
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
    log.info("Oyente iniciado (cancelación de eco %s)", "activa" if _aec.available else "NO disponible")
    # Siempre encendido: cualquier fallo inesperado se registra y el oyente vuelve a empezar.
    while True:
        try:
            run()
        except Exception:
            log.exception("Fallo inesperado en el oyente; vuelvo a empezar en %ds", RESTART_DELAY_S)
            time.sleep(RESTART_DELAY_S)


if __name__ == "__main__":
    main()
