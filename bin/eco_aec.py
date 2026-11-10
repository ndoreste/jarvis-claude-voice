"""[Nau] 10/11/2026: cancelación de eco de verdad (AEC3 de WebRTC, vía livekit) mientras Jarvis habla.

Con la voz por un altavoz, lo que dice Jarvis vuelve al micro con ~0,41 s de retraso y a
casi la mitad de volumen (medido con el BS9655 y el micro USB): tan fuerte como un «para»
de Nau, así que ni el volumen ni Whisper lo distinguían. AEC3 resta del micro lo que está
sonando y deja la voz del usuario: en la prueba, el eco bajó de 0,034 a 0,005 (-17 dB).

Si livekit no está instalado (p. ej. tras un `uv sync`), `available` es False y el oyente
sigue como antes (solo «para» corta con altavoz). Para quitarlo: `uv pip uninstall livekit`.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path

import numpy as np

from microfono import FRAME, SAMPLE_RATE

log = logging.getLogger("escucha")

CHUNK = SAMPLE_RATE // 100  # AEC3 trabaja en trozos de 10 ms

try:
    from livekit import rtc
except ImportError:  # sin livekit: sin cancelación de eco
    rtc = None


def _load_far(path: Path) -> np.ndarray:
    """El WAV que va a sonar, en mono int16 a 16 kHz (la referencia del eco)."""
    try:
        with wave.open(str(path), "rb") as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            raw = wav.readframes(wav.getnframes())
    except (OSError, wave.Error, EOFError):
        return np.zeros(0, np.int16)
    if width != 2:
        return np.zeros(0, np.int16)
    pcm = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        positions = np.arange(0, len(pcm), rate / SAMPLE_RATE)
        pcm = np.interp(positions, np.arange(len(pcm)), pcm)
    return pcm.astype(np.int16)


class EchoCanceller:
    """Un solo AEC3 para todo el oyente: lo aprendido de la habitación se conserva entre frases."""

    def __init__(self) -> None:
        self._apm = None
        if rtc is not None:
            try:
                self._apm = rtc.AudioProcessingModule(echo_cancellation=True, high_pass_filter=True)
            except Exception as exc:  # noqa: BLE001 - librería nativa: cualquier fallo = sin AEC
                log.warning("Cancelación de eco no disponible: %s", exc)
        self._far = np.zeros(0, np.int16)
        self._pos = 0

    @property
    def available(self) -> bool:
        return self._apm is not None

    def start(self, path: Path) -> None:
        """Empieza a sonar `path`: a partir de aquí cada trozo del micro se limpia con él."""
        self._far = _load_far(path)
        self._pos = 0

    def clean(self, frame: np.ndarray) -> np.ndarray:
        """Quita del trozo de micro (80 ms) el eco de lo que está sonando."""
        if self._apm is None or len(frame) != FRAME:
            return frame
        try:
            out = np.empty_like(frame)
            for i in range(0, FRAME, CHUNK):
                far = self._far[self._pos:self._pos + CHUNK]
                if len(far) < CHUNK:
                    far = np.concatenate([far, np.zeros(CHUNK - len(far), np.int16)])
                self._pos += CHUNK
                self._apm.process_reverse_stream(rtc.AudioFrame(far.tobytes(), SAMPLE_RATE, 1, CHUNK))
                near = rtc.AudioFrame(frame[i:i + CHUNK].tobytes(), SAMPLE_RATE, 1, CHUNK)
                self._apm.process_stream(near)
                out[i:i + CHUNK] = np.frombuffer(bytes(near.data), dtype=np.int16)
            return out
        except Exception as exc:  # noqa: BLE001 - si la AEC falla, mejor el audio sin limpiar
            log.warning("Cancelación de eco desactivada por un error: %s", exc)
            self._apm = None
            return frame
