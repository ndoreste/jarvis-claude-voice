"""[Nau] 27/11/2026: previsión del tiempo para el cerebro de voz (jarvis-herramienta tiempo).

Antes el cerebro buscaba el tiempo en la web: 24–32 s por pregunta (medido en la prueba del
27/11). Open-Meteo responde en <1 s y sin clave; es la misma fuente que usa el HUD. La ciudad
por defecto sale de data/usuario.toml ([ciudad]); otra ciudad se busca con su geocodificador.
Solo biblioteca estándar.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta

from ajustes import _SETTINGS

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT_S = 6
MAX_DAYS = 16  # lo que da Open-Meteo
DAY_NAMES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
# Códigos WMO de Open-Meteo → texto corto (como en el HUD, con nieve y llovizna helada)
WEATHER_CODES = {
    0: "despejado", 1: "casi despejado", 2: "nubes y claros", 3: "nublado", 45: "niebla", 48: "niebla",
    51: "llovizna", 53: "llovizna", 55: "llovizna", 56: "llovizna helada", 57: "llovizna helada",
    61: "lluvia débil", 63: "lluvia", 65: "lluvia fuerte", 66: "lluvia helada", 67: "lluvia helada",
    71: "nieve débil", 73: "nieve", 75: "nieve fuerte", 77: "granizo fino", 80: "chubascos",
    81: "chubascos", 82: "chubascos fuertes", 85: "chubascos de nieve", 86: "chubascos de nieve",
    95: "tormenta", 96: "tormenta con granizo", 99: "tormenta con granizo",
}


class WeatherError(RuntimeError):
    """No se ha podido leer el tiempo."""


def _get(url: str, params: dict) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(full, timeout=TIMEOUT_S) as resp:  # noqa: S310 - URL fija https
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise WeatherError(f"Open-Meteo no responde: {exc}") from exc


def _place(city: str) -> tuple[str, float, float]:
    """(nombre, latitud, longitud): la ciudad pedida o la de usuario.toml."""
    if not city.strip():
        home = _SETTINGS.get("ciudad") or {}
        if "latitud" not in home or "longitud" not in home:
            raise WeatherError("No hay ciudad configurada en usuario.toml: dime cuál.")
        return str(home.get("nombre") or "tu ciudad"), float(home["latitud"]), float(home["longitud"])
    found = _get(GEOCODING_URL, {"name": city.strip(), "count": 1, "language": "es"}).get("results")
    if not found:
        raise WeatherError(f"No encuentro la ciudad «{city}».")
    top = found[0]
    where = ", ".join(str(p) for p in (top.get("name"), top.get("country")) if p)
    return where, float(top["latitude"]), float(top["longitude"])


def forecast(city: str = "", start: int = 0, days: int = 1) -> str:
    """Texto breve con la previsión día a día (y el tiempo de ahora si incluye hoy)."""
    start = max(0, min(start, MAX_DAYS - 1))
    days = max(1, min(days, MAX_DAYS - start))
    name, lat, lon = _place(city)
    data = _get(FORECAST_URL, {
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "current": "temperature_2m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,wind_speed_10m_max",
        "forecast_days": start + days,
    })
    daily = data.get("daily") or {}
    lines = [f"Previsión para {name}:"]
    current = data.get("current") or {}
    if start == 0 and "temperature_2m" in current:
        lines.append(f"Ahora: {round(current['temperature_2m'])} °C, "
                     f"{WEATHER_CODES.get(int(current.get('weather_code', -1)), 'sin dato')}.")
    for i in range(start, min(start + days, len(daily.get("time") or []))):
        day = date.fromisoformat(daily["time"][i])
        label = "hoy" if day == date.today() else (
            "mañana" if day == date.today() + timedelta(days=1) else DAY_NAMES[day.weekday()])
        rain = daily.get("precipitation_probability_max", [None] * (i + 1))[i]
        lines.append(
            f"{label} {day:%d/%m}: {WEATHER_CODES.get(int(daily['weather_code'][i]), 'sin dato')}, "
            f"de {round(daily['temperature_2m_min'][i])} a {round(daily['temperature_2m_max'][i])} °C"
            + (f", lluvia {rain} %" if rain is not None else "")
            + f", viento hasta {round(daily['wind_speed_10m_max'][i])} km/h.")
    return "\n".join(lines)
