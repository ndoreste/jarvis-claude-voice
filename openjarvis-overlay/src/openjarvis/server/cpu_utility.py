"""[Nau] Uso de CPU medido igual que el Administrador de tareas de Windows.

El Administrador de tareas no usa «% de tiempo de procesador» (lo que da psutil), sino
«% de utilidad del procesador», que tiene en cuenta la frecuencia real (turbo). En
procesadores híbridos como el Core Ultra 7 255U la diferencia llega a ser del doble.
Se lee con la API PDH de Windows (nombres en inglés, válidos en cualquier idioma).
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
ERROR_SUCCESS = 0
MIN_INTERVAL_S = 1.0  # intervalo mínimo entre lecturas (con menos, el dato es ruido)
TOTAL_PATH = r"\Processor Information(_Total)\% Processor Utility"
CORES_PATH = r"\Processor Information(*)\% Processor Utility"


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _FmtValue)]


class CpuUtility:
    """Consulta PDH abierta una vez; cada `sample()` da el uso desde la lectura anterior."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ok = False
        self._last: tuple[float, tuple[float, float]] | None = None
        if sys.platform != "win32":
            return
        try:
            self._pdh = ctypes.WinDLL("pdh.dll")
            self._query = wintypes.HANDLE()
            self._total = wintypes.HANDLE()
            self._cores = wintypes.HANDLE()
            if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)) != ERROR_SUCCESS:
                return
            add = self._pdh.PdhAddEnglishCounterW
            if add(self._query, TOTAL_PATH, None, ctypes.byref(self._total)) != ERROR_SUCCESS:
                return
            if add(self._query, CORES_PATH, None, ctypes.byref(self._cores)) != ERROR_SUCCESS:
                return
            self._pdh.PdhCollectQueryData(self._query)  # la primera lectura solo sirve de referencia
            self._ok = True
        except OSError:
            self._ok = False

    def sample(self) -> tuple[float, float] | None:
        """(uso total, núcleo más cargado) en %, o None si PDH no está disponible."""
        if not self._ok:
            return None
        with self._lock:
            # Varias pestañas consultando a la vez: se reutiliza la última lectura reciente
            if self._last and time.monotonic() - self._last[0] < MIN_INTERVAL_S:
                return self._last[1]
            result = self._read()
            if result is not None:
                self._last = (time.monotonic(), result)
            return result

    def _read(self) -> tuple[float, float] | None:
        if self._pdh.PdhCollectQueryData(self._query) != ERROR_SUCCESS:
            return None
        value = _FmtValue()
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        if self._pdh.PdhGetFormattedCounterValue(self._total, fmt, None, ctypes.byref(value)) != ERROR_SUCCESS:
            return None
        total = value.doubleValue
        core_max = self._core_max(fmt)
        # Un núcleo en turbo pasa del 100 %; el Administrador de tareas lo deja en 100
        return min(100.0, total), min(100.0, core_max if core_max is not None else total)

    def _core_max(self, fmt: int) -> float | None:
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(self._cores, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if ctypes.c_uint32(status).value != PDH_MORE_DATA or not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        status = self._pdh.PdhGetFormattedCounterArrayW(self._cores, fmt, ctypes.byref(size), ctypes.byref(count), buf)
        if status != ERROR_SUCCESS:
            return None
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        # Solo núcleos sueltos («0,3»), sin los totales («_Total», «0,_Total»)
        values = [items[i].FmtValue.doubleValue for i in range(count.value)
                  if items[i].szName and "_Total" not in items[i].szName]
        return max(values, default=None)
