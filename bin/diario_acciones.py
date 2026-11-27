"""[Nau] 27/11/2026: diario de las acciones al momento de Jarvis.

- Cada acción hecha se apunta (data/acciones-diario.json, las últimas 60) con lo necesario para
  darle la vuelta: «deshaz lo último» (undo).
- Se registra también en la bóveda de Obsidian: claudeObsidian/Jarvis/Registro/<día>.md, sin
  el texto de los mensajes ni de los archivos.
- Rutinas: «guarda esto como modo estudio» guarda las acciones repetibles de la conversación y
  «modo estudio» las repite (data/rutinas.json, con copia legible en Jarvis/Rutinas.md).
- Confirmación pendiente: enviar un WhatsApp o un correo y quitar un evento esperan al «sí»
  que comprueba el oyente (set_pending / take_pending), nunca el modelo.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from acciones_pc import ROOT, SpeakError, _plain, _similarity

JOURNAL = ROOT / "data" / "acciones-diario.json"
ROUTINES = ROOT / "data" / "rutinas.json"
JOURNAL_MAX = 60
UNDO_MAX_AGE_S = 60 * 60  # «deshaz» solo alcanza a la última hora
PENDING_MAX_AGE_S = 120
CONVERSATION_FALLBACK_S = 15 * 60
ROUTINE_MATCH = 0.75
REPLAYABLE = {"abrir", "cerrar", "musica", "volumen", "bloquear"}
PRIVATE_KINDS = {"whatsapp", "correo"}  # en el registro no va lo que dice el mensaje
_lock = threading.Lock()
_conversation_started = 0.0


def _vault_dir() -> Path | None:
    try:
        from ajustes import PROYECTOS
    except ImportError:
        return None
    vault = PROYECTOS / "claudeObsidian"
    return vault / "Jarvis" if vault.is_dir() else None


# ── Diario ────────────────────────────────────────────────────────────────────

def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def entries() -> list[dict]:
    data = _load(JOURNAL, [])
    return data if isinstance(data, list) else []


def mark_conversation() -> None:
    """El oyente avisa de que empieza una conversación (para «guarda esto como…»)."""
    global _conversation_started
    _conversation_started = time.time()


def record(kind: str, spec: str, result: str, order: str = "", undo: dict | None = None) -> None:
    """Apunta una acción hecha (y cómo deshacerla) en el diario y en Obsidian."""
    entry = {"t": time.time(), "tipo": kind, "spec": spec, "resultado": result,
             "orden": "" if kind in PRIVATE_KINDS else order, "deshacer": undo, "deshecha": False}
    with _lock:
        JOURNAL.write_text(json.dumps([*entries(), entry][-JOURNAL_MAX:], ensure_ascii=False,
                                      indent=1), encoding="utf-8")
    _write_registry(entry)


def _write_registry(entry: dict) -> None:
    folder = _vault_dir()
    if folder is None:
        return
    day = datetime.fromtimestamp(entry["t"])
    note = folder / "Registro" / f"{day:%Y-%m-%d}.md"
    try:
        note.parent.mkdir(parents=True, exist_ok=True)
        if not note.exists():
            note.write_text(f"# Acciones de Jarvis · {day:%d/%m/%Y}\n\nLo que Jarvis hizo al momento "
                            "(sin el texto de mensajes ni archivos).\n\n", encoding="utf-8")
        said = f"«{entry['orden']}» → " if entry["orden"] else ""
        with note.open("a", encoding="utf-8") as fh:
            fh.write(f"- {day:%H:%M} · {said}{entry['tipo']}: {entry['resultado']}\n")
    except OSError:
        pass


def _mark_undone(target: dict) -> None:
    with _lock:
        updated = [{**e, "deshecha": True} if e["t"] == target["t"] and e["spec"] == target["spec"]
                   else e for e in entries()]
        JOURNAL.write_text(json.dumps(updated, ensure_ascii=False, indent=1), encoding="utf-8")


def undo(run_inverse: Callable[[dict], str]) -> str:
    """Deshace la última acción (de la última hora). run_inverse ejecuta la vuelta atrás y
    devuelve qué ha hecho. SpeakError si lo último no se puede deshacer."""
    now = time.time()
    recent = [e for e in entries() if not e.get("deshecha") and now - e["t"] < UNDO_MAX_AGE_S
              and e["tipo"] != "deshacer"]
    if not recent:
        raise SpeakError("No tengo nada reciente que deshacer, señor.")
    last = recent[-1]
    if not last.get("deshacer"):
        raise SpeakError(f"Lo último que hice fue {last['resultado']}, y eso no se puede deshacer.")
    done = run_inverse(last["deshacer"])
    _mark_undone(last)
    return f"Deshecho: {done}."


# ── Rutinas ───────────────────────────────────────────────────────────────────

def routines() -> dict[str, list[str]]:
    data = _load(ROUTINES, {})
    return data if isinstance(data, dict) else {}


def save_routine(name: str) -> str:
    name = name.strip().strip("«»\"'")
    if not name:
        raise SpeakError("Dime qué nombre le pongo a la rutina.")
    since = _conversation_started or time.time() - CONVERSATION_FALLBACK_S
    specs = [e["spec"] for e in entries()
             if e["t"] >= since and e["tipo"] in REPLAYABLE and not e.get("deshecha")]
    if not specs:
        raise SpeakError("En esta conversación no he hecho nada que se pueda repetir: abrir, "
                         "cerrar, música o volumen.")
    data = {**routines(), name: specs}
    ROUTINES.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_routines_note(data)
    return f"Guardada la rutina «{name}» con {len(specs)} {'acción' if len(specs) == 1 else 'acciones'}."


def find_routine(name: str) -> tuple[str, list[str]]:
    data = routines()
    if not data:
        raise SpeakError("Aún no tengo ninguna rutina guardada.")
    wanted = re.sub(r"^(?:la |el )?(?:rutina |modo )?", "", _plain(name))
    score, found = max((max(_similarity(name, n), _similarity(wanted, _plain(n))), n) for n in data)
    if score < ROUTINE_MATCH:
        raise SpeakError(f"No tengo ninguna rutina «{name}». Tengo: {', '.join(data)}.")
    return found, data[found]


def _write_routines_note(data: dict[str, list[str]]) -> None:
    folder = _vault_dir()
    if folder is None:
        return
    lines = ["# Rutinas de Jarvis", "",
             "Se crean por voz («guarda esto como modo estudio») y se lanzan diciendo su nombre. "
             "Este archivo es una copia legible; las rutinas viven en `jarvis/data/rutinas.json`.", ""]
    for name, specs in data.items():
        lines += [f"## {name}", "", *[f"- `{s}`" for s in specs], ""]
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "Rutinas.md").write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


# ── Confirmación pendiente ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Pending:
    question: str
    run: Callable[[], str]  # hace el envío y devuelve qué ha hecho
    created: float


_pending: list[Pending] = []


def set_pending(question: str, run: Callable[[], str]) -> str:
    _pending[:] = [Pending(question, run, time.monotonic())]
    return question


def take_pending() -> Pending | None:
    """La confirmación pendiente (y la quita), si es reciente."""
    pending, _pending[:] = (_pending[0] if _pending else None), []
    if pending is None or time.monotonic() - pending.created > PENDING_MAX_AGE_S:
        return None
    return pending


def has_pending() -> bool:
    return bool(_pending) and time.monotonic() - _pending[0].created <= PENDING_MAX_AGE_S


YES_WORDS = {"si", "vale", "venga", "claro", "adelante", "dale", "ok", "okay", "correcto",
             "confirmo", "confirmado", "hazlo", "mandalo", "envialo", "mandaselo", "enviaselo",
             "quitalo", "borralo", "perfecto", "exacto", "eso"}
NO_WORDS = {"no", "cancela", "cancelalo", "espera", "para", "dejalo", "olvidalo", "nada"}
CHANGE_WORDS = {"pero", "cambia", "cambialo", "aunque", "corrige", "vez"}


def answer_kind(text: str) -> str:
    """'si', 'no' u 'otra' para la respuesta a «¿Lo mando?». Un «sí, pero cambia…» es 'otra'
    (lo vuelve a preparar el cerebro); solo frases cortas cuentan."""
    words = _plain(text).split()
    if not words or len(words) > 6 or any(w in CHANGE_WORDS for w in words):
        return "otra"
    if words[0] in NO_WORDS or (len(words) > 1 and words[0] == "mejor" and words[1] == "no"):
        return "no"
    if words[0] in YES_WORDS or (words[0] in {"jarvis"} and len(words) > 1 and words[1] in YES_WORDS):
        return "si"
    return "otra"
