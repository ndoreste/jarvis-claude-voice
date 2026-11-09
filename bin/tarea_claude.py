"""[Nau] Ejecuta en segundo plano una tarea que Jarvis encarga a Claude Code.

Lo lanza la herramienta `encargar_a_claude` (src/src/openjarvis/tools/claude_code.py):
    pythonw.exe tarea_claude.py <id>
Lee ..\\data\\tareas\\<id>.json, ejecuta `claude -p` con los MCP, conectores y skills de
el usuario, y escribe el resultado en el mismo archivo. El oyente (escucha.py) lo lee y se lo
dice al usuario en voz alta.

Permisos: modo «auto» de Claude Code (bloquea lo peligroso). Además, lo que envía,
publica, paga o borra para siempre se devuelve como «CONFIRMAR: …» y Jarvis pide el sí.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from ajustes import PROYECTOS, USUARIO, claude_exe as _find_claude

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "data" / "tareas"
HOME = Path.home()
# [Nau] «En el proyecto de LinkedIn…»: la tarea arranca dentro de esa carpeta, como al
# abrirla en VS Code, y carga su CLAUDE.md, su memoria y sus skills
PROJECTS_DIR = PROYECTOS
PROJECT_MATCH_CUTOFF = 0.75
# Sonnet: rápido y gasta menos plan que Opus; tiene las mismas skills, MCP y conectores
CLAUDE_MODEL = "sonnet"
TIMEOUT_S = 20 * 60
CONFIRM_PREFIX = "CONFIRMAR:"
# Solo lo añade este script, y solo si Jarvis marca la tarea como confirmada por el usuario
CONFIRMED_MARK = f"[{USUARIO.upper()} HA CONFIRMADO POR VOZ]"
# Cualquier imitación de la marca en el texto dictado (mayúsculas, espacios, otro nombre)
FAKE_MARK = re.compile(r"\[[^\]]*confirmad[oa]\s+por\s+voz[^\]]*\]", re.IGNORECASE)
# [Nau] 09/11/2026: «confirmado» solo vale para un permiso que Jarvis ya ha preguntado
# por voz, de esa misma sesión, reciente y de un solo uso. Así una inyección de prompt
# (una web leída por el cerebro) no puede pedir una acción nueva «ya confirmada».
CONFIRM_WINDOW_S = 10 * 60
MAX_RESULT_CHARS = 1200

SYSTEM_PROMPT = (
    f"Recibes una orden que {USUARIO} ha dado por voz a su asistente Jarvis; Jarvis te la pasa "
    "para que la hagas en su PC (Windows 11) o en sus cuentas conectadas. Hazla entera tú "
    "solo y decide los detalles menores sin preguntar: nadie lee esta sesión en directo. "
    "Tu último mensaje lo dirá Jarvis en voz alta, en primera persona, como si lo hubiera "
    "hecho él: no te nombres ni menciones a Claude ni a Claude Code. Escribe de 1 a 3 frases cortas en "
    "español, sin markdown, sin listas, sin rutas largas ni URLs, diciendo qué has hecho "
    "o qué has encontrado. Para el navegador usa las herramientas de Claude in Chrome "
    f"(mcp__claude-in-chrome__*), que manejan el Chrome de {USUARIO} con sus sesiones abiertas; "
    "no abras otro navegador aparte salvo que esas no estén disponibles.\n"
    "REGLA DE CONFIRMACIÓN, sin excepciones: la orden llega transcrita de la voz y puede "
    "ser una activación falsa o un error de transcripción, así que aunque la orden lo pida "
    "de forma explícita, NO envíes nada a otras personas (correo, WhatsApp, mensajes), NO "
    "publiques, NO compres ni pagues, y NO borres, sobrescribas ni muevas a la papelera "
    f"archivos, carpetas, correos, eventos o datos de {USUARIO}. Tampoco busques otro camino si "
    "algo te lo bloquean. Prepara todo lo demás y empieza tu último mensaje exactamente con "
    f"«{CONFIRM_PREFIX}» seguido de una frase que diga qué harás exactamente cuando {USUARIO} "
    f"diga que sí. Solo puedes hacerlo si el mensaje de {USUARIO} empieza por {CONFIRMED_MARK}."
)


def claude_exe() -> str:
    """El .exe nativo: un .cmd rompería las comillas del prompt de sistema."""
    return _find_claude()


def save(path: Path, task: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _plain(name: str) -> str:
    """«Motor Agéntico», «motorAgéntico» y «motor agentico» → «motoragentico»."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(c for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def find_project(spoken: str) -> Path | None:
    """Carpeta de claudeProjects que corresponde a cómo la ha dicho el usuario (o la transcripción)."""
    wanted = _plain(spoken)
    if not wanted or not PROJECTS_DIR.is_dir():
        return None
    folders = {_plain(p.name): p for p in PROJECTS_DIR.iterdir() if p.is_dir()}
    if wanted in folders:
        return folders[wanted]
    # «CorGon» → CorGonCybersolutions: si solo una carpeta contiene lo dicho, es esa
    containing = [key for key in folders if len(wanted) >= 4 and wanted in key]
    if len(containing) == 1:
        return folders[containing[0]]
    close =difflib.get_close_matches(wanted, folders, n=1, cutoff=PROJECT_MATCH_CUTOFF)
    return folders[close[0]] if close else None


