"""[Nau] Rutas del HUD de Jarvis: datos del PC, tiempo y estado del oyente de voz.

La web (página HUD) las consulta cada poco:
  GET  /v1/hud/sistema   CPU, RAM, disco, batería, red, procesos, Wi-Fi, GPU, temperatura…
  GET  /v1/hud/equipo    ficha fija del equipo (nombre, SO, CPU, GPU, modelo)
  GET  /v1/hud/oyente    estado del oyente (en espera, escuchando, pensando, hablando)
  GET  /v1/hud/tiempo    tiempo en la ciudad de data/usuario.toml (Open-Meteo, sin clave)
  POST /v1/hud/energia   bloquear, reiniciar, apagar o cancelar (solo desde la web local)

Los datos lentos de leer (Wi-Fi, GPU, temperatura) los refresca un hilo cada 10 s.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import platform
import re
import socket
import subprocess
import threading
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import psutil
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.server.cpu_utility import CpuUtility

logger = logging.getLogger(__name__)

hud_router = APIRouter(prefix="/v1/hud", tags=["hud"])

DATA_DIR = Path(os.environ.get("OPENJARVIS_HOME") or Path.home() / ".openjarvis")
LISTENER_STATE_FILE = DATA_DIR / "escucha-estado.json"
LISTENER_HEARTBEAT_FILE = DATA_DIR / "escucha.heartbeat"
VOICE_MEMORY_FILE = DATA_DIR / "memoria-voz.jsonl"

SLOW_REFRESH_S = 10
LISTENER_ALIVE_S = 120          # marca de vida más antigua: oyente caído
LISTENER_STALE_STATE_S = 30     # un estado «activo» sin cambios en este tiempo vuelve a «en espera»
WEATHER_TTL_S = 15 * 60
# [Nau] Nombre y ciudad del usuario: data/usuario.toml (plantilla en config/usuario.example.toml)
def _user_settings() -> dict[str, Any]:
    try:
        with (DATA_DIR / "usuario.toml").open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


USER_SETTINGS = _user_settings()
CITY = USER_SETTINGS.get("ciudad") or {}
WEATHER_URL = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
    "latitude": CITY.get("latitud", 40.42),
    "longitude": CITY.get("longitud", -3.70),
    "current": "temperature_2m,weather_code,relative_humidity_2m",
    "timezone": CITY.get("zona_horaria", "auto"),
})
POWER_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}
POWER_DELAY_S = 30  # margen para cancelar un reinicio o apagado
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Códigos WMO de Open-Meteo → texto corto
WEATHER_CODES = {
    0: "Despejado", 1: "Casi despejado", 2: "Nubes y claros", 3: "Nublado", 45: "Niebla", 48: "Niebla",
    51: "Llovizna", 53: "Llovizna", 55: "Llovizna", 61: "Lluvia débil", 63: "Lluvia", 65: "Lluvia fuerte",
    80: "Chubascos", 81: "Chubascos", 82: "Chubascos fuertes", 95: "Tormenta", 96: "Tormenta", 99: "Tormenta",
}


def _powershell(command: str, timeout: float = 8) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW, check=False,
    )
    return result.stdout.strip()


# ── Datos lentos, refrescados en segundo plano ──────────────────────────────

class _SlowSampler:
    """Wi-Fi, uso de GPU y temperatura: tardan en leerse, así que se cachean."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {"wifi": None, "gpu": None, "temperatura": None}
        self._temp_supported = True
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def ensure_running(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="hud-sampler", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            self.data = {"wifi": self._wifi(), "gpu": self._gpu(), "temperatura": self._temperature()}
            time.sleep(SLOW_REFRESH_S)

    @staticmethod
    def _wifi() -> dict[str, Any] | None:
        try:
            out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                                 timeout=5, creationflags=NO_WINDOW, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        # «Señal» llega con la ñ mal descodificada (netsh escribe en la página de códigos OEM)
        signal = re.search(r"(?:Se\S{1,3}al|Signal)\s*:\s*(\d+)\s*%", out)
        ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.MULTILINE)
        if not signal:
            return None
        return {"senal": int(signal.group(1)), "red": ssid.group(1).strip() if ssid else None}

    @staticmethod
    def _gpu() -> float | None:
        try:
            out = _powershell(
                "(Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage' -ErrorAction Stop)"
                ".CounterSamples | Measure-Object CookedValue -Sum | Select-Object -ExpandProperty Sum"
            )
            return round(min(100.0, float(out.replace(",", "."))), 1) if out else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def _temperature(self) -> float | None:
        if not self._temp_supported:
            return None
        try:
            out = _powershell(
                "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |"
                " Select-Object -First 1 -ExpandProperty CurrentTemperature"
            )
            if not out:
                raise ValueError("sin dato")
            return round(float(out) / 10 - 273.15, 1)
        except (OSError, subprocess.SubprocessError, ValueError):
            self._temp_supported = False  # Windows no la da sin permisos: no se vuelve a intentar
            return None


