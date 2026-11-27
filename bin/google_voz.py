"""[Nau] 27/11/2026: Google al momento para Jarvis: agenda (añadir y quitar), correo y contactos.

Usa el mismo proyecto de Google Cloud «Jarvis» que la agenda de solo lectura
(data/connectors/google.json). Hace falta aceptar una vez los permisos nuevos:
    src\\.venv\\Scripts\\python.exe bin\\google_voz.py autorizar
(abre la ventana de Google; el token nuevo vale también para la agenda de lectura).
Solo biblioteca estándar.
"""

from __future__ import annotations

import base64
import http.server
import json
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from ajustes import DATA_DIR

TOKEN_FILES = ("google.json", "gcalendar.json")
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
PEOPLE = "https://people.googleapis.com/v1"
REDIRECT_PORT = 8789
SCOPES = [
    "openid", "email",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/contacts.other.readonly",
]
TIMEOUT_S = 10
DAY_NAMES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


class GoogleError(RuntimeError):
    """Google no ha dejado hacerlo (sin permiso, sin red…)."""


# ── Token ─────────────────────────────────────────────────────────────────────

def _token_path() -> Path:
    return DATA_DIR / "connectors" / TOKEN_FILES[0]


def _tokens() -> dict:
    try:
        return json.loads(_token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoogleError("No tengo la cuenta de Google conectada.") from exc


def _save_tokens(tokens: dict) -> None:
    for name in TOKEN_FILES:
        path = DATA_DIR / "connectors" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        raise GoogleError(f"Google rechaza el permiso ({exc.code}): {exc.read()[:200]!r}") from exc
    except OSError as exc:
        raise GoogleError(f"Sin conexión con Google: {exc}") from exc


def _refresh() -> str:
    tokens = _tokens()
    fresh = _post_form(TOKEN_URL, {"client_id": tokens["client_id"], "client_secret": tokens["client_secret"],
                                   "refresh_token": tokens["refresh_token"], "grant_type": "refresh_token"})
    tokens = {**tokens, "access_token": fresh["access_token"], "token": fresh["access_token"]}
    _save_tokens(tokens)
    return fresh["access_token"]


def api(method: str, url: str, body: dict | None = None, retry: bool = True) -> dict:
    """Llamada a una API de Google con el token de Jarvis (lo renueva si ha caducado)."""
    token = _tokens().get("access_token", "")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and retry:
            _refresh()
            return api(method, url, body, retry=False)
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code == 403 and "insufficient" in detail.lower():
            raise GoogleError("Me falta el permiso de Google para eso: hay que autorizarlo otra vez.") from exc
        if exc.code == 403 and ("has not been used" in detail or "disabled" in detail):
            raise GoogleError("Esa API de Google no está activada en el proyecto Jarvis.") from exc
        raise GoogleError(f"Google ha devuelto un error {exc.code}.") from exc
    except OSError as exc:
        raise GoogleError(f"Sin conexión con Google: {exc}") from exc


# ── Autorización (una vez) ────────────────────────────────────────────────────

def authorize() -> str:
    """Abre la ventana de permisos de Google y guarda el token nuevo."""
    tokens = _tokens()
    state = secrets.token_urlsafe(16)
    redirect = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - nombre de http.server
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if query.get("state") == state:
                got.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in got
            self.wfile.write(("<h2>Listo: ya puedes cerrar esta pestaña.</h2>" if ok
                              else "<h2>No se ha concedido el permiso.</h2>").encode())

        def log_message(self, *_args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    params = {"client_id": tokens["client_id"], "redirect_uri": redirect, "response_type": "code",
              "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state}
    webbrowser.open(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")
    for _ in range(600):  # hasta 5 min
        if got:
            break
        threading.Event().wait(0.5)
    server.server_close()
    if "code" not in got:
        raise GoogleError("No se concedió el permiso de Google.")
    fresh = _post_form(TOKEN_URL, {"client_id": tokens["client_id"], "client_secret": tokens["client_secret"],
                                   "code": got["code"], "redirect_uri": redirect,
                                   "grant_type": "authorization_code"})
    _save_tokens({**tokens, "access_token": fresh["access_token"], "token": fresh["access_token"],
                  "refresh_token": fresh.get("refresh_token", tokens.get("refresh_token")),
                  "scope": fresh.get("scope", "")})
    return fresh.get("scope", "")


# ── Agenda ────────────────────────────────────────────────────────────────────

def _local(value: str) -> datetime:
    """'2026-11-28T17:00' (hora de aquí) → datetime con zona."""
    try:
        return datetime.fromisoformat(value.strip()).astimezone()
    except ValueError as exc:
        raise GoogleError(f"No entiendo la fecha «{value}».") from exc


def when_text(start: dict) -> str:
    """«el viernes 28 a las 17:00» (o «el viernes 28, todo el día»)."""
    if "dateTime" in start:
        moment = datetime.fromisoformat(start["dateTime"]).astimezone()
        return f"el {DAY_NAMES[moment.weekday()]} {moment.day} a las {moment:%H:%M}"
    day = datetime.fromisoformat(start["date"])
    return f"el {DAY_NAMES[day.weekday()]} {day.day}, todo el día"


def add_event(title: str, start: str, end: str = "", place: str = "") -> dict:
    """Crea un evento. Sin hora (solo '2026-11-28') es de todo el día; sin fin, dura 1 hora."""
    if not title.strip() or not start.strip():
        raise GoogleError("Falta el título o la fecha del evento.")
    body: dict = {"summary": title.strip()}
    if place.strip():
        body["location"] = place.strip()
    if "T" not in start:
        first = datetime.fromisoformat(start.strip()).date()
        last = datetime.fromisoformat(end.strip()).date() if end.strip() else first
        body["start"] = {"date": first.isoformat()}
        body["end"] = {"date": (last + timedelta(days=1)).isoformat()}
    else:
        begin = _local(start)
        finish = _local(end) if end.strip() else begin + timedelta(hours=1)
        body["start"] = {"dateTime": begin.isoformat()}
        body["end"] = {"dateTime": finish.isoformat()}
    return api("POST", CALENDAR, body)


def find_events(text: str, day: str = "", days: int = 14) -> list[dict]:
    """Eventos cuyo título se parece a «text», ese día o en los próximos días."""
    from acciones_pc import _similarity

    first = datetime.fromisoformat(day).astimezone() if day.strip() else datetime.now().astimezone()
    first = first.replace(hour=0, minute=0, second=0, microsecond=0)
    span = 1 if day.strip() else days
    query = urllib.parse.urlencode({"timeMin": first.isoformat(),
                                    "timeMax": (first + timedelta(days=span)).isoformat(),
                                    "singleEvents": "true", "orderBy": "startTime", "maxResults": 50})
    items = api("GET", f"{CALENDAR}?{query}").get("items", [])
    scored = [(_similarity(text, e.get("summary", "")), e) for e in items]
    best = max((s for s, _e in scored), default=0.0)
    return [e for s, e in scored if s >= max(0.6, best - 0.05)] if text.strip() else items


def delete_event(event_id: str) -> None:
    api("DELETE", f"{CALENDAR}/{urllib.parse.quote(event_id)}")


def recreate_event(event: dict) -> dict:
    """Para «deshaz» tras quitar un evento: lo vuelve a crear igual."""
    keep = {k: event[k] for k in ("summary", "description", "location", "start", "end") if k in event}
    return api("POST", CALENDAR, keep)


# ── Correo ────────────────────────────────────────────────────────────────────

EMAIL_MATCH = 0.75
CONTACTS_TTL_S = 10 * 60
_contacts_cache: list = []  # [momento, [(nombre, correo)]]


def _email_contacts() -> list[tuple[str, str]]:
    """(nombre, correo) de los contactos guardados y de «otros contactos» (gente a la que ha
    escrito). Muchos de estos últimos no tienen nombre: se usa lo de antes de la @."""
    import time

    if _contacts_cache and time.monotonic() - _contacts_cache[0] < CONTACTS_TTL_S:
        return _contacts_cache[1]
    people: dict[str, str] = {}
    for url, key in ((f"{PEOPLE}/people/me/connections?personFields=names,emailAddresses&pageSize=1000",
                      "connections"),
                     (f"{PEOPLE}/otherContacts?readMask=names,emailAddresses&pageSize=1000", "otherContacts")):
        for person in api("GET", url).get(key, []):
            names = [n.get("displayName") for n in person.get("names", []) if n.get("displayName")]
            for email in (e.get("value") for e in person.get("emailAddresses", []) if e.get("value")):
                people.setdefault(email.lower(), names[0] if names else "")
    contacts = [(shown or email.split("@")[0], email) for email, shown in people.items()]
    _contacts_cache[:] = [time.monotonic(), contacts]
    return contacts


def find_email(name: str) -> tuple[str, str]:
    """(nombre, correo) de un contacto de Google. GoogleError si no hay o hay varios distintos."""
    import re

    from acciones_pc import _similarity

    if "@" in name:
        return name.strip(), name.strip()

    def score(shown: str, email: str) -> float:
        local = re.sub(r"[._\-+\d]+", " ", email.split("@")[0])
        return max(_similarity(name, shown), _similarity(name, local))

    ranked = sorted(((score(s, e), s, e) for s, e in _email_contacts()), reverse=True)
    if not ranked or ranked[0][0] < EMAIL_MATCH:
        raise GoogleError(f"No encuentro el correo de «{name}» en tus contactos.")
    top = ranked[0][0]
    close = {e: s for sc, s, e in ranked if sc >= top - 0.05}
    if len(close) > 1:
        raise GoogleError(f"Tengo a varios: {', '.join(sorted(set(close.values()))[:4])}. ¿A cuál?")
    return ranked[0][1], ranked[0][2]


def send_email(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject or "(sin asunto)"
    message.set_content(body.replace("\\n", "\n"))
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    api("POST", GMAIL_SEND, {"raw": raw})


if __name__ == "__main__" and sys.argv[1:2] == ["autorizar"]:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        print("Permisos concedidos:", authorize())
    except GoogleError as exc:
        print(exc)
        sys.exit(1)