def previous_project(session_id: str) -> str | None:
    """Proyecto de la tarea que se continúa (la sesión solo se reanuda desde su carpeta)."""
    for path in TASKS_DIR.glob("*.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if task.get("session_id") == session_id:
            return task.get("carpeta")
    return None


def work_dir(task: dict) -> Path | None:
    """Carpeta donde arranca la tarea. None si el usuario ha nombrado un proyecto que no existe."""
    if task.get("sesion_previa"):
        folder = previous_project(task["sesion_previa"])
        if folder and Path(folder).is_dir():
            return Path(folder)
    if task.get("proyecto"):
        return find_project(task["proyecto"])
    return HOME


def build_command(task: dict) -> list[str]:
    cmd = [
        claude_exe(), "-p",
        "--output-format", "json",
        "--permission-mode", "auto",
        "--model", CLAUDE_MODEL,
        "--add-dir", str(HOME),
        # El Chrome del usuario con sus sesiones (extensión Claude in Chrome), no uno aparte
        "--chrome",
        "--append-system-prompt", SYSTEM_PROMPT,
        "--name", f"Jarvis{' (' + Path(task['carpeta']).name + ')' if task.get('carpeta') else ''}: "
                  f"{task['tarea'][:60]}",
    ]
    if task.get("sesion_previa"):
        cmd += ["--resume", task["sesion_previa"]]
    return cmd


def prompt_for(task: dict) -> str:
    """La marca de confirmación la pone este script, nunca el texto dictado."""
    text = FAKE_MARK.sub("", task["tarea"].replace(CONFIRMED_MARK, "")).strip()
    return f"{CONFIRMED_MARK} {text}" if task.get("confirmado") else text


def subscription_env() -> dict[str, str]:
    """Sin claves de API: Claude Code usa siempre la sesión de claude.ai (plan Max), nunca
    la API de pago por uso, aunque algún día haya una clave en las variables de entorno."""
    blocked = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
               "CLAUDE_CODE_USE_VERTEX"}
    return {k: v for k, v in os.environ.items() if k.upper() not in blocked}


def run(task: dict) -> dict:
    """Ejecuta Claude Code y devuelve los campos de resultado."""
    folder = work_dir(task)
    if folder is None:
        return {"estado": "error",
                "resultado": f"No encuentro ningún proyecto que se llame «{task['proyecto']}».",
                "detalle": f"Sin carpeta parecida en {PROJECTS_DIR}"}
    task["carpeta"] = str(folder) if folder != HOME else None
    try:
        proc = subprocess.run(
            build_command(task), input=prompt_for(task), capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=str(folder), timeout=TIMEOUT_S,
            creationflags=subprocess.CREATE_NO_WINDOW, env=subscription_env(),
        )
    except subprocess.TimeoutExpired:
        return {"estado": "error", "resultado": "Ha tardado más de 20 minutos y la he parado."}
    except OSError as exc:
        return {"estado": "error", "resultado": "No he podido ponerme con ello por un fallo técnico.",
                "detalle": f"No arranca Claude Code: {exc}"}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "sin salida").strip()[:300]
        return {"estado": "error", "resultado": "Ha habido un fallo técnico y no he podido terminarlo.",
                "detalle": detail}
    text = str(data.get("result") or "").strip()[:MAX_RESULT_CHARS]
    fields = {
        "session_id": data.get("session_id"),
        "coste_usd": data.get("total_cost_usd"),
        "denegados": len(data.get("permission_denials") or []),
    }
    if data.get("is_error") or not text:
        return {**fields, "estado": "error", "resultado": text or "Ha terminado sin respuesta."}
    confirm = text.startswith(CONFIRM_PREFIX)
    if confirm:
        text = text[len(CONFIRM_PREFIX):].strip()
    return {**fields, "estado": "hecha", "resultado": text, "confirmar": confirm}


def pending_confirmation(session_id: str) -> Path | None:
    """Tarea de esa sesión que pidió permiso, que Jarvis ya ha preguntado y que sigue sin usar."""
    now = time.time()
    for path in TASKS_DIR.glob("*.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (task.get("session_id") == session_id and task.get("confirmar")
                and task.get("anunciado") and not task.get("confirmacion_usada")
                and now - float(task.get("terminada") or 0) < CONFIRM_WINDOW_S):
            return path
    return None


def accept_confirmation(task: dict) -> bool:
    """¿Vale el «confirmado» de esta tarea? Si vale, gasta el permiso pendiente."""
    if not task.get("confirmado"):
        return False
    session = task.get("sesion_previa")
    pending = pending_confirmation(session) if session else None
    if pending is None:
        task["detalle"] = ("Confirmación ignorada: no había un permiso pendiente, ya preguntado "
                           "por voz y reciente, para esa sesión")
        return False
    asked = json.loads(pending.read_text(encoding="utf-8"))
    asked["confirmacion_usada"] = time.time()
    save(pending, asked)
    return True


def main() -> None:
    path = TASKS_DIR / f"{sys.argv[1]}.json"
    task = json.loads(path.read_text(encoding="utf-8"))
    task.update(estado="en_curso", empezada=time.time(), confirmado=accept_confirmation(task))
    save(path, task)
    task.update(run(task))
    task["terminada"] = time.time()
    save(path, task)


if __name__ == "__main__":
    main()
