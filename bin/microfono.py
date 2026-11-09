"""Micrófono de Jarvis con cola propia.

PortAudio solo guarda unos 0,3 s de audio entre lecturas: lo que el usuario dice mientras
Jarvis transcribe o piensa se perdía. Aquí el audio entra por callback a una cola
(hasta MAX_QUEUE_S), así se puede comprobar si el usuario ha seguido hablando.
Si el micro deja de entregar audio (p. ej. al volver de suspensión), read_frame
lanza MicUnavailable para que el oyente lo vuelva a abrir.
"""

from __future__ import annotations

import queue

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, el tamaño que espera openWakeWord
MAX_QUEUE_S = 60
READ_TIMEOUT_S = 5.0


class MicUnavailable(RuntimeError):
    """El micrófono no entrega audio."""


class Microphone:
    def __init__(self) -> None:
        self._queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=int(MAX_QUEUE_S * SAMPLE_RATE / FRAME)
        )
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME,
            callback=self._on_audio,
        )

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        frame = indata[:, 0].copy()
        try:
            self._queue.put_nowait(frame)
        except queue.Full:  # nadie lee: se descarta lo más antiguo
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(frame)

    def __enter__(self) -> "Microphone":
        self._stream.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stream.stop()
        self._stream.close()

    @property
    def pending_frames(self) -> int:
        """Tramos de 80 ms grabados que aún no se han leído."""
        return self._queue.qsize()

    def read_frame(self) -> np.ndarray:
        try:
            return self._queue.get(timeout=READ_TIMEOUT_S)
        except queue.Empty as exc:
            raise MicUnavailable(f"el micro no entrega audio desde hace {READ_TIMEOUT_S:.0f}s") from exc

    def drain(self) -> list[np.ndarray]:
        """Devuelve (y vacía) todo el audio pendiente."""
        frames = []
        while True:
            try:
                frames.append(self._queue.get_nowait())
            except queue.Empty:
                return frames
