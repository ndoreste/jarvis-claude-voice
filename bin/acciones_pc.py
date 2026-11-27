"""[Nau] 26/11/2026: acciones instantáneas del PC para el cerebro de voz (~1 s, sin tarea).

Antes, «abre Spotify» lanzaba una tarea completa de Claude Code (15–20 s). Ahora lo simple va
directo (jarvis-herramienta accion …):
    abrir "Spotify"        app del menú Inicio, marcador de Chrome, web, carpeta o la interfaz
    cerrar "Spotify"       un programa entero (como su X)
    carpeta "nombre" [--en escritorio]        crea una carpeta (por defecto Documentos\\Jarvis)
    archivo "x.py" --texto "…" [--en …] [--abrir]   crea un archivo o script (nunca sobrescribe)
    nota "texto"           apunta una nota en Documentos\\Jarvis\\Notas.md
    buscar "nombre"        busca un archivo por su nombre y lo abre
    reciente foto|video|documento|pdf|audio [--en carpeta]   abre lo último guardado
    captura                captura de pantalla (Imágenes\\Capturas de pantalla)
    copiar "texto"         al portapapeles
    musica play|pausa|siguiente|anterior
    volumen subir|bajar|silencio|activar|<0-100>
    bloquear
Además, el oyente usa duck_media()/restore_media() para bajar la música mientras le hablas.

Nada personal: las apps salen del menú Inicio (Get-StartApps) y los marcadores del perfil de
Chrome en uso. Solo biblioteca estándar, salvo el volumen (pycaw, cargado al usarlo).
"""

from __future__ import annotations

import ctypes
import difflib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import uuid
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS_CACHE = ROOT / "data" / "apps-inicio.json"
DUCK_FILE = ROOT / "data" / "volumen-musica.json"
APPS_MAX_AGE_S = 24 * 3600
CHROME_DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200 | NO_WINDOW  # DETACHED_PROCESS | NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000  # fuera del Job Object del cerebro (si no, muere con él)

