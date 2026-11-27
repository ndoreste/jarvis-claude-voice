"""[Nau] 27/11/2026: todas las acciones al momento de Jarvis, con su registro y su «deshaz».

dispatch(args) hace una acción y devuelve (resultado, qué, cómo deshacerla). Resultados:
    hecho      ya dijo «Enseguida, señor.»: no se añade nada
    decir      el texto se dice en voz alta («He mandado «x» a mi papelera.»)
    confirmar  el texto es una pregunta («¿Le mando a Diego: …?»); el envío espera al «sí»
               que comprueba el oyente (diario_acciones.take_pending)
Las de siempre (abrir, música, volumen, archivos…) siguen en acciones_pc.run. Las nuevas:
borrar, recuperar, deshacer, whatsapp, correo, agendar, desagendar, rutina, guardar_rutina.
"""

from __future__ import annotations

import argparse
import shlex
import time
from pathlib import Path

import acciones_pc as pc
import diario_acciones as diario
from acciones_pc import SpeakError

NEW_KINDS = {"borrar", "recuperar", "deshacer", "whatsapp", "correo", "agendar", "desagendar",
             "rutina", "guardar_rutina"}
READBACK_MAX_CHARS = 220  # lo que se lee en voz alta de un mensaje largo antes de enviarlo


def snapshot(args: argparse.Namespace) -> dict:
    """Lo que hay antes de la acción y hará falta para deshacerla."""
    before: dict = {}
    if args.tipo == "volumen":
        before["volumen"] = pc.volume_level()
    elif args.tipo == "copiar":
        before["portapapeles"] = pc.clipboard_text()
    elif args.tipo == "musica":
        before["lista"] = pc.current_list[0] if pc.current_list else ""
    elif args.tipo == "captura":
        before["t"] = time.time()
    pc.last_created.clear()
    pc.last_note.clear()
    return before


def inverse_for(args: argparse.Namespace, done: str, before: dict) -> dict | None:
    """Cómo deshacer una acción de acciones_pc.run (None si no se puede)."""
    kind, value = args.tipo, args.valor
    order = pc._plain(value)
    if kind == "abrir":
        if done.startswith("abierto la app "):
            return {"tipo": "cerrar", "valor": done.removeprefix("abierto la app ")}
        if done.startswith("abierta la carpeta "):
            return {"tipo": "cerrar", "valor": f"la carpeta {done.removeprefix('abierta la carpeta ')}"}
        if done == "abierto mi interfaz":
            return {"tipo": "cerrar", "valor": "interfaz"}
        return None  # una web o un marcador: la pestaña no se cierra sola
    if kind == "cerrar":
        return {"tipo": "abrir", "valor": "interfaz" if pc._plain(value) in pc.HUD_NAMES else value}
    if kind == "musica":
        if args.lista:
            previous = before.get("lista")
            return ({"tipo": "musica", "valor": "play", "lista": previous} if previous
                    else {"tipo": "musica", "valor": "pausa"})
        flips = {"siguiente": "anterior", "anterior": "siguiente", "pausa": "play", "play": "pausa"}
        key = next((k for k in flips if order.startswith(k[:4])), "play" if order in ("", "pon", "musica") else "")
        return {"tipo": "musica", "valor": flips[key]} if key else None
    if kind == "volumen" and before.get("volumen") is not None:
        return {"tipo": "volumen", "valor": str(round(before["volumen"] * 100))}
    if kind in ("carpeta", "archivo") and pc.last_created:
        return {"especial": "a_papelera", "ruta": str(pc.last_created[0])}
    if kind == "nota" and pc.last_note:
        return {"especial": "quitar_nota", "archivo": str(pc.last_note[0]), "linea": pc.last_note[1]}
    if kind == "copiar" and before.get("portapapeles"):
        return {"tipo": "copiar", "valor": before["portapapeles"]}
    if kind == "captura":
        return {"especial": "captura", "desde": before.get("t", time.time())}
    return None


def run_inverse(undo: dict) -> str:
    """Hace la vuelta atrás y dice qué ha hecho."""
    special = undo.get("especial")
    if special is None:
        done = pc.run(undo["tipo"], undo.get("valor", ""), playlist=undo.get("lista", ""))
        return done
    if special == "a_papelera":
        import papelera

        entry = papelera.trash(Path(undo["ruta"]))
        return f"«{entry['nombre']}» a mi papelera"
    if special == "recuperar":
        import papelera

        entry = papelera.restore(entry_id=undo["id"])[1]
        return f"«{Path(entry['origen']).name}» de vuelta en {Path(entry['origen']).parent.name}"
    if special == "quitar_nota":
        if not pc.remove_last_note(Path(undo["archivo"]), undo["linea"]):
            raise SpeakError("La nota ya no es la última: no la quito.")
        return "nota quitada"
    if special == "captura":
        shots = pc.known_folder("imagenes")
        newest = sorted((p for p in (shots / "Capturas de pantalla").glob("*.png")
                         if p.stat().st_mtime >= undo["desde"] - 1), key=lambda p: p.stat().st_mtime) if shots else []
        if not newest:
            raise SpeakError("No encuentro la captura.")
        import papelera

        papelera.trash(newest[-1])
        return "captura a mi papelera"
    if special == "quitar_evento":
        import google_voz

        google_voz.delete_event(undo["id"])
        return f"quitado «{undo.get('titulo', 'el evento')}» de tu calendario"
    if special == "crear_evento":
        import google_voz

        google_voz.recreate_event(undo["evento"])
        return f"vuelve a estar «{undo['evento'].get('summary', 'el evento')}» en tu calendario"
    raise SpeakError("Eso no sé deshacerlo.")


# ── Acciones nuevas ───────────────────────────────────────────────────────────

