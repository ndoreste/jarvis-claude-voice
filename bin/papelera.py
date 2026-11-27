"""[Nau] 27/11/2026: papelera propia de Jarvis («borra X», «recupera X», «deshaz»).

Borrar por voz es al momento y sin preguntar porque nunca es definitivo: el archivo o la
carpeta se mueve a Documentos\\Jarvis\\Papelera y se apunta de dónde venía (papelera.json).
Se recupera con «recupera X» o «deshaz lo último», y se vacía sola a los 30 días. Nunca se
vacía por voz. Hay zonas prohibidas (Windows, programas, AppData, claves, Jarvis, la bóveda
de Obsidian, carpetas enteras como Descargas o Documentos).
Solo biblioteca estándar.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from acciones_pc import ROOT, SpeakError, _similarity, jarvis_folder, known_folder, locate

KEEP_DAYS = 30
MANIFEST = "papelera.json"
RESTORE_MATCH = 0.75


def trash_dir() -> Path:
    folder = jarvis_folder() / "Papelera"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _protected_roots() -> list[Path]:
    home = Path.home()
    env = os.environ.get
    roots = [Path(env("SystemRoot", r"C:\Windows")), Path(env("ProgramFiles", r"C:\Program Files")),
             Path(env("ProgramFiles(x86)", r"C:\Program Files (x86)")),
             Path(env("ProgramData", r"C:\ProgramData")), home / "AppData", home / ".ssh",
             home / ".claude", home / ".gnupg", ROOT, trash_dir()]
    try:
        from ajustes import PROYECTOS

        roots.append(PROYECTOS / "claudeObsidian")  # la bóveda: sus notas no se borran por voz
    except ImportError:
        pass
    return roots


def _whole_folders() -> list[Path]:
    """Carpetas que no se borran enteras (sí lo que hay dentro)."""
    folders = [Path.home(), Path(Path.home().anchor), jarvis_folder()]
    folders += [p for p in (known_folder(k) for k in ("descargas", "documentos", "escritorio",
                                                       "imagenes", "musica", "videos")) if p]
    try:
        from ajustes import PROYECTOS

        folders.append(PROYECTOS)
    except ImportError:
        pass
    return folders


def check_allowed(path: Path) -> None:
    """SpeakError si la ruta está en una zona prohibida."""
    real = path.resolve()
    for root in _protected_roots():
        try:
            if real == root.resolve() or real.is_relative_to(root.resolve()):
                raise SpeakError(f"«{path.name}» está en una zona que no toco, señor.")
        except OSError:
            continue
    if any(real == folder.resolve() for folder in _whole_folders()):
        raise SpeakError(f"La carpeta «{path.name}» entera no la borro, señor.")


def _load() -> list[dict]:
    try:
        data = json.loads((trash_dir() / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save(entries: list[dict]) -> None:
    (trash_dir() / MANIFEST).write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                                        encoding="utf-8")


def purge_expired(now: float | None = None) -> int:
    """Vacía lo que lleva más de KEEP_DAYS en la papelera (lo único que se borra de verdad)."""
    now = now or time.time()
    keep, removed = [], 0
    for entry in _load():
        if now - float(entry.get("fecha", now)) < KEEP_DAYS * 86400:
            keep.append(entry)
            continue
        target = trash_dir() / str(entry.get("guardado", ""))
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)
        removed += 1
    if removed:
        _save(keep)
    return removed


def trash(path: Path) -> dict:
    """Mueve un archivo o carpeta a la papelera y devuelve su entrada."""
    if not path.exists():
        raise SpeakError(f"«{path.name}» ya no existe.")
    check_allowed(path)
    purge_expired()
    entry = {"id": uuid.uuid4().hex[:10], "nombre": path.name, "origen": str(path),
             "fecha": time.time(), "carpeta": path.is_dir()}
    entry["guardado"] = f"{entry['id']}_{path.name}"
    shutil.move(str(path), str(trash_dir() / entry["guardado"]))
    _save([*_load(), entry])
    return entry


def trash_by_name(query: str, where: str = "") -> tuple[str, dict]:
    """«borra el PDF de la factura»: busca el archivo (o la carpeta) y lo manda a la papelera."""
    wants_folder = "carpeta" in query.casefold()
    name = re.sub(r"(?i)\b(?:(?:la|las|el)\s+)?carpetas?\s+(?:(?:de|del)\s+)?", "", query).strip()
    if known_folder(name) is not None or known_folder(query) is not None:
        # «borra Descargas»: nunca la carpeta entera
        raise SpeakError(f"La carpeta «{name}» entera no la borro, señor. Dime qué archivo.")
    # Solo carpetas si lo dice («borra la carpeta Pruebas»): un archivo no encontrado no
    # acaba en una carpeta de nombre parecido
    path = locate(name, where, folders=wants_folder, strict=True)
    entry = trash(path)
    kind = "la carpeta" if entry["carpeta"] else ""
    return f"He mandado {kind} «{path.name}» a mi papelera.".replace("  ", " "), entry


def restore(entry_id: str = "", query: str = "") -> tuple[str, dict]:
    """Devuelve a su sitio una entrada (por id, por nombre o la última)."""
    entries = _load()
    if not entries:
        raise SpeakError("Mi papelera está vacía.")
    if entry_id:
        matches = [e for e in entries if e.get("id") == entry_id]
    elif query.strip():
        scored = sorted(((_similarity(query, Path(e["nombre"]).stem), e["fecha"], e) for e in entries),
                        key=lambda t: (t[0], t[1]), reverse=True)
        matches = [scored[0][2]] if scored and scored[0][0] >= RESTORE_MATCH else []
    else:
        matches = [max(entries, key=lambda e: e.get("fecha", 0))]
    if not matches:
        raise SpeakError(f"No tengo «{query or entry_id}» en mi papelera.")
    entry = matches[0]
    source = trash_dir() / entry["guardado"]
    target = Path(entry["origen"])
    if not source.exists():
        _save([e for e in entries if e is not entry])
        raise SpeakError(f"«{entry['nombre']}» ya no está en la papelera.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(f"{target.stem} (recuperado {datetime.now():%H%M%S}){target.suffix}")
    shutil.move(str(source), str(target))
    _save([e for e in entries if e.get("id") != entry["id"]])
    return f"He devuelto «{target.name}» a {target.parent.name}.", {**entry, "origen": str(target)}
