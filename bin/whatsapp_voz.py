"""[Nau] 27/11/2026: WhatsApp al momento para Jarvis («mándale un WhatsApp a Diego: …»).

Habla directamente con el servidor de WhatsApp de Nau (whatsapp-mcp-ts, MCP por HTTP en
127.0.0.1:8766), sin tarea de Claude Code: buscar y enviar tarda <1 s.
El destinatario sale primero de la nota de contactos de la bóveda (contactos_whatsapp en
data/usuario.toml: cómo lo dice Nau, variantes de la voz y su chat) y, si no está, de los
contactos de WhatsApp. Si hay varios parecidos, pregunta a cuál en vez de adivinar.
El envío lo confirma siempre Nau por voz (diario_acciones.set_pending).
Solo biblioteca estándar.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from acciones_pc import SpeakError, _plain, _similarity

MCP_URL = "http://127.0.0.1:8766/mcp"
TIMEOUT_S = 15
NAME_MATCH = 0.8
COUNTRY_CODE = "34"  # números de 9 cifras de la nota (España)
_ids = [0]


def _call(tool: str, arguments: dict) -> str:
    """Llama a una herramienta del MCP de WhatsApp y devuelve su texto."""
    _ids[0] += 1
    body = {"jsonrpc": "2.0", "id": _ids[0], "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}
    request = urllib.request.Request(MCP_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode()
    except (urllib.error.URLError, OSError) as exc:
        raise SpeakError("WhatsApp no está conectado en el ordenador ahora mismo.") from exc
    if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
        raw = next((line[5:] for line in raw.splitlines() if line.startswith("data:")), "{}")
    reply = json.loads(raw)
    result = reply.get("result") or {}
    text = "".join(c.get("text", "") for c in result.get("content", []))
    if result.get("isError") or "error" in reply:
        raise SpeakError(f"WhatsApp no ha podido: {text or reply.get('error')}"[:200])
    return text


def _note_contacts() -> list[tuple[list[str], str, str]]:
    """(cómo lo dice Nau y variantes, nombre en WhatsApp, chat) de la nota de la bóveda."""
    try:
        from ajustes import CONTACTOS_WHATSAPP

        text = Path(CONTACTOS_WHATSAPP).read_text(encoding="utf-8") if CONTACTOS_WHATSAPP else ""
    except (ImportError, OSError):
        return []
    contacts = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0] or set(cells[0]) <= set("-: ") or cells[0].startswith("Cómo"):
            continue
        said = [s.strip() for s in cells[0].split(",") if s.strip()]
        chat = next(iter(re.findall(r"`([^`]+@(?:lid|s\.whatsapp\.net|g\.us))`", line)), "")
        if not chat:
            digits = re.sub(r"\D", "", cells[2])
            if len(digits) == 9:
                digits = COUNTRY_CODE + digits
            chat = f"{digits}@s.whatsapp.net" if len(digits) >= 10 else ""
        if said and chat:
            contacts.append((said + [cells[1]], cells[1] or said[0], chat))
    return contacts


def resolve(name: str) -> tuple[str, str]:
    """(nombre para decir, chat de WhatsApp) de a quién mandar. SpeakError si no está claro."""
    name = name.strip().strip("«»\"'")
    if not name:
        raise SpeakError("¿A quién se lo mando, señor?")
    best = max(((max(_similarity(name, s) for s in said), shown, chat)
                for said, shown, chat in _note_contacts()), default=(0.0, "", ""))
    if best[0] >= NAME_MATCH:
        return best[1], best[2]
    try:
        found = json.loads(_call("search_contacts", {"query": name}) or "[]")
    except ValueError:
        found = []
    if not found and " " in name:  # «Diego Trocoli» → prueba con el nombre de pila
        found = json.loads(_call("search_contacts", {"query": name.split()[0]}) or "[]")
    scored = sorted(((_similarity(name, c.get("name", "")), c) for c in found),
                    key=lambda t: t[0], reverse=True)
    if not scored or scored[0][0] < 0.5:
        raise SpeakError(f"No tengo a «{name}» en WhatsApp.")
    top = scored[0][0]
    names = sorted({_plain(c.get("name", "")): c.get("name", "") for s, c in scored if s >= top - 0.05}.values())
    if len(names) > 1:
        raise SpeakError(f"Tengo a varios: {', '.join(names[:4])}. ¿A cuál?")
    same = [c for s, c in scored if s >= top - 0.05]
    chat = next((c["jid"] for c in same if c["jid"].endswith("@s.whatsapp.net")), same[0]["jid"])
    return names[0], chat


def send(chat: str, text: str) -> None:
    if not text.strip():
        raise SpeakError("¿Qué le digo?")
    _call("send_message", {"recipient": chat, "message": text.replace("\\n", "\n")})