def _readback(text: str) -> str:
    text = " ".join(text.replace("\\n", " ").split())
    return text if len(text) <= READBACK_MAX_CHARS else text[:READBACK_MAX_CHARS].rsplit(" ", 1)[0] + "…"


def _whatsapp(args: argparse.Namespace, order: str) -> tuple[str, str, None]:
    import whatsapp_voz

    who, chat = whatsapp_voz.resolve(args.valor)
    message = args.texto.strip()
    if not message:
        raise SpeakError(f"¿Qué le digo a {who}?")

    def send() -> str:
        whatsapp_voz.send(chat, message)
        diario.record("whatsapp", f"accion whatsapp {shlex.quote(who)}", f"WhatsApp a {who}", order)
        return f"Enviado a {who}, señor."

    return "confirmar", diario.set_pending(f"¿Le mando a {who} por WhatsApp: «{_readback(message)}»?", send), None


def _email(args: argparse.Namespace, order: str) -> tuple[str, str, None]:
    import google_voz

    try:
        who, address = google_voz.find_email(args.valor)
    except google_voz.GoogleError as exc:
        raise SpeakError(str(exc)) from exc
    body = args.texto.strip()
    if not body:
        raise SpeakError(f"¿Qué le pongo en el correo a {who}?")
    subject = args.asunto.strip() or body.split(".")[0][:60]

    def send() -> str:
        google_voz.send_email(address, subject, body)
        diario.record("correo", f"accion correo {shlex.quote(who)}", f"correo a {who}", order)
        return f"Correo enviado a {who}, señor."

    question = f"¿Le mando un correo a {who}, asunto «{subject}»: «{_readback(body)}»?"
    return "confirmar", diario.set_pending(question, send), None


def _add_event(args: argparse.Namespace) -> tuple[str, str, dict]:
    import google_voz

    event = google_voz.add_event(args.valor, args.inicio, args.fin, args.lugar)
    when = google_voz.when_text(event.get("start", {}))
    return ("decir", f"Apuntado en tu calendario: «{event.get('summary', args.valor)}», {when}.",
            {"especial": "quitar_evento", "id": event.get("id", ""), "titulo": event.get("summary", "")})


def _remove_event(args: argparse.Namespace, order: str) -> tuple[str, str, None]:
    import google_voz

    found = google_voz.find_events(args.valor, args.dia)
    if not found:
        raise SpeakError(f"No encuentro «{args.valor}» en tu calendario.")
    if len(found) > 1:
        options = "; ".join(f"«{e.get('summary', '')}» {google_voz.when_text(e.get('start', {}))}"
                            for e in found[:3])
        raise SpeakError(f"Tengo varios: {options}. ¿Cuál quito?")
    event = found[0]
    title, when = event.get("summary", ""), google_voz.when_text(event.get("start", {}))

    def remove() -> str:
        google_voz.delete_event(event["id"])
        diario.record("desagendar", f"accion desagendar {shlex.quote(title)}", f"quitado «{title}» {when}",
                      order, {"especial": "crear_evento", "evento": event})
        return f"Quitado «{title}» de tu calendario, señor."

    return "confirmar", diario.set_pending(f"¿Quito «{title}», {when}, de tu calendario?", remove), None


def _routine(args: argparse.Namespace, order: str) -> tuple[str, str, None]:
    name, specs = diario.find_routine(args.valor)
    from herramientas_voz import parse_action

    done = 0
    for spec in specs:
        step = parse_action(shlex.split(spec))
        outcome, _detail = run(step, order=f"rutina {name}", spec=spec)
        done += outcome in ("hecho", "decir", "cerrado")
    return "decir", f"Rutina «{name}» lista: {done} de {len(specs)} acciones.", None


def dispatch(args: argparse.Namespace, order: str = "") -> tuple[str, str, dict | None]:
    """Hace la acción. (resultado, texto, cómo deshacerla o None)."""
    kind = args.tipo
    if kind not in NEW_KINDS:
        before = snapshot(args)
        done = pc.run(kind, args.valor, args.texto, args.en, args.abrir, args.lista)
        return "hecho", done, inverse_for(args, done, before)
    if kind == "deshacer":
        return "decir", diario.undo(run_inverse), None
    if kind == "borrar":
        import papelera

        said, entry = papelera.trash_by_name(args.valor, args.en)
        return "decir", said, {"especial": "recuperar", "id": entry["id"]}
    if kind == "recuperar":
        import papelera

        said, entry = papelera.restore(query=args.valor)
        return "decir", said, {"especial": "a_papelera", "ruta": entry["origen"]}
    if kind == "whatsapp":
        return _whatsapp(args, order)
    if kind == "correo":
        return _email(args, order)
    if kind == "agendar":
        return _add_event(args)
    if kind == "desagendar":
        return _remove_event(args, order)
    if kind == "rutina":
        return _routine(args, order)
    return "decir", diario.save_routine(args.valor), None


def run(args: argparse.Namespace, order: str = "", spec: str = "") -> tuple[str, str]:
    """Hace la acción y la apunta en el diario (si se ha hecho). Errores que se dicen en voz alta
    → ('decir', texto); los demás → ('fallo', detalle) para encargarla como tarea."""
    import google_voz

    try:
        outcome, text, undo = dispatch(args, order)
    except pc.NotOpenError as exc:
        return "cerrado", str(exc)
    except SpeakError as exc:
        return "decir", str(exc)
    except google_voz.GoogleError as exc:
        return "decir", str(exc)
    if outcome in ("hecho", "decir") and args.tipo not in ("deshacer", "rutina", "guardar_rutina"):
        diario.record(args.tipo, spec or f"accion {args.tipo} {shlex.quote(args.valor)}".strip(),
                      text, order, undo)
    return outcome, text
