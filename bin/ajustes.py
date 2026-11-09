"""[Nau] Ajustes personales de Jarvis: nombre, perfil, carpeta de proyectos y claude.exe.

Se leen de data/usuario.toml (plantilla: config/usuario.example.toml). Así el código no
lleva datos personales y cada persona pone los suyos sin tocar los scripts.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("OPENJARVIS_HOME") or ROOT / "data")
SETTINGS_FILE = DATA_DIR / "usuario.toml"


def _load() -> dict:
    try:
        with SETTINGS_FILE.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


_SETTINGS = _load()

USUARIO: str = str(_SETTINGS.get("nombre") or "usuario")
PERFIL: str = str(_SETTINGS.get("perfil") or "").strip()
PROYECTOS = Path(os.path.expandvars(os.path.expanduser(
    str(_SETTINGS.get("proyectos") or "~/claudeProjects"))))


def claude_exe() -> str:
    """El claude.exe nativo: un .cmd rompería las comillas del prompt de sistema."""
    configured = str(_SETTINGS.get("claude_exe") or os.environ.get("JARVIS_CLAUDE_EXE") or "")
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("claude.exe")
    if found:
        return found
    guesses = (
        Path.home() / ".local" / "bin" / "claude.exe",
        Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@anthropic-ai"
        / "claude-code" / "bin" / "claude.exe",
    )
    return next((str(p) for p in guesses if p.exists()), "claude.exe")
