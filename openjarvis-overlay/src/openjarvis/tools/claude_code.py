"""[Nau] Herramienta que encarga tareas del PC a Claude Code en segundo plano.

Jarvis (Haiku) la usa cuando el usuario pide HACER algo en su ordenador o en sus cuentas.
Crea data/tareas/<id>.json y lanza bin/tarea_claude.py sin ventana; vuelve al momento
para que Jarvis diga «me pongo con ello». Cuando Claude Code termina, el oyente
(bin/escucha.py) lee el resultado y se lo dice al usuario en voz alta.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_TOOL_NAME = "encargar_a_claude"
# src/src/openjarvis/tools/claude_code.py → carpeta jarvis
_ROOT = Path(__file__).resolve().parents[4]
_TASKS_DIR = _ROOT / "data" / "tareas"
_RUNNER = _ROOT / "bin" / "tarea_claude.py"
_MAX_RUNNING = 3
_STALE_S = 30 * 60  # una tarea «en curso» más antigua se da por muerta
_MAX_TASK_CHARS = 4000


def _running_tasks() -> int:
    now = time.time()
    count = 0
    for path in _TASKS_DIR.glob("*.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if task.get("estado") in ("pendiente", "en_curso") and now - task.get("creada", 0) < _STALE_S:
            count += 1
    return count


def _python() -> str:
    """pythonw.exe REAL (el de la instalación base), para que no se abra ninguna ventana.

    [Nau] El pythonw.exe del .venv es un lanzador de uv que arranca por debajo el
    python.exe con consola: cada tarea abría una ventana de Terminal (09/11/2026).
    tarea_claude.py solo usa la biblioteca estándar, así que no necesita el .venv.
    """
    base = Path(getattr(sys, "_base_executable", "") or sys.executable)
    for exe in (base.with_name("pythonw.exe"), Path(sys.executable).with_name("pythonw.exe")):
        if exe.exists():
            return str(exe)
    return sys.executable


@ToolRegistry.register(_TOOL_NAME)
class ClaudeCodeTaskTool(BaseTool):
    """Encarga a Claude Code una tarea en el PC del usuario, en segundo plano."""

    tool_id = _TOOL_NAME

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=_TOOL_NAME,
            description=(
                "Hace en segundo plano cualquier tarea que el usuario quiera "
                "que se HAGA en su ordenador o en sus cuentas: abrir o crear archivos y "
                "carpetas, ejecutar programas o comandos, organizar cosas, buscar en sus "
                "archivos, escribir o preparar correos y WhatsApp, Notion, Google Drive, "
                "Spotify, el navegador, proyectos de código, n8n, etc. Con ella tienes "
                "todas las herramientas, skills y conectores del usuario. Úsala siempre que el usuario "
                "pida una acción; no la uses para preguntas que puedes contestar tú. "
                "Escribe en 'tarea' la orden completa y clara, en español, con todo el "
                "contexto de la conversación que haga falta. Vuelve al momento: dile a "
                "El usuario en una frase corta que te pones con ello y que le avisarás al "
                "terminar. Si el usuario responde a una tarea anterior, pasa en 'sesion' el "
                "identificador de sesión que se te indicó. Pon 'confirmado' a true SOLO "
                "cuando el usuario acabe de decir claramente que sí a una confirmación pendiente."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tarea": {
                        "type": "string",
                        "description": "La orden completa y clara de lo que hay que hacer.",
                    },
                    "sesion": {
                        "type": "string",
                        "description": (
                            "Opcional: identificador de sesión de una tarea anterior "
                            "para continuarla (p. ej. cuando el usuario confirma)."
                        ),
                        "default": "",
                    },
                    "proyecto": {
                        "type": "string",
                        "description": (
                            "Opcional: proyecto (carpeta de claudeProjects) en el que hacer "
                            "la tarea, si el usuario lo nombra («en el proyecto de LinkedIn» → "
                            "linkedin). Se trabaja dentro de esa carpeta, con sus "
                            "instrucciones, memoria y skills."
                        ),
                        "default": "",
                    },
                    "confirmado": {
                        "type": "boolean",
                        "description": (
                            "true solo si el usuario acaba de decir que sí a una acción que "
                            "quedó pendiente de confirmación."
                        ),
                        "default": False,
                    },
                },
                "required": ["tarea"],
            },
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        tarea = str(params.get("tarea", "") or "").strip()[:_MAX_TASK_CHARS]
        if not tarea:
            return self._fail("Falta la orden en 'tarea'.")
        _TASKS_DIR.mkdir(parents=True, exist_ok=True)
        if _running_tasks() >= _MAX_RUNNING:
            return self._fail(
                f"Ya hay {_MAX_RUNNING} tareas en marcha. Dile al usuario que espere a que acabe alguna."
            )
        task_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        task = {
            "id": task_id,
            "tarea": tarea,
            "sesion_previa": str(params.get("sesion", "") or "").strip() or None,
            "confirmado": params.get("confirmado") is True,
            "proyecto": str(params.get("proyecto", "") or "").strip() or None,
            "estado": "pendiente",
            "creada": time.time(),
            "anunciado": False,
        }
        path = _TASKS_DIR / f"{task_id}.json"
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        flags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        try:
            subprocess.Popen(
                [_python(), str(_RUNNER), task_id],
                cwd=str(_ROOT), creationflags=flags, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.exception("No se pudo lanzar la tarea de Claude Code")
            path.unlink(missing_ok=True)
            return self._fail("No he podido ponerme con la tarea por un fallo técnico.")
        logger.info("Tarea %s encargada a Claude Code", task_id)
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"Tarea {task_id} en marcha en segundo plano. Habla en primera persona y no "
                "menciones nunca a Claude ni a Claude Code. Dile al usuario, "
                "en una frase corta, que te pones con ello y que le avisarás al terminar."
            ),
            success=True,
            metadata={"task_id": task_id},
        )

    def _fail(self, message: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=message, success=False)
