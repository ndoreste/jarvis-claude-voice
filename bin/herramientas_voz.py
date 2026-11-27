"""[Nau] Herramientas del cerebro de voz (Sonnet por la suscripción, ver cerebro_claude.py).

El cerebro solo puede ejecutar este script (a través de bin/jarvis-herramienta):
    jarvis-herramienta agenda [--desde N] [--dias N] [--buscar TEXTO]
    jarvis-herramienta encargar --tarea "orden" [--proyecto NOMBRE] [--sesion ID] [--confirmado]
    jarvis-herramienta accion abrir|musica|volumen|bloquear [VALOR]   (al momento, acciones_pc.py)
Reutiliza las herramientas de OpenJarvis: agenda_google (solo lectura) y
encargar_a_claude (tareas del PC en segundo plano).
"""

from __future__ import annotations

import argparse
import sys


def _log_action(kind: str, outcome: str) -> None:
    """[Nau] 26/11/2026: data/acciones.log, para comprobar qué hizo cada acción (son órdenes
    dirigidas a Jarvis; no se apunta el texto de archivos ni notas, solo el resultado)."""
    from datetime import datetime
    from pathlib import Path

    log_file = Path(__file__).resolve().parent.parent / "data" / "acciones.log"
    try:
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {kind}: {outcome}\n")
    except OSError:
        pass


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
    # [Nau] 26/11/2026: acciones instantáneas (acciones_pc.py), sin tarea de Claude Code
    action = sub.add_parser("accion", help="Abrir apps o webs, música, volumen, bloquear")
    action.add_argument("tipo", choices=["abrir", "cerrar", "musica", "volumen", "bloquear",
                                         "carpeta", "archivo", "nota", "buscar", "reciente",
                                         "captura", "copiar"])
    action.add_argument("valor", nargs="?", default="")
    action.add_argument("--texto", default="", help="Contenido del archivo o texto a copiar")
    action.add_argument("--en", default="", help="Carpeta: escritorio, descargas, un proyecto…")
    action.add_argument("--abrir", action="store_true", help="Abrir el archivo al crearlo")
    args = parser.parse_args()

    if args.cmd == "accion":
        from acciones_pc import ActionError, NotOpenError, run

        try:
            done = run(args.tipo, args.valor, args.texto, args.en, args.abrir)
            _log_action(args.tipo, f"hecho: {done}")
            print(f"Hecho: {done}. Ya has dicho «Enseguida, señor.»: "
                  "no digas nada más.")
            return 0
        except NotOpenError as exc:
            # [Nau] 26/11/2026: nada que cerrar → se dice al momento (antes, tarea de ~15 s)
            _log_action(args.tipo, f"no estaba abierto: {exc}")
            print(f"No hacía falta: {exc}. NO lo encargues. Dile solo, muy corto, que ya "
                  "estaba cerrado (p. ej. «Ya estaba cerrado, señor.»).")
            return 1
        except Exception as exc:  # noqa: BLE001 - cualquier fallo: que lo encargue como tarea
            detail = str(exc) if isinstance(exc, ActionError) else "fallo técnico"
            _log_action(args.tipo, f"fallo: {exc!r}"[:300])
            print(f"No se ha podido al momento ({detail}). Encárgalo ahora con «encargar» "
                  "(misma orden, más clara) sin volver a decir «Enseguida, señor.».")
            return 1
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
