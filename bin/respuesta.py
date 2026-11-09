"""[Nau] Respuesta de Jarvis por trozos para la voz.

Answer pide la respuesta al cerebro Sonnet (cerebro_claude.py) y la va entregando por
frases según llegan, para que Jarvis empiece a hablar con la primera. Si Sonnet falla
antes de decir nada, usa la reserva (Haiku por OpenRouter) y la trocea igual.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator

from cerebro_claude import FLUSH, BrainError, ClaudeBrain

log = logging.getLogger("escucha.respuesta")

SENTENCE_END = re.compile(r"[.!?…;:](?=\s)")
# [Nau] 09/11/2026: la primera frase sale en cuanto acaba en punto, aunque no llegue nada
# detrás («Déjame mirar tu agenda.» esperaba a la herramienta: 3 s en vez de ~1 s)
FIRST_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")
CLAUSE_END = re.compile(r"[.!?…;:,](?=\s)")
FIRST_CHUNK_MIN_CHARS = 12  # [Nau] 25 → 12: una primera frase más corta se junta con la siguiente
# Si la primera frase es larga, el primer trozo corta en una coma: Jarvis empieza antes
FIRST_CLAUSE_MIN_CHARS = 50
NEXT_CHUNK_MIN_CHARS = 80
SPEECH_CHUNK_CHARS = 180


def speech_chunks(text: str) -> list[str]:
    """Trozos de un texto completo: la primera frase sola (suena antes) y el resto en bloques."""
    sentences = [s for s in re.split(r"(?<=[.!?…;:])\s+", text.strip()) if s]
    chunks: list[str] = []
    for sentence in sentences:
        if not chunks:
            chunks.append(sentence)
        elif len(chunks) == 1 and len(chunks[0]) < FIRST_CHUNK_MIN_CHARS:
            chunks[0] += " " + sentence
        elif len(chunks) > 1 and len(chunks[-1]) + len(sentence) < SPEECH_CHUNK_CHARS:
            chunks[-1] += " " + sentence
        else:
            chunks.append(sentence)
    return chunks


def pop_chunk(buffer: str, min_chars: int, pattern: re.Pattern = SENTENCE_END) -> tuple[str | None, str]:
    """Saca del texto recibido un trozo que acabe en fin de frase y tenga al menos min_chars."""
    for match in pattern.finditer(buffer):
        if match.end() >= min_chars:
            return buffer[:match.end()].strip(), buffer[match.end():]
    return None, buffer


def pop_first_chunk(buffer: str) -> tuple[str | None, str]:
    chunk, rest = pop_chunk(buffer, FIRST_CHUNK_MIN_CHARS, FIRST_SENTENCE_END)
    if chunk is None:
        chunk, rest = pop_chunk(buffer, FIRST_CLAUSE_MIN_CHARS, CLAUSE_END)
    return chunk, rest


class Answer:
    """Iterador de trozos de la respuesta; .text es lo que ha llegado hasta ahora."""

    def __init__(self, brain: ClaudeBrain, prompt: str,
                 fallback: Callable[[], str | None]) -> None:
        self.parts: list[str] = []
        self.source = "sonnet"
        self.failed = False
        self._cancelled = False
        self._chunks = self._produce(brain, prompt, fallback)

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        return next(self._chunks)

    @property
    def text(self) -> str:
        return " ".join("".join(self.parts).split())

    def cancel(self) -> None:
        """Deja de entregar trozos (El usuario ha interrumpido o ha seguido hablando)."""
        self._cancelled = True

    def _produce(self, brain: ClaudeBrain, prompt: str,
                 fallback: Callable[[], str | None]) -> Iterator[str]:
        produced = False
        try:
            buffer = ""
            for delta in brain.ask(prompt):
                if self._cancelled:
                    return
                self.parts.append(delta)
                if delta == FLUSH and buffer.strip():
                    produced = True
                    yield buffer.strip()
                    buffer = ""
                    continue
                buffer += delta
                chunk, buffer = (pop_chunk(buffer, NEXT_CHUNK_MIN_CHARS) if produced
                                 else pop_first_chunk(buffer))
                while chunk is not None:
                    produced = True
                    yield chunk
                    chunk, buffer = pop_chunk(buffer, NEXT_CHUNK_MIN_CHARS)
            if buffer.strip() and not self._cancelled:
                yield buffer.strip()
            return
        except BrainError as exc:
            log.warning("Cerebro Sonnet sin respuesta (%s)%s", exc,
                        "" if produced else "; uso la reserva Haiku")
            if produced:
                return
        self.parts, self.source = [], "haiku"
        reply = fallback()
        if reply is None:
            self.failed = True
            return
        self.parts = [reply]
        yield from speech_chunks(reply)