_slow = _SlowSampler()
_cpu_utility = CpuUtility()
psutil.cpu_percent(interval=None)  # la primera lectura siempre es 0: se descarta aquí
_net_last: tuple[float, int, int] | None = None


def _network_rates() -> tuple[float, float]:
    """KB/s de subida y bajada desde la consulta anterior."""
    global _net_last
    counters = psutil.net_io_counters()
    now = time.monotonic()
    rates = (0.0, 0.0)
    if _net_last:
        elapsed = max(0.001, now - _net_last[0])
        rates = ((counters.bytes_sent - _net_last[1]) / 1024 / elapsed,
                 (counters.bytes_recv - _net_last[2]) / 1024 / elapsed)
    _net_last = (now, counters.bytes_sent, counters.bytes_recv)
    return round(rates[0], 1), round(rates[1], 1)


def _count_lines(path: Path) -> int:
    try:
        with path.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


@hud_router.get("/sistema")
async def system_metrics() -> dict[str, Any]:
    _slow.ensure_running()
    mem, swap = psutil.virtual_memory(), psutil.swap_memory()
    disk = psutil.disk_usage("C:\\")
    battery = psutil.sensors_battery()
    up_kbs, down_kbs = _network_rates()
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    # Igual que el Administrador de tareas; si PDH falla, el «tiempo de procesador» de psutil
    cpu, core_max = _cpu_utility.sample() or (sum(per_core) / max(1, len(per_core)), max(per_core, default=0.0))
    gb = 1024 ** 3
    return {
        "cpu": round(cpu, 1),
        "cpu_nucleo_max": round(core_max, 1),
        "ram": {"usado_gb": round(mem.used / gb, 1), "total_gb": round(mem.total / gb, 1), "pct": mem.percent},
        "swap_pct": swap.percent,
        "disco": {"usado_gb": round(disk.used / gb), "total_gb": round(disk.total / gb),
                  "libre_gb": round(disk.free / gb), "pct": disk.percent},
        "bateria": None if battery is None else {"pct": round(battery.percent), "cargando": battery.power_plugged},
        "red": {"subida_kbs": up_kbs, "bajada_kbs": down_kbs},
        "procesos": len(psutil.pids()),
        "arranque": psutil.boot_time(),
        "charlas_guardadas": _count_lines(VOICE_MEMORY_FILE),
        **_slow.data,
    }


_equipo_cache: dict[str, Any] | None = None


