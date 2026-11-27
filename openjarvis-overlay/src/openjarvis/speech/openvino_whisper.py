"""[Nau] 26/11/2026: Whisper «small» con OpenVINO en la NPU (o la gráfica) del PC.

Medido con habla rápida y ruido: 0,28–0,56 s por frase en la NPU frente a 1,3–1,5 s con
faster-whisper en la CPU, con el mismo acierto. Lo usa faster_whisper.py para la conversación;
si OpenVINO no está (un `uv sync` lo quita) o falla, se vuelve a faster-whisper solo.

Modelo: data/modelos/whisper-small-int8-ov (OpenVINO/whisper-small-int8-ov de Hugging Face).
La primera carga en la NPU compila el modelo (~20–45 s); queda en caché y luego tarda ~2 s.
"""

from __future__ import annotations

import io
import logging
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# src/src/openjarvis/speech/openvino_whisper.py → carpeta jarvis
_ROOT = Path(__file__).resolve().parents[4]
MODELS_DIR = _ROOT / "data" / "modelos"
CACHE_DIR = MODELS_DIR / "cache-openvino"
DEVICES = ("NPU", "GPU", "CPU")  # el primero que funcione
SAMPLE_RATE = 16000


class OpenVinoWhisper:
    """Un modelo Whisper de OpenVINO; se carga la primera vez que se usa."""

    def __init__(self, folder: str) -> None:
        self._path = MODELS_DIR / folder
        self._pipe = None
        self._use_prompt = True
        self._failed = False
        self.device: Optional[str] = None
        self._lock = threading.Lock()  # la pipeline no admite dos frases a la vez

    def available(self) -> bool:
        return not self._failed and self._path.is_dir()

    def _load(self) -> None:
        import openvino_genai as ov_genai  # noqa: PLC0415 - opcional

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        errors = []
        for device in DEVICES:
            try:
                self._pipe = ov_genai.WhisperPipeline(str(self._path), device,
                                                      CACHE_DIR=str(CACHE_DIR))
                self.device = device
                break
            except Exception as exc:  # noqa: BLE001 - se prueba el siguiente dispositivo
                errors.append(f"{device}: {exc}")
        if self._pipe is None:
            raise RuntimeError("; ".join(errors))
        logger.info("Whisper OpenVINO %s cargado en %s", self._path.name, self.device)

    def transcribe(self, wav_bytes: bytes, language: Optional[str], prompt: str = "",
                   hotwords: str = "") -> Optional[str]:
        """Texto de un WAV de 16 kHz mono, o None si no se puede (se usará faster-whisper)."""
        if not self.available():
            return None
        audio = _wav_to_float(wav_bytes)
        if audio is None:
            return None
        with self._lock:
            try:
                if self._pipe is None:
                    self._load()
                if self._use_prompt and (prompt or hotwords):
                    try:
                        return self._generate(audio, language, prompt, hotwords)
                    except Exception:  # noqa: BLE001
                        # La NPU tiene tamaños fijos y no admite el vocabulario: sin él
                        logger.info("Whisper OpenVINO en %s sin vocabulario inicial", self.device)
                        self._use_prompt = False
                return self._generate(audio, language, "", "")
            except Exception:  # noqa: BLE001 - nunca deja a Jarvis sin oír
                logger.exception("Whisper OpenVINO ha fallado; vuelvo a faster-whisper")
                self._failed = True
                return None

    def _generate(self, audio: np.ndarray, language: Optional[str], prompt: str,
                  hotwords: str) -> str:
        config = self._pipe.get_generation_config()
        config.task = "transcribe"
        config.return_timestamps = False
        config.language = f"<|{language}|>" if language else None
        if prompt and hasattr(config, "initial_prompt"):
            config.initial_prompt = prompt
        if hotwords and hasattr(config, "hotwords"):
            config.hotwords = hotwords
        return str(self._pipe.generate(audio.tolist(), config)).strip()


def _wav_to_float(wav_bytes: bytes) -> Optional[np.ndarray]:
    try:
        with wave.open(io.BytesIO(wav_bytes)) as wav:
            if (wav.getframerate() != SAMPLE_RATE or wav.getnchannels() != 1
                    or wav.getsampwidth() != 2):
                return None
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    except (wave.Error, EOFError):
        return None
    return pcm.astype(np.float32) / 32768
