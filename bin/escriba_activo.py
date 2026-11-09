"""[Nau] ¿Está Escriba dictando ahora mismo?

Mientras el usuario dicta con Escriba, Jarvis no debe hacer caso de lo que oye (08/11/2026:
se activaba con un falso «Hey Jarvis» o al mencionar «Jarvis» en un dictado).

Escriba (app Tauri) enseña una ventanita flotante «Escriba» siempre encima mientras
graba, transcribe o revisa el texto, y la oculta al terminar. Su ventana principal
también se llama «Escriba», pero no está siempre encima: así se distinguen.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

ESCRIBA_EXE = "escriba.exe"
WINDOW_TITLE = "Escriba"
CHECK_EVERY_S = 0.3  # no se consulta Windows en cada trozo de audio
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]

_last_check = 0.0
_last_value = False


def _exe_name(pid: int) -> str:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value.rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(handle)


def _is_overlay(hwnd: int) -> bool:
    if not _user32.IsWindowVisible(hwnd):
        return False
    if not _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST:
        return False
    title = ctypes.create_unicode_buffer(64)
    _user32.GetWindowTextW(hwnd, title, 64)
    if title.value != WINDOW_TITLE:
        return False
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return _exe_name(pid.value) == ESCRIBA_EXE


def _scan() -> bool:
    found = False

    def visit(hwnd: int, _: int) -> bool:
        nonlocal found
        if _is_overlay(hwnd):
            found = True
            return False  # deja de enumerar
        return True

    _user32.EnumWindows(_EnumWindowsProc(visit), 0)
    return found


def escriba_dictando() -> bool:
    """True si la ventanita de grabación de Escriba está a la vista."""
    global _last_check, _last_value
    now = time.monotonic()
    if now - _last_check >= CHECK_EVERY_S:
        try:
            _last_value = _scan()
        except OSError:
            _last_value = False
        _last_check = now
    return _last_value


if __name__ == "__main__":
    print("Escriba dictando:", _scan())
