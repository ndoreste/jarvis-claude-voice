"""Memoria persistente de las conversaciones por voz de Jarvis.

Cada conversación (desde que el usuario le despierta hasta que se despide o se calla) se
guarda como una línea en data\\memoria-voz.jsonl. Al despertar, Jarvis empieza de
cero; solo si el usuario pide retomar algo («vamos a retomar lo de Telegram») se buscan
las conversaciones que hablan de eso y se le pasan como contexto.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from pathlib import Path

from ajustes import USUARIO

log = logging.getLogger("escucha")

# «vamos a retomar…», «sigamos con…», «¿te acuerdas de…?», «lo que hablamos de…»
RESUME_REQUEST = re.compile(
    r"\b(?:retom\w*|reanud\w*|(?:sigamos|seguimos|seguir|continuemos|continuamos|continuar|volvamos|volver)"
    r"\s+(?:con|a|por)|te acuerdas|recuerdas|lo que (?:hablamos|habl[aá]bamos|estuvimos hablando|dijimos|comentamos)"
    r"|habl[aá]bamos|la otra vez|el otro d[ií]a)\b",
    re.IGNORECASE,
)
# Palabras que no sirven para saber de qué tema se habla
STOPWORDS = {
    "vamos", "retomar", "retomemos", "retoma", "retomamos", "reanudar", "sigamos", "seguimos",
    "seguir", "continuar", "continuemos", "continuamos", "volvamos", "volver", "hablamos",
    "hablabamos", "hablando", "estuvimos", "dijimos", "comentamos", "acuerdas", "recuerdas",
    "aquello", "sobre", "tema", "temas", "cosa", "cosas", "esto", "esta", "este", "estos",
    "otra", "otro", "para", "como", "cuando", "donde", "jarvis", "quiero", "puedes", "ayer",
    "antes", "dias", "hola", "favor", "conversacion", "nuestra", "nuestro", "todo", "algo",
    "entonces", "pues", "bueno", "vale", "aquel", "aquella", "dicho", "habiamos", "estabamos",
}
STEM_LEN = 6  # «telegram», «telegrama» → «telegr»
MAX_RECALLED = 2
MAX_CHARS_PER_CONVERSATION = 4000


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _stamp(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def _keywords(request: str) -> list[str]:
    words = re.findall(r"\w+", _plain(request))
    return sorted({w[:STEM_LEN] for w in words if len(w) >= 4 and w not in STOPWORDS})


def _transcript(conversation: dict) -> str:
    lines = [
        f"{USUARIO if m['role'] == 'user' else 'Jarvis'}: {m['content']}"
        for m in conversation.get("mensajes", [])
    ]
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS_PER_CONVERSATION else "…" + text[-MAX_CHARS_PER_CONVERSATION:]


class VoiceMemory:
    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, started: float, messages: list[dict]) -> None:
        """Guarda una conversación terminada (solo los turnos del usuario y Jarvis)."""
        turns = [
            {"role": m["role"], "content": m["content"]}
            for m in messages if m.get("role") in ("user", "assistant")
        ]
        if not any(m["role"] == "user" for m in turns):
            return
        record = {"inicio": _stamp(started), "fin": _stamp(time.time()), "mensajes": turns}
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            log.exception("No se pudo guardar la conversación en la memoria")

    def load(self) -> list[dict]:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        conversations = []
        for line in lines:
            try:
                conversations.append(json.loads(line))
            except ValueError:
                log.warning("Línea dañada en la memoria de voz, se ignora")
        return conversations

    def recall(self, request: str) -> dict | None:
        """Mensaje de sistema con las conversaciones que el usuario pide retomar, o None."""
        conversations = self.load()
        if not conversations:
            return None
        keywords = _keywords(request)
        scored = []
        for index, conv in enumerate(conversations):
            text = _plain(_transcript(conv))
            scored.append((sum(text.count(k) for k in keywords), index))
        best = [s for s in sorted(scored, reverse=True) if s[0] > 0][:MAX_RECALLED]
        # Sin tema claro («retomemos lo de antes»): la última conversación
        chosen = sorted(i for _, i in best) if best else [len(conversations) - 1]
        log.info("Retomando conversaciones %s (palabras clave %s)",
                 [conversations[i]["inicio"] for i in chosen], keywords)
        blocks = [f"[{conversations[i]['inicio']}]\n{_transcript(conversations[i])}" for i in chosen]
        return {
            "role": "system",
            "content": (
                f"{USUARIO} te pide retomar algo que hablasteis en otra conversación. Estas son las "
                "conversaciones guardadas que tratan de eso (de la más antigua a la más reciente). "
                "Úsalas para seguir el tema que pide; si no encuentras lo que busca, díselo.\n\n"
                + "\n\n".join(blocks)
            ),
        }

    def import_legacy(self, legacy_file: Path) -> None:
        """Pasa el historial antiguo (conversacion-voz.json) a la memoria, una sola vez."""
        if not legacy_file.exists():
            return
        try:
            messages = json.loads(legacy_file.read_text(encoding="utf-8"))
            self.save(legacy_file.stat().st_mtime, messages)
            legacy_file.rename(legacy_file.with_suffix(".migrado.json"))
            log.info("Historial antiguo pasado a la memoria de voz")
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("No se pudo pasar el historial antiguo a la memoria")