@hud_router.get("/equipo")
async def machine_info() -> dict[str, Any]:
    global _equipo_cache
    if _equipo_cache is None:
        try:
            raw = _powershell(
                "$cs = Get-CimInstance Win32_ComputerSystem; $os = Get-CimInstance Win32_OperatingSystem;"
                "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1;"
                "$gpu = (Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ' + ';"
                "[pscustomobject]@{ modelo = \"$($cs.Manufacturer) $($cs.Model)\"; so = \"$($os.Caption) $($os.Version)\";"
                " cpu = $cpu.Name.Trim(); nucleos = $cpu.NumberOfCores; gpu = $gpu } | ConvertTo-Json -Compress",
                timeout=15,
            )
            info = json.loads(raw) if raw else {}
        except (OSError, subprocess.SubprocessError, ValueError):
            info = {}
        info.setdefault("so", f"{platform.system()} {platform.version()}")
        info.update({"equipo": socket.gethostname(), "usuario": getpass.getuser(),
                     "nombre": USER_SETTINGS.get("nombre", ""),
                     "zona": CITY.get("zona", CITY.get("nombre", ""))})
        _equipo_cache = info
    return _equipo_cache


@hud_router.get("/oyente")
async def listener_state() -> dict[str, Any]:
    """Estado que escribe escucha.py en escucha-estado.json."""
    now = time.time()
    try:
        state = json.loads(LISTENER_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"estado": "idle", "texto": "", "ts": 0}
    try:
        alive = now - LISTENER_HEARTBEAT_FILE.stat().st_mtime < LISTENER_ALIVE_S
    except OSError:
        alive = False
    if state.get("estado") != "idle" and now - float(state.get("ts", 0)) > LISTENER_STALE_STATE_S:
        state["estado"] = "idle"  # el oyente se reinició a mitad de conversación
    state["activo"] = alive
    return state


_weather_cache: tuple[float, dict[str, Any]] | None = None


@hud_router.get("/tiempo")
async def weather() -> dict[str, Any]:
    global _weather_cache
    if _weather_cache and time.time() - _weather_cache[0] < WEATHER_TTL_S:
        return _weather_cache[1]
    try:
        with urllib.request.urlopen(WEATHER_URL, timeout=8) as resp:
            current = json.loads(resp.read())["current"]
        data = {
            "lugar": CITY.get("nombre", "Madrid"),
            "temperatura": round(current["temperature_2m"]),
            "humedad": current.get("relative_humidity_2m"),
            "cielo": WEATHER_CODES.get(int(current.get("weather_code", -1)), "—"),
        }
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("No se pudo leer el tiempo: %s", exc)
        if _weather_cache:
            return _weather_cache[1]
        raise HTTPException(status_code=503, detail="Tiempo no disponible") from exc
    _weather_cache = (time.time(), data)
    return data


class PowerRequest(BaseModel):
    accion: str
    confirmado: bool = False


POWER_COMMANDS = {
    "bloquear": ["rundll32.exe", "user32.dll,LockWorkStation"],
    "reiniciar": ["shutdown", "/r", "/t", str(POWER_DELAY_S), "/c", "Jarvis: reinicio en 30 s (cancelable)"],
    "apagar": ["shutdown", "/s", "/t", str(POWER_DELAY_S), "/c", "Jarvis: apagado en 30 s (cancelable)"],
    "cancelar": ["shutdown", "/a"],
}


@hud_router.post("/energia")
async def power_action(body: PowerRequest, request: Request) -> dict[str, Any]:
    """Bloquea, reinicia o apaga el PC. Solo desde la web local y con confirmación explícita.

    Exigir el origen de la web evita que otra página del navegador lo dispare.
    """
    if request.headers.get("origin") not in POWER_ORIGINS:
        raise HTTPException(status_code=403, detail="Solo desde la web local de Jarvis")
    command = POWER_COMMANDS.get(body.accion)
    if command is None:
        raise HTTPException(status_code=400, detail="Acción no válida")
    if body.accion in ("reiniciar", "apagar") and not body.confirmado:
        raise HTTPException(status_code=400, detail="Falta la confirmación")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW, check=False)
    logger.warning("HUD: acción de energía «%s» (código %s)", body.accion, result.returncode)
    if result.returncode != 0 and body.accion != "cancelar":
        raise HTTPException(status_code=500, detail=result.stderr.strip() or "No se pudo ejecutar")
    return {"ok": True, "accion": body.accion, "segundos": POWER_DELAY_S if body.accion in ("reiniciar", "apagar") else 0}
