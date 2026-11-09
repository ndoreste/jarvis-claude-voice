"""[Nau] Herramientas del cerebro de voz (Sonnet por la suscripción, ver cerebro_claude.py).

El cerebro solo puede ejecutar este script (a través de bin/jarvis-herramienta):
    jarvis-herramienta agenda [--desde N] [--dias N] [--buscar TEXTO]
    jarvis-herramienta encargar --tarea "orden" [--proyecto NOMBRE] [--sesion ID] [--confirmado]
Reutiliza las herramientas de OpenJarvis: agenda_google (solo lectura) y
encargar_a_claude (tareas del PC en segundo plano).
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="jarvis-herramienta")
    sub = parser.add_subparsers(dest="cmd", required=True)
    agenda = sub.add_parser("agenda", help="Eventos de Google Calendar (solo lectura)")
    agenda.add_argument("--desde", type=int, default=0, help="0 = hoy, 1 = mañana, -1 = ayer")
    agenda.add_argument("--dias", type=int, default=1, help="Cuántos días (máximo 31)")
    agenda.add_argument("--buscar", default="", help="Filtra por título, descripción o lugar")
    task = sub.add_parser("encargar", help="Encarga una tarea del PC a Claude Code")
    task.add_argument("--tarea", required=True)
    task.add_argument("--sesion", default="")
    task.add_argument("--confirmado", action="store_true")
    task.add_argument("--proyecto", default="", help="Carpeta de claudeProjects donde trabajar")
    args = parser.parse_args()

    if args.cmd == "agenda":
        from openjarvis.tools.google_calendar import GoogleAgendaTool

        result = GoogleAgendaTool().execute(desde=args.desde, dias=args.dias, buscar=args.buscar)
    else:
        from openjarvis.tools.claude_code import ClaudeCodeTaskTool

        result = ClaudeCodeTaskTool().execute(
            tarea=args.tarea, sesion=args.sesion, confirmado=args.confirmado,
            proyecto=args.proyecto,
        )
    print(result.content)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