APP_MATCH = 0.85      # parecido mínimo para abrir una app sin dudar
BOOKMARK_MATCH = 0.8
APP_MATCH_LOOSE = 0.7  # si no hay marcador ni web, se acepta una app algo menos parecida
# Webs públicas habituales (para cuando no están en los marcadores)
KNOWN_SITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "google calendar": "https://calendar.google.com", "calendario": "https://calendar.google.com",
    "whatsapp web": "https://web.whatsapp.com", "github": "https://github.com",
    "netflix": "https://www.netflix.com", "google": "https://www.google.com",
    "linkedin": "https://www.linkedin.com", "chatgpt": "https://chatgpt.com",
    "twitter": "https://x.com", "x": "https://x.com", "amazon": "https://www.amazon.es",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com",
}
# Carpetas de Windows (SHGetKnownFolderPath): siguen a OneDrive si las tiene redirigidas
KNOWN_FOLDERS = {
    "descargas": "374DE290-123F-4565-9164-39C4925E467B",
    "documentos": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "escritorio": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "imagenes": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "musica": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
KNOWN_FOLDERS.update({f"mis {k}": v for k, v in list(KNOWN_FOLDERS.items())})
KNOWN_FOLDERS.update({f"carpeta de {k}": v for k, v in list(KNOWN_FOLDERS.items())
                      if not k.startswith("mis ")})
KNOWN_FOLDERS.update({"downloads": KNOWN_FOLDERS["descargas"], "documents": KNOWN_FOLDERS["documentos"],
                      "desktop": KNOWN_FOLDERS["escritorio"]})
NOTES_FILE = "Notas.md"
# «La última foto / el último vídeo / el último documento»: extensiones y carpeta por defecto
FILE_KINDS = {
    "foto": (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp"),
    "video": (".mp4", ".mov", ".mkv", ".avi", ".webm"),
    "documento": (".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".pptx", ".odt"),
    "audio": (".mp3", ".wav", ".m4a", ".flac", ".ogg"),
}
FILE_KINDS.update({"imagen": FILE_KINDS["foto"], "captura": (".png", ".jpg"),
                   "pdf": (".pdf",), "cancion": FILE_KINDS["audio"]})
DEFAULT_FOLDER_FOR_KIND = {"foto": "imagenes", "imagen": "imagenes", "captura": "imagenes",
                           "video": "videos", "audio": "musica", "cancion": "musica"}
SEARCH_FOLDERS = ("escritorio", "documentos", "descargas", "imagenes", "videos", "musica")
SEARCH_SKIP = {"node_modules", "__pycache__", "venv", "AppData", "site-packages", "dist", "build"}
SEARCH_MAX_S = 4.0
FILE_MATCH = 0.75
# La web del propio Jarvis (HUD, puerto 5173 de jarvis-arranque)
HUD_URL = "http://localhost:5173"
HUD_TITLES = {"OpenJarvis", "Jarvis"}  # título de su ventana propia (modo app de Chrome)
HUD_WAIT_S = 6.0  # lo que se espera a que aparezca su ventana para ponerla a pantalla completa
HUD_NAMES = {"interfaz", "tu interfaz", "mi interfaz", "la interfaz", "hud", "el hud", "tu hud",
             "jarvis", "panel", "tu panel", "panel de jarvis", "interfaz de jarvis"}
# Cómo se dice a veces una app frente a su nombre en el menú Inicio
APP_ALIASES = {
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "code": "Visual Studio Code",
    "bloc": "Bloc de notas", "notepad": "Bloc de notas", "explorador": "Explorador de archivos",
    "calculadora": "Calculadora", "configuracion": "Configuración", "ajustes": "Configuración",
    "terminal": "Terminal", "word": "Word", "excel": "Excel",
}
# Programa (.exe) de las apps cuyo nombre no coincide con él, para cerrarlas
CLOSE_EXE = {
    "bloc de notas": "notepad", "google chrome": "chrome", "visual studio code": "code",
    "explorador de archivos": "explorer", "calculadora": "calculatorapp", "word": "winword",
    "configuracion": "systemsettings", "terminal": "windowsterminal",
}
FORCE_CLOSE_EXE = {"spotify.exe"}  # al cerrarlo se queda en la bandeja: se termina
PROTECTED_EXE = {"explorer.exe", "python.exe", "pythonw.exe", "claude.exe"}  # el escritorio y Jarvis
# Spotify: título de la ventana en pausa («Spotify Premium»); sonando es «Artista - Canción»
SPOTIFY_EXE = "spotify.exe"
SPOTIFY_IDLE_TITLE = re.compile(r"^spotify(?: (?:premium|free))?$", re.IGNORECASE)
SPOTIFY_START_WAIT_S = 15.0
SPOTIFY_READY_S = 2.0
# Teclas multimedia (llegan al reproductor aunque no tenga el foco)
VK = {"playpause": 0xB3, "siguiente": 0xB0, "anterior": 0xB1,
      "silencio": 0xAD, "bajar": 0xAE, "subir": 0xAF}
VOLUME_STEP = 0.1
DUCK_LEVEL = 0.2  # la música baja al 20 % de su volumen mientras Jarvis conversa
OWN_AUDIO = {"python.exe", "pythonw.exe"}  # la voz de Jarvis no se baja

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class ActionError(RuntimeError):
    """La acción no se puede hacer al momento: el cerebro la encarga como tarea."""


class NotOpenError(ActionError):
    """Lo que había que cerrar ya no está abierto: se dice al momento, sin tarea."""


# ── Utilidades ────────────────────────────────────────────────────────────────

def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9ñ]+", text))


def _similarity(query: str, name: str) -> float:
    q, n = _plain(query), _plain(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if len(q) >= 3 and (n.startswith(q) or f" {q}" in f" {n}"):
        return 0.9
    return difflib.SequenceMatcher(None, q, n).ratio()


def _best(query: str, items: list[tuple[str, str]]) -> tuple[float, str, str]:
    scored = [(_similarity(query, name), name, target) for name, target in items]
    return max(scored, default=(0.0, "", ""))


def _start(target: str) -> None:
    os.startfile(target)  # noqa: S606 - app o URL elegida de una lista local


# ── Apps del menú Inicio ──────────────────────────────────────────────────────

def _read_start_apps() -> list[tuple[str, str]]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=20, creationflags=NO_WINDOW, check=False,
    ).stdout
    data = json.loads(out or "[]")
    apps = [(str(a.get("Name") or ""), str(a.get("AppID") or "")) for a in
            (data if isinstance(data, list) else [data])]
    apps = [a for a in apps if a[0] and a[1]]
    try:
        APPS_CACHE.write_text(json.dumps({"actualizado": time.time(), "apps": apps},
                                         ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return apps


def start_apps(refresh: bool = False) -> list[tuple[str, str]]:
    """Apps del menú Inicio (nombre, AppID), en caché un día (leerlas cuesta ~1,3 s)."""
    if not refresh:
        try:
            cached = json.loads(APPS_CACHE.read_text(encoding="utf-8"))
            if time.time() - float(cached.get("actualizado", 0)) < APPS_MAX_AGE_S:
                return [tuple(a) for a in cached.get("apps", [])]
        except (OSError, ValueError):
            pass
    return _read_start_apps()


def launch_app(app_id: str) -> None:
    if Path(app_id).is_file():
        _start(app_id)
    else:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                         creationflags=NO_WINDOW, close_fds=True)


# ── Marcadores de Chrome ──────────────────────────────────────────────────────

def bookmarks() -> list[tuple[str, str]]:
    try:
        state = json.loads((CHROME_DATA / "Local State").read_text(encoding="utf-8"))
        profile = state.get("profile", {}).get("last_used") or "Default"
        data = json.loads((CHROME_DATA / profile / "Bookmarks").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found: list[tuple[str, str]] = []

    def walk(node: dict) -> None:
        if node.get("type") == "url" and node.get("url", "").startswith("http"):
            found.append((str(node.get("name") or ""), node["url"]))
        for child in node.get("children", []) or []:
            walk(child)

    for root in data.get("roots", {}).values():
        if isinstance(root, dict):
            walk(root)
    return found


# ── Abrir ─────────────────────────────────────────────────────────────────────

def open_target(query: str) -> str:
    """Abre una app, un marcador, una web conocida o un dominio. Devuelve qué ha abierto."""
    query = query.strip().strip("«»\"'")
    if not query:
        raise ActionError("Falta qué abrir.")
    if re.match(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?$", query) and " " not in query:
        url = query if query.startswith("http") else f"https://{query}"
        _start(url)
        return url
    if _plain(query) in HUD_NAMES:
        open_hud()
        return "mi interfaz"
    query = APP_ALIASES.get(_plain(query), query)
    apps = start_apps()
    score, name, app_id = _best(query, apps)
    if score < APP_MATCH:
        fresh = start_apps(refresh=True)  # quizá se instaló hace poco
        score, name, app_id = _best(query, fresh)
    if score >= APP_MATCH:
        launch_app(app_id)
        return f"la app {name}"
    b_score, b_name, b_url = _best(query, bookmarks())
    if b_score >= BOOKMARK_MATCH:
        _start(b_url)
        return f"el marcador {b_name}"
    site = KNOWN_SITES.get(_plain(query))
    if site:
        _start(site)
        return site
    if score >= APP_MATCH_LOOSE:
        launch_app(app_id)
        return f"la app {name}"
    raise ActionError(f"No encuentro ninguna app, marcador ni web que se llame «{query}».")


def _chrome_exe() -> str | None:
    """chrome.exe según el registro de Windows (App Paths) o su sitio habitual."""
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                path = winreg.QueryValue(key, None)
                if path and Path(path).is_file():
                    return path
        except OSError:
            continue
    for base in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        path = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and path.is_file():
            return str(path)
    return None


def open_hud() -> None:
    """[Nau] 26/11/2026: la interfaz en su propia ventana de Chrome (modo app), para poder
    cerrarla sola sin tocar las pestañas de Nau. Desde la misma noche se abre a pantalla
    completa y, si ya está abierta, se trae delante en vez de abrir otra."""
    chrome = _chrome_exe()
    if not chrome:
        _start(HUD_URL)
        return
    hwnd = _hud_window()
    if not hwnd:
        subprocess.Popen([chrome, f"--app={HUD_URL}", "--start-fullscreen"],
                         creationflags=NO_WINDOW, close_fds=True)
        deadline = time.monotonic() + HUD_WAIT_S
        while not hwnd and time.monotonic() < deadline:
            time.sleep(0.2)
            hwnd = _hud_window()
        if not hwnd:
            return  # Chrome tarda: se abre igual, aunque sin pantalla completa
        time.sleep(0.5)  # que termine de colocarse
    _fullscreen(hwnd)


def _hud_window() -> int:
    for hwnd, _pid, exe in _windows():
        if exe == "chrome.exe" and _title(hwnd).strip() in HUD_TITLES:
            return hwnd
    return 0


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _is_fullscreen(hwnd: int) -> bool:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    mon = info.rcMonitor
    return (rect.left <= mon.left and rect.top <= mon.top
            and rect.right >= mon.right and rect.bottom >= mon.bottom)


def _fullscreen(hwnd: int) -> None:
    """Delante y a pantalla completa (F11 de Chrome). F11 solo se pulsa si la ventana de la
    interfaz tiene el foco: así nunca cambia otra ventana de Nau."""
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE, por si estaba minimizada
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    if _is_fullscreen(hwnd) or user32.GetForegroundWindow() != hwnd:
        if not _is_fullscreen(hwnd):
            user32.ShowWindow(hwnd, 3)  # sin foco no hay F11: al menos maximizada
        return
    user32.keybd_event(0x7A, 0, 0, 0)    # F11
    user32.keybd_event(0x7A, 0, 0x2, 0)


# ── Ventanas y teclas ─────────────────────────────────────────────────────────

def _process_name(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.casefold()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def window_titles(exe: str) -> list[str]:
    """Títulos de las ventanas visibles de un programa (p. ej. spotify.exe)."""
    titles: list[str] = []
    names: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in names:
            names[pid.value] = _process_name(pid.value)
        if names[pid.value] == exe:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            titles.append(buf.value)
        return True

    user32.EnumWindows(callback, 0)
    return titles


def press(key: str) -> None:
    vk = VK[key]
    user32.keybd_event(vk, 0, 0x1, 0)        # KEYEVENTF_EXTENDEDKEY
    user32.keybd_event(vk, 0, 0x1 | 0x2, 0)  # … | KEYEVENTF_KEYUP


def spotify_state() -> str:
    """'cerrado', 'pausa' o 'sonando' (según el título de su ventana)."""
    titles = [t for t in window_titles(SPOTIFY_EXE) if t.strip()]
    if not titles:
        return "cerrado"
    return "pausa" if all(SPOTIFY_IDLE_TITLE.match(t.strip()) for t in titles) else "sonando"


# ── Música ────────────────────────────────────────────────────────────────────

def _base_pythonw() -> str:
    base = Path(getattr(sys, "_base_executable", "") or sys.executable)
    for exe in (base.with_name("pythonw.exe"), Path(sys.executable).with_name("pythonw.exe")):
        if exe.exists():
            return str(exe)
    return sys.executable


def _detach(*args: str) -> None:
    """Sigue en segundo plano (abrir Spotify y darle al play tarda unos segundos)."""
    command = [_base_pythonw(), str(Path(__file__).resolve()), *args]
    popen = dict(close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
    try:
        subprocess.Popen(command, creationflags=DETACHED | BREAKAWAY, **popen)
    except OSError:
        subprocess.Popen(command, creationflags=DETACHED, **popen)


def start_spotify_and_play() -> None:
    """(En segundo plano) abre Spotify, espera a que esté listo y le da al play."""
    apps = start_apps()
    score, _name, app_id = _best("Spotify", apps)
    if score >= APP_MATCH:
        launch_app(app_id)
    else:
        _start("spotify:")
    deadline = time.monotonic() + SPOTIFY_START_WAIT_S
    while time.monotonic() < deadline and spotify_state() == "cerrado":
        time.sleep(0.3)
    time.sleep(SPOTIFY_READY_S)
    for _ in range(2):  # a veces el primer play llega antes de que esté listo
        if spotify_state() == "sonando":
            return
        press("playpause")
        time.sleep(2.0)


def music(order: str) -> str:
    order = _plain(order)
    if order in ("play", "pon", "reproducir", "continuar", "seguir", "musica"):
        state = spotify_state()
        if state == "sonando":
            return "ya estaba sonando"
        if state == "cerrado":
            _detach("_spotify_play")
            return "abro Spotify y le doy al play"
        press("playpause")
        return "play"
    if order in ("pausa", "pausar", "parar", "para", "stop"):
        if spotify_state() == "sonando" or playing_sessions():
            press("playpause")
            return "pausa"
        return "no sonaba nada"
    if order in ("siguiente", "next", "salta", "saltar"):
        press("siguiente")
        return "siguiente canción"
    if order in ("anterior", "atras", "previous"):
        press("anterior")
        return "canción anterior"
    raise ActionError(f"Orden de música desconocida: «{order}».")


# ── Volumen (pycaw) ───────────────────────────────────────────────────────────

def _com_init() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except OSError:
        pass  # ya iniciado en este hilo


def _endpoint():
    _com_init()
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    speakers = AudioUtilities.GetSpeakers()
    interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return interface.QueryInterface(IAudioEndpointVolume)


def volume(order: str) -> str:
    order = _plain(order)
    try:
        endpoint = _endpoint()
    except Exception:  # noqa: BLE001 - sin pycaw, teclas de volumen
        endpoint = None
    if order.isdigit():
        level = max(0, min(100, int(order))) / 100
        if endpoint is None:
            raise ActionError("No puedo fijar el volumen exacto.")
        endpoint.SetMute(0, None)
        endpoint.SetMasterVolumeLevelScalar(level, None)
        return f"volumen al {round(level * 100)} %"
    if order in ("subir", "sube", "mas", "arriba"):
        if endpoint is None:
            for _ in range(5):
                press("subir")
            return "volumen más alto"
        level = min(1.0, endpoint.GetMasterVolumeLevelScalar() + VOLUME_STEP)
        endpoint.SetMute(0, None)
        endpoint.SetMasterVolumeLevelScalar(level, None)
        return f"volumen al {round(level * 100)} %"
    if order in ("bajar", "baja", "menos", "abajo"):
        if endpoint is None:
            for _ in range(5):
                press("bajar")
            return "volumen más bajo"
        level = max(0.0, endpoint.GetMasterVolumeLevelScalar() - VOLUME_STEP)
        endpoint.SetMasterVolumeLevelScalar(level, None)
        return f"volumen al {round(level * 100)} %"
    if order in ("silencio", "silenciar", "mute", "quitar sonido"):
        if endpoint is None:
            press("silencio")
        else:
            endpoint.SetMute(1, None)
        return "sonido silenciado"
    if order in ("activar", "quitar silencio", "unmute", "sonido"):
        if endpoint is None:
            press("silencio")
        else:
            endpoint.SetMute(0, None)
        return "sonido activado"
    raise ActionError(f"Orden de volumen desconocida: «{order}».")


def _media_sessions():
    """Sesiones de audio de otros programas (no la voz de Jarvis ni los sonidos del sistema)."""
    _com_init()
    from pycaw.pycaw import AudioUtilities

    for session in AudioUtilities.GetAllSessions():
        if session.Process is None:
            continue
        try:
            name = session.Process.name().casefold()
        except Exception:  # noqa: BLE001 - el proceso acaba de cerrarse
            continue
        if name not in OWN_AUDIO:
            yield session, name


def playing_sessions() -> list[str]:
    try:
        return [name for session, name in _media_sessions() if session.State == 1]
    except Exception:  # noqa: BLE001 - sin pycaw no se sabe
        return []


def duck_media() -> int:
    """Baja al DUCK_LEVEL lo que esté sonando (música, vídeos) y guarda cómo estaba."""
    try:
        saved = json.loads(DUCK_FILE.read_text(encoding="utf-8")) if DUCK_FILE.exists() else {}
        for session, _name in _media_sessions():
            pid = str(session.ProcessId)
            if session.State != 1 or pid in saved:
                continue
            level = session.SimpleAudioVolume.GetMasterVolume()
            saved[pid] = level
            session.SimpleAudioVolume.SetMasterVolume(level * DUCK_LEVEL, None)
        if saved:
            DUCK_FILE.write_text(json.dumps(saved), encoding="utf-8")
        return len(saved)
    except Exception:  # noqa: BLE001 - bajar la música es un extra: nunca rompe la charla
        return 0


def restore_media() -> int:
    """Devuelve la música al volumen que tenía antes de duck_media()."""
    try:
        saved = json.loads(DUCK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    restored = 0
    try:
        for session, _name in _media_sessions():
            level = saved.get(str(session.ProcessId))
            if level is not None:
                session.SimpleAudioVolume.SetMasterVolume(float(level), None)
                restored += 1
    except Exception:  # noqa: BLE001
        pass
    DUCK_FILE.unlink(missing_ok=True)
    return restored


# ── Cerrar ────────────────────────────────────────────────────────────────────

def _windows() -> list[tuple[int, int, str]]:
    """(hwnd, pid, programa) de las ventanas visibles con título."""
    found: list[tuple[int, int, str]] = []
    names: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in names:
                names[pid.value] = _process_name(pid.value)
            found.append((hwnd, pid.value, names[pid.value]))
        return True

    user32.EnumWindows(callback, 0)
    return found


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def close_hud() -> str:
    """Cierra la ventana propia de la interfaz (modo app). Si está en una pestaña normal no se
    toca (cerraría todo Chrome): va como tarea."""
    targets = [hwnd for hwnd, _pid, exe in _windows()
               if exe == "chrome.exe" and _title(hwnd).strip() in HUD_TITLES]
    if not targets:
        if any(exe == "chrome.exe" and _title(hwnd).startswith(tuple(f"{t} - " for t in HUD_TITLES))
               for hwnd, _pid, exe in _windows()):
            raise ActionError("La interfaz está en una pestaña de Chrome, no en su ventana.")
        raise NotOpenError("la interfaz no está abierta")
    for hwnd in targets:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return "cerrada mi interfaz"


def close_folders(query: str) -> str | None:
    """Cierra ventanas de carpetas del explorador («cierra la carpeta de Descargas», «cierra
    todas las carpetas»). None si la orden no habla de carpetas. El escritorio no se toca."""
    plain = _plain(query)
    every = "todas" in plain and "carpeta" in plain
    name = _folder_words(query)
    if not every and "carpeta" not in plain and known_folder(plain) is None:
        return None
    folder = known_folder(name) if name else None
    wanted = {_plain(folder.name)} if folder else set()
    if name:
        wanted.add(_plain(name))
    closed = 0
    for hwnd, _pid, exe in _windows():
        if exe != "explorer.exe" or _class_name(hwnd) != "CabinetWClass":
            continue
        title = _plain(_title(hwnd))
        if every or not wanted or any(w and (title == w or title.startswith(w)) for w in wanted):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
            closed += 1
    if not closed:
        raise NotOpenError("no hay ninguna carpeta abierta" if every or not name
                           else f"la carpeta «{name}» no está abierta")
    return f"cerradas {closed} ventanas de carpetas" if closed > 1 else "carpeta cerrada"


def _process_alive(exe: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"], capture_output=True,
                         text=True, encoding="oem", errors="replace", timeout=10,
                         creationflags=NO_WINDOW, check=False).stdout
    return exe.casefold() in out.casefold()


def close_app(query: str) -> str:
    """Cierra un programa por su nombre como lo haría la X de la ventana (si hay algo sin
    guardar, el programa pregunta). Spotify se oculta en la bandeja al cerrarlo: se termina.
    Por el título no se cierra nada: «cierra YouTube» cerraría todo Chrome (va como tarea)."""
    if _plain(query) in HUD_NAMES:
        return close_hud()
    folders = close_folders(query)
    if folders is not None:
        return folders
    wanted = _plain(APP_ALIASES.get(_plain(query), query))
    exe_name = CLOSE_EXE.get(wanted, wanted.replace(" ", "")) + ".exe"
    targets = [(hwnd, exe) for hwnd, _pid, exe in _windows() if exe == exe_name]
    if not targets and not _process_alive(exe_name):
        # una app conocida que no está abierta se dice al momento; si no es una app
        # («cierra YouTube», una pestaña), va como tarea
        if wanted in CLOSE_EXE or _best(wanted, start_apps())[0] >= APP_MATCH:
            raise NotOpenError(f"«{query}» no está abierto")
        raise ActionError(f"No veo abierto «{query}».")
    if exe_name in PROTECTED_EXE:
        raise ActionError(f"«{query}» no lo cierro al momento.")
    for hwnd, _exe in targets:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    if exe_name in FORCE_CLOSE_EXE:
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline and _process_alive(exe_name):
            time.sleep(0.3)
        if _process_alive(exe_name):
            subprocess.run(["taskkill", "/IM", exe_name, "/F"], capture_output=True,
                           timeout=10, creationflags=NO_WINDOW, check=False)
    return f"cerrado {query}"


# ── Carpetas y archivos ───────────────────────────────────────────────────────

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _folder_words(text: str) -> str:
    """«la carpeta de Descargas», «mis descargas» → «descargas»."""
    return re.sub(r"^(?:todas )?(?:(?:la|el|las|los|mi|mis|tu|tus) )?(?:carpetas? |ventanas? )?"
                  r"(?:(?:de la|de los|del|de|de mis|de mi) )?", "", _plain(text)).strip()


def known_folder(key: str) -> Path | None:
    """Carpeta de Windows (Descargas, Documentos…) donde esté de verdad (OneDrive incluido)."""
    guid = KNOWN_FOLDERS.get(_plain(key)) or KNOWN_FOLDERS.get(_folder_words(key))
    if guid is None:
        return None
    raw = _GUID.from_buffer_copy(uuid.UUID(guid).bytes_le)
    path = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32")
    if shell32.SHGetKnownFolderPath(ctypes.byref(raw), 0, None, ctypes.byref(path)) != 0:
        return None
    try:
        return Path(path.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


def jarvis_folder() -> Path:
    """Donde Jarvis guarda lo que crea si no se dice otra carpeta: Documentos\\Jarvis."""
    folder = (known_folder("documentos") or Path.home() / "Documents") / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _place(where: str) -> Path:
    if not where.strip():
        return jarvis_folder()
    folder = known_folder(where) or _project_folder(where)
    if folder is None:
        raise ActionError(f"No sé qué carpeta es «{where}».")
    return folder


def _project_folder(name: str) -> Path | None:
    """Una carpeta de proyecto por su nombre («LinkedIn», «motor agéntico»)."""
    try:
        from tarea_claude import find_project
    except ImportError:
        return None
    plain = _plain(name)
    if plain in ("proyectos", "mis proyectos"):
        from ajustes import PROYECTOS

        return PROYECTOS if PROYECTOS.is_dir() else None
    return find_project(re.sub(r"^(?:el |la )?(?:carpeta )?(?:del |de )?(?:proyecto )?", "",
                               name.strip(), flags=re.IGNORECASE))


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    if not cleaned:
        raise ActionError("Falta el nombre.")
    return cleaned[:120]


def _free_path(path: Path) -> Path:
    """Nunca sobrescribe: «informe.txt» → «informe (2).txt» si ya existe."""
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def make_folder(name: str, where: str = "") -> str:
    folder = _free_path(_place(where) / _safe_name(name))
    folder.mkdir(parents=True)
    return f"carpeta «{folder.name}» creada en {folder.parent.name}"


def make_file(name: str, text: str, where: str = "", open_it: bool = False) -> str:
    name = _safe_name(name)
    if not Path(name).suffix:
        name += ".txt"
    path = _free_path(_place(where) / name)
    path.write_text(text.replace("\\n", "\n") + ("\n" if text and not text.endswith("\n") else ""),
                    encoding="utf-8")
    if open_it:
        _start(str(path))
    return f"archivo «{path.name}» creado en {path.parent.name}"


def add_note(text: str) -> str:
    if not text.strip():
        raise ActionError("Falta el texto de la nota.")
    notes = jarvis_folder() / NOTES_FILE
    stamp = time.strftime("%d/%m/%Y %H:%M")
    with notes.open("a", encoding="utf-8") as fh:
        fh.write(f"- {stamp} — {text.strip()}\n")
    return "nota apuntada"


def open_folder(name: str) -> Path | None:
    folder = known_folder(name) or _project_folder(name)
    if folder is not None and folder.is_dir():
        _start(str(folder))
    return folder


def find_file(query: str) -> str:
    """Busca un archivo por su nombre en las carpetas personales y en los proyectos, y lo abre."""
    wanted = _plain(Path(query).stem if "." in query else query)
    if len(wanted) < 3:
        raise ActionError("Dime un nombre de archivo más largo.")
    roots = [p for p in (known_folder(k) for k in SEARCH_FOLDERS) if p and p.is_dir()]
    try:
        from ajustes import PROYECTOS

        if PROYECTOS.is_dir():
            roots.append(PROYECTOS)
    except ImportError:
        pass
    deadline = time.monotonic() + SEARCH_MAX_S
    best: tuple[float, float, Path] | None = None
    for root in roots:
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SEARCH_SKIP]
            for file in files:
                score = _similarity(wanted, Path(file).stem)
                if score >= FILE_MATCH:
                    path = Path(folder) / file
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    if best is None or (score, mtime) > (best[0], best[1]):
                        best = (score, mtime, path)
            if time.monotonic() > deadline:
                break
        if time.monotonic() > deadline:
            break
    if best is None:
        raise ActionError(f"No encuentro ningún archivo que se llame «{query}».")
    _start(str(best[2]))
    return f"abierto el archivo «{best[2].name}»"


def open_latest(where: str = "", kind: str = "") -> str:
    """Abre el archivo más reciente de una carpeta (y sus subcarpetas), p. ej. la última foto."""
    kind = _plain(kind)
    extensions = FILE_KINDS.get(kind, ())
    if not where.strip():
        where = DEFAULT_FOLDER_FOR_KIND.get(kind, "descargas")
    folder = known_folder(where) or _project_folder(where)
    if folder is None or not folder.is_dir():
        raise ActionError(f"No sé qué carpeta es «{where}».")
    deadline = time.monotonic() + SEARCH_MAX_S
    best: tuple[float, Path] | None = None
    for current, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SEARCH_SKIP]
        for file in files:
            if extensions and Path(file).suffix.casefold() not in extensions:
                continue
            if file.startswith(".") or file.casefold() in ("desktop.ini", "thumbs.db"):
                continue
            path = Path(current) / file
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, path)
        if time.monotonic() > deadline:
            break
    if best is None:
        raise ActionError(f"No hay nada{' de ese tipo' if extensions else ''} en {folder.name}.")
    _start(str(best[1]))
    return f"abierto «{best[1].name}», lo último de {folder.name}"


# ── Captura y portapapeles ────────────────────────────────────────────────────

def screenshot() -> str:
    """Win + Impr Pant: Windows la guarda en Imágenes\\Capturas de pantalla."""
    user32.keybd_event(0x5B, 0, 0, 0)            # VK_LWIN
    user32.keybd_event(0x2C, 0, 0, 0)            # VK_SNAPSHOT
    user32.keybd_event(0x2C, 0, 0x2, 0)
    user32.keybd_event(0x5B, 0, 0x2, 0)
    return "captura guardada en Imágenes, Capturas de pantalla"


def copy_text(text: str) -> str:
    if not text:
        raise ActionError("Falta el texto que copiar.")
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    data = (text.replace("\\n", "\r\n") + "\0").encode("utf-16-le")
    for _ in range(5):  # otro programa puede tener el portapapeles abierto un momento
        if user32.OpenClipboard(None):
            break
        time.sleep(0.1)
    else:
        raise ActionError("El portapapeles está ocupado.")
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0042, len(data))  # GHND
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(13, handle)  # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()
    return "texto copiado al portapapeles"


# ── Bloquear ──────────────────────────────────────────────────────────────────

def lock() -> str:
    if not user32.LockWorkStation():
        raise ActionError("Windows no ha dejado bloquear el equipo.")
    return "equipo bloqueado"


def run(kind: str, value: str = "", text: str = "", where: str = "",
        open_it: bool = False) -> str:
    """Punto de entrada de jarvis-herramienta accion. Lanza ActionError si no se puede."""
    kind = _plain(kind)
    if kind == "abrir":
        folder = open_folder(value) if (known_folder(value) or "carpeta" in _plain(value)) else None
        if folder is not None:
            return f"abierta la carpeta {folder.name}"
        return f"abierto {open_target(value)}"
    if kind == "carpeta":
        return make_folder(value, where)
    if kind == "archivo":
        return make_file(value, text, where, open_it)
    if kind == "nota":
        return add_note(value or text)
    if kind == "buscar":
        return find_file(value)
    if kind == "reciente":
        return open_latest(where, value)
    if kind == "captura":
        return screenshot()
    if kind == "copiar":
        return copy_text(value or text)
    if kind == "cerrar":
        return close_app(value)
    if kind == "musica":
        return music(value or "play")
    if kind == "volumen":
        return volume(value)
    if kind == "bloquear":
        return lock()
    raise ActionError(f"Acción desconocida: «{kind}».")


if __name__ == "__main__" and sys.argv[1:2] == ["_spotify_play"]:
    start_spotify_and_play()
