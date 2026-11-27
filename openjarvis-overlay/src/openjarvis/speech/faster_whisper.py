"""Faster-Whisper speech-to-text backend (local, CTranslate2-based)."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import List, Optional

from openjarvis.core.registry import SpeechRegistry
from openjarvis.speech._stubs import Segment, SpeechBackend, TranscriptionResult
from openjarvis.speech.openvino_whisper import OpenVinoWhisper

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None  # type: ignore[assignment, misc]

try:
    import ctranslate2
except ImportError:
    ctranslate2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

HOTWORDS = "Jarvis"
INITIAL_PROMPT = (
    "Conversación en español de España con su asistente. Nau habla de CorGon, ASIR, "
    "Linux, VirtualBox, WhatsApp, Obsidian, Chrome, Skool, Imperio Agéntico, el micro y la agenda."
)
# [Nau] 26/11/2026: modelos que se pueden pedir además del de config (campo «model»)
EXTRA_MODELS = {"small"}
# Y su versión de OpenVINO (NPU/gráfica), mucho más rápida; si falla, faster-whisper en la CPU
OPENVINO_MODELS = {"small": OpenVinoWhisper("whisper-small-int8-ov")}


@SpeechRegistry.register("faster-whisper")
class FasterWhisperBackend(SpeechBackend):
    """Local speech-to-text using Faster-Whisper (CTranslate2)."""

    backend_id = "faster-whisper"

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "float16",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: Optional[WhisperModel] = None
        self._extras: dict = {}  # [Nau] modelos de EXTRA_MODELS ya cargados
        self._last_error: Optional[str] = None

    def _resolve_compute_type(self) -> str:
        """Pick a CTranslate2 compute type supported by the configured device."""
        if ctranslate2 is None:
            return self._compute_type

        try:
            supported = set(ctranslate2.get_supported_compute_types(self._device))
        except Exception as exc:
            logger.debug(
                "Could not inspect CTranslate2 compute types for %s: %s",
                self._device,
                exc,
            )
            return self._compute_type

        if self._compute_type in supported:
            return self._compute_type

        preferences = (
            ("int8", "float32", "int8_float32", "int16")
            if self._compute_type == "float16"
            else ("float32", "int8", "int8_float32", "int16")
        )
        fallback = next((value for value in preferences if value in supported), None)
        if fallback is None:
            return self._compute_type

        logger.warning(
            "CTranslate2 compute_type=%r is not supported on device=%r; "
            "using %r instead",
            self._compute_type,
            self._device,
            fallback,
        )
        return fallback

    def _extra_model(self, size: str) -> WhisperModel:
        """[Nau] 26/11/2026: otro modelo bajo demanda («small» para la conversación: entiende
        el habla a ritmo normal; el «base» por defecto sigue para despertar, que es más rápido)."""
        if size not in self._extras:
            self._ensure_model()  # comprueba que faster-whisper está instalado
            self._extras[size] = WhisperModel(
                size, device=self._device, compute_type=self._resolve_compute_type(),
                cpu_threads=12)  # 12 hilos: 1,17 s frente a 1,25 s con 8 (medido)
        return self._extras[size]

    def _ensure_model(self) -> WhisperModel:
        """Lazy-load the Whisper model on first use."""
        if self._model is None:
            if WhisperModel is None:
                self._last_error = (
                    "faster-whisper is not installed. "
                    "Install with: uv sync --extra desktop"
                )
                raise ImportError(self._last_error)
            compute_type = self._resolve_compute_type()
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=compute_type,
                # [Nau] 09/11/2026: 8 hilos, 0,48 s por frase frente a 0,63 s (medido)
                cpu_threads=8,
            )
        self._last_error = None
        return self._model

    def transcribe(
        self,
        audio: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
        model_size: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe audio bytes using Faster-Whisper."""
        fast = OPENVINO_MODELS.get(model_size or "")
        if fast is not None and format.lstrip(".") == "wav":
            text = fast.transcribe(audio, language, INITIAL_PROMPT, HOTWORDS)
            if text is not None:
                return TranscriptionResult(text=text, language=language, confidence=None,
                                           duration_seconds=0.0, segments=[])
        try:
            if model_size and model_size != self._model_size and model_size in EXTRA_MODELS:
                model = self._extra_model(model_size)
            else:
                model = self._ensure_model()

            # Write audio to a temp file (faster-whisper needs a file path).
            # delete=False + manual unlink: on Windows an open
            # NamedTemporaryFile holds an exclusive handle, so PyAV's reopen
            # of tmp.name inside model.transcribe() fails with EACCES.
            suffix = f".{format}" if not format.startswith(".") else format
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            try:
                with tmp:
                    tmp.write(audio)

                # Empuja a Whisper a escribir el nombre del asistente («Hola Jarvis»
                # salía como «Hola ya Arbis»); con ruido o silencio no lo inventa.
                # vad_filter: el detector de voz (Silero) quita ruido, viento y silencios
                # antes de transcribir, así Whisper no se inventa frases con ellos.
                # [Nau] 09/11/2026: beam_size=1 (búsqueda voraz): igual de preciso con frases
                # cortas y hasta 0,4 s más rápido que el beam 5 por defecto con la CPU cargada.
                # [Nau] 24/11/2026: initial_prompt con su vocabulario (CorGon salía «Corbón»);
                # sin «Jarvis» dentro, para que no se invente el nombre con ruido (probado con
                # silencio y ruido: no escribe nada).
                # [Nau] 26/11/2026: without_timestamps (no se usan): small 1,42 → 1,34 s (medido)
                kwargs = {"hotwords": HOTWORDS, "vad_filter": True, "beam_size": 1,
                          "initial_prompt": INITIAL_PROMPT,
                          "condition_on_previous_text": False, "without_timestamps": True}
                if language:
                    kwargs["language"] = language

                segments_iter, info = model.transcribe(tmp.name, **kwargs)
                segments_list = list(segments_iter)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError as unlink_exc:
                    logger.debug(
                        "Could not remove temp audio file %s: %s",
                        tmp.name,
                        unlink_exc,
                    )
        except Exception as exc:
            self._last_error = str(exc)
            raise

        # Build result
        text = "".join(seg.text for seg in segments_list).strip()
        segments = [
            Segment(
                text=seg.text.strip(),
                start=seg.start,
                end=seg.end,
                confidence=None,
            )
            for seg in segments_list
        ]

        self._last_error = None
        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", None),
            confidence=getattr(info, "language_probability", None),
            duration_seconds=getattr(info, "duration", 0.0),
            segments=segments,
        )

    def health(self) -> bool:
        """Check if model is loaded or loadable."""
        try:
            self._ensure_model()
            return True
        except Exception as exc:
            self._last_error = str(exc)
            logger.debug("Faster-Whisper health check failed: %s", exc)
            return False

    def last_error(self) -> Optional[str]:
        """Return the last model load or transcription error, if any."""
        return self._last_error

    def supported_formats(self) -> List[str]:
        """Supported audio formats (same as ffmpeg/Whisper)."""
        return ["wav", "mp3", "m4a", "ogg", "flac", "webm"]
