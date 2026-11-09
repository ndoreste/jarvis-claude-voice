"""[Nau] Herramienta de agenda: consulta Google Calendar en vivo (solo lectura).

Usa el token OAuth que guarda ``jarvis connect gcalendar`` y lo renueva solo
cuando caduca. Lee todos los calendarios visibles de la cuenta y devuelve los
eventos con la hora local del PC.
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_TOOL_NAME = "agenda_google"
_API = "https://www.googleapis.com/calendar/v3"
_MAX_DIAS = 31
_MAX_EVENTOS = 50
_DIAS_SEMANA = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _api_calendars(token: str) -> Dict[str, Any]:
    resp = httpx.get(
        f"{_API}/users/me/calendarList",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()


def _normalize(text: str) -> str:
    """Minúsculas y sin tildes, para buscar «dentista» o «Dentista» igual."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _matches(event: Dict[str, Any], query: str) -> bool:
    if not query:
        return True
    fields = (event.get(k, "") for k in ("summary", "description", "location"))
    haystack = _normalize(" ".join(fields))
    return all(word in haystack for word in _normalize(query).split())


def _api_events(
    token: str,
    calendar_id: str,
    time_min: str,
    time_max: str,
) -> Dict[str, Any]:
    # El filtro «q» de Google falla con tildes: se filtra aquí (_matches).
    params: Dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 250,
        "timeMin": time_min,
        "timeMax": time_max,
    }
    resp = httpx.get(
        f"{_API}/calendars/{quote(calendar_id, safe='')}/events",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()


def _credentials_path() -> str:
    from openjarvis.connectors.gcalendar import _DEFAULT_CREDENTIALS_PATH
    from openjarvis.connectors.oauth import resolve_google_credentials

    return resolve_google_credentials(_DEFAULT_CREDENTIALS_PATH)


def _event_start(event: Dict[str, Any]) -> datetime:
    start = event.get("start", {})
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"]).astimezone()
    return datetime.combine(date.fromisoformat(start["date"]), time()).astimezone()


def _format_event(event: Dict[str, Any], calendar_name: str) -> str:
    start = event.get("start", {})
    end = event.get("end", {})
    inicio = _event_start(event)
    dia = f"{_DIAS_SEMANA[inicio.weekday()]} {inicio:%d/%m}"
    if "dateTime" in start:
        fin = datetime.fromisoformat(end.get("dateTime", start["dateTime"]))
        horas = f"{inicio:%H:%M}-{fin.astimezone():%H:%M}"
    else:
        horas = "todo el día"
    partes = [f"{dia} {horas}", event.get("summary", "(sin título)")]
    if event.get("location"):
        partes.append(event["location"])
    partes.append(f"calendario: {calendar_name}")
    return " · ".join(partes)


def _collect_events(
    path: str, time_min: str, time_max: str, query: str
) -> List[tuple[datetime, str]]:
    from openjarvis.connectors.google_auth import call_with_refresh

    calendars = call_with_refresh(_api_calendars, path).get("items", [])
    found: List[tuple[datetime, str]] = []
    for cal in calendars:
        if cal.get("selected") is False:
            continue
        name = cal.get("summaryOverride") or cal.get("summary", cal.get("id", ""))
        try:
            data = call_with_refresh(
                _api_events, path, cal["id"], time_min, time_max
            )
        except httpx.HTTPStatusError as exc:
            # Un calendario que falle (borrado, sin permiso) no tumba el resto.
            logger.warning("Calendario %s omitido: %s", name, exc)
            continue
        for event in data.get("items", []):
            if event.get("status") == "cancelled" or not _matches(event, query):
                continue
            found.append((_event_start(event), _format_event(event, name)))
    found.sort(key=lambda item: item[0])
    return found


@ToolRegistry.register(_TOOL_NAME)
class GoogleAgendaTool(BaseTool):
    """Lee los eventos del Google Calendar del usuario en un rango de días."""

    tool_id = _TOOL_NAME

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=_TOOL_NAME,
            description=(
                "Consulta la agenda de Google Calendar del usuario (solo lectura). "
                "Úsala para preguntas como «¿qué tengo hoy?», «¿qué tengo "
                "mañana?», «¿cuándo es el dentista?» o «¿qué tengo esta semana?». "
                "Devuelve los eventos con día, hora local, título, lugar y "
                "calendario."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "desde": {
                        "type": "integer",
                        "description": (
                            "Primer día a consultar, contando desde hoy: "
                            "0 = hoy, 1 = mañana, -1 = ayer."
                        ),
                        "default": 0,
                    },
                    "dias": {
                        "type": "integer",
                        "description": (
                            "Cuántos días consultar a partir de 'desde' "
                            f"(1 = un solo día, 7 = una semana, máximo {_MAX_DIAS})."
                        ),
                        "default": 1,
                    },
                    "buscar": {
                        "type": "string",
                        "description": (
                            "Texto opcional para filtrar eventos (título, "
                            "descripción o lugar). Para buscar un evento concreto "
                            "sin fecha, usa dias=31."
                        ),
                        "default": "",
                    },
                },
                "required": [],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            desde = int(params.get("desde", 0) or 0)
            dias = max(1, min(int(params.get("dias", 1) or 1), _MAX_DIAS))
        except (TypeError, ValueError):
            return self._fail("Los parámetros 'desde' y 'dias' deben ser números.")
        query = str(params.get("buscar", "") or "").strip()

        hoy = datetime.combine(date.today(), time()).astimezone()
        inicio = hoy + timedelta(days=desde)
        fin = inicio + timedelta(days=dias)

        from openjarvis.connectors.google_auth import GoogleAuthError

        try:
            events = _collect_events(
                _credentials_path(), inicio.isoformat(), fin.isoformat(), query
            )
        except GoogleAuthError:
            # Permiso retirado o caducado (p. ej. revocado en la cuenta de Google).
            return self._fail(
                "El permiso de Google Calendar ha caducado. Dile al usuario que hay "
                "que renovarlo (pedírselo a Claude en la carpeta de Jarvis)."
            )
        except Exception as exc:  # noqa: BLE001 - se informa al modelo
            return self._fail(f"No pude leer Google Calendar: {exc}")

        rango = f"{inicio:%d/%m/%Y}" + (
            f" al {fin - timedelta(days=1):%d/%m/%Y}" if dias > 1 else ""
        )
        if not events:
            extra = f" que coincidan con «{query}»" if query else ""
            content = f"No hay eventos{extra} en la agenda ({rango})."
        else:
            lineas = [texto for _, texto in events[:_MAX_EVENTOS]]
            content = f"Agenda ({rango}):\n" + "\n".join(lineas)
        return ToolResult(
            tool_name=self.tool_id,
            content=content,
            success=True,
            metadata={"count": len(events)},
        )

    def _fail(self, message: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=message, success=False)


def agenda_text(desde: int = 0, dias: int = 1, buscar: Optional[str] = None) -> str:
    """Atajo para scripts: devuelve la agenda como texto."""
    return GoogleAgendaTool().execute(desde=desde, dias=dias, buscar=buscar or "").content
