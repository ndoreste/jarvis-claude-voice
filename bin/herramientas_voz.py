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


def run_action(args: argparse.Namespace, order: str = "", spec: str = "") -> tuple[str, str]:
    """Ejecuta una acción al momento. Devuelve (resultado, texto):
    'hecho' (ya dijo «Enseguida, señor.»), 'decir' (decir el texto), 'confirmar' (el texto es la
    pregunta antes de enviar), 'cerrado' (no había nada que cerrar) o 'fallo' (encargarla como
    tarea). La usa el cerebro con las marcas [[accion …]] (cerebro_claude.py), sin pasar por Bash;
    el registro y el «deshaz» van en acciones_voz.py."""
    from acciones_pc import ActionError
    from acciones_voz import run

    try:
        outcome, text = run(args, order, spec)
    except Exception as exc:  # noqa: BLE001 - cualquier otro fallo: se encarga como tarea
        _log_action(args.tipo, f"fallo: {exc!r}"[:300])
        return "fallo", str(exc) if isinstance(exc, ActionError) else "fallo técnico"
    # [Nau] 26/11/2026: «cerrado» = nada que cerrar → se dice al momento (antes, tarea de ~15 s)
    _log_action(args.tipo, f"{outcome}: {text}"[:300] if args.tipo not in ("whatsapp", "correo")
                else outcome)
    return outcome, text


def parse_action(argv: list[str]) -> argparse.Namespace:
    """Lee «accion tipo valor --texto … --en … --abrir». ValueError si no se entiende."""
    import contextlib
    import io

    with contextlib.redirect_stderr(io.StringIO()):
        try:
            args = build_parser().parse_args(argv)
        except SystemExit as exc:
            raise ValueError(f"acción mal escrita: {' '.join(argv)[:120]}") from exc
    if args.cmd != "accion":
        raise ValueError("solo se pueden marcar acciones")
    return args


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "accion":
        import acciones_pc

        acciones_pc.BACKGROUND_OK = False  # este proceso acaba al volver: nada en hilos
        outcome, detail = run_action(args)
        if outcome == "hecho":
            print(f"Hecho: {detail}. Ya has dicho «Enseguida, señor.»: no digas nada más.")
            return 0
        if outcome == "cerrado":
            print(f"No hacía falta: {detail}. NO lo encargues. Dile solo, muy corto, que ya "
                  "estaba cerrado (p. ej. «Ya estaba cerrado, señor.»).")
            return 1
        if outcome == "decir":
            print(f"Dile exactamente: {detail}")
            return 0
        if outcome == "confirmar":
            print("Enviar o quitar algo necesita el «sí» de voz: desde aquí no se hace.")
            return 1
        print(f"No se ha podido al momento ({detail}). Encárgalo ahora con «encargar» "
              "(misma orden, más clara) sin volver a decir «Enseguida, señor.».")
        return 1
    if args.cmd == "tiempo":
        from tiempo_voz import WeatherError, forecast

        try:
            print(forecast(args.ciudad, args.desde, args.dias))
            return 0
        except WeatherError as exc:
            print(f"No he podido mirar el tiempo: {exc}")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis-herramienta")
    sub = parser.add_subparsers(dest="cmd", required=True)
    agenda = sub.add_parser("agenda", help="Eventos de Google Calendar (solo lectura)")
    agenda.add_argument("--desde", type=int, default=0, help="0 = hoy, 1 = mañana, -1 = ayer")
    agenda.add_argument("--dias", type=int, default=1, help="Cuántos días (máximo 31)")
    agenda.add_argument("--buscar", default="", help="Filtra por título, descripción o lugar")
    # [Nau] 27/11/2026: el tiempo por Open-Meteo (~1 s) en vez de buscarlo en la web (24–32 s)
    weather = sub.add_parser("tiempo", help="Previsión del tiempo (Open-Meteo)")
    weather.add_argument("--ciudad", default="", help="Sin ciudad: la de usuario.toml")
    weather.add_argument("--desde", type=int, default=0, help="0 = hoy, 1 = mañana")
    weather.add_argument("--dias", type=int, default=1, help="Cuántos días (máximo 16)")
    task = sub.add_parser("encargar", help="Encarga una tarea del PC a Claude Code")
    task.add_argument("--tarea", required=True)
    task.add_argument("--sesion", default="")
    task.add_argument("--confirmado", action="store_true")
    task.add_argument("--proyecto", default="", help="Carpeta de claudeProjects donde trabajar")
    # [Nau] 26/11/2026: acciones instantáneas (acciones_pc.py), sin tarea de Claude Code
    action = sub.add_parser("accion", help="Abrir apps o webs, música, volumen, bloquear")
    action.add_argument("tipo", choices=["abrir", "cerrar", "musica", "volumen", "bloquear",
                                         "carpeta", "archivo", "nota", "buscar", "reciente",
                                         "captura", "copiar",
                                         # [Nau] 27/11/2026 (acciones_voz.py)
                                         "borrar", "recuperar", "deshacer", "whatsapp", "correo",
                                         "agendar", "desagendar", "rutina", "guardar_rutina"])
    action.add_argument("valor", nargs="?", default="")
    action.add_argument("--texto", default="", help="Contenido del archivo o texto a copiar")
    action.add_argument("--en", default="", help="Carpeta: escritorio, descargas, un proyecto…")
    action.add_argument("--abrir", action="store_true", help="Abrir el archivo al crearlo")
    action.add_argument("--lista", default="", help="Lista o canción de Spotify por su nombre")
    action.add_argument("--asunto", default="", help="Asunto del correo")
    action.add_argument("--inicio", default="", help="Evento: 2026-11-28T17:00 (o solo la fecha)")
    action.add_argument("--fin", default="", help="Evento: fin (sin él, dura 1 hora)")
    action.add_argument("--lugar", default="", help="Evento: lugar")
    action.add_argument("--dia", default="", help="Quitar evento: el día (2026-11-28)")
    return parser


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
