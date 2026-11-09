# Instalación paso a paso

Esta guía monta Jarvis desde cero en Windows 10 u 11, igual que lo monté yo. Calcula entre **45 y 60 minutos**, casi todo esperando descargas.

Cada paso dice **qué hacer** y **qué deberías ver al terminar**. Si algo no sale como se describe, para y mira [solucion-de-problemas.md](solucion-de-problemas.md) antes de seguir.

> Los comandos son de **PowerShell** (no de CMD). Ábrelo con *Inicio → escribe «PowerShell» → Windows PowerShell*. No hace falta ejecutarlo como administrador salvo donde se indica.

## Índice

0. [Antes de empezar](#0-antes-de-empezar)
1. [Herramientas base](#1-herramientas-base)
2. [Claude Code](#2-claude-code)
3. [Carpetas y código](#3-carpetas-y-código)
4. [Aplicar los cambios de Jarvis sobre OpenJarvis](#4-aplicar-los-cambios-de-jarvis-sobre-openjarvis)
5. [Dependencias de Python](#5-dependencias-de-python)
6. [Web del HUD](#6-web-del-hud)
7. [Tus ajustes](#7-tus-ajustes)
8. [Claves y voz](#8-claves-y-voz)
9. [Google Calendar (opcional)](#9-google-calendar-opcional)
10. [Primer arranque](#10-primer-arranque)
11. [Arranque automático y vigilante](#11-arranque-automático-y-vigilante)
12. [Comprobación final](#12-comprobación-final)

---

## 0. Antes de empezar

Necesitas:

| Qué | Para qué | ¿Obligatorio? |
|---|---|---|
| Windows 10 u 11 | Jarvis usa APIs de Windows (sonido, ventanas, tareas programadas) | Sí |
| Suscripción **Claude Pro o Max** | El cerebro de voz y las tareas usan tu sesión de claude.ai | Sí |
| Micrófono (mejor con auriculares) | Con altavoces, la voz de Jarvis puede cortarse a sí misma | Sí |
| ~6 GB libres | OpenJarvis, el modelo de Whisper y las dependencias | Sí |
| Cuenta de **ElevenLabs** | Voz natural. Sin ella habla la voz de Windows | No |
| Cuenta de **OpenRouter** | Respuesta de reserva (Haiku) si Sonnet falla | No |
| Proyecto de **Google Cloud** | Leer tu Google Calendar | No |

En toda la guía, la carpeta de Jarvis es `%USERPROFILE%\claudeProjects\jarvis`. Si usas otra, cámbiala en los comandos.

## 1. Herramientas base

**Qué hacer.** Instala Git, Node.js 22 o superior y uv con `winget`:

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id astral-sh.uv -e
```

Cierra PowerShell y ábrelo de nuevo para que reconozca los programas nuevos. Comprueba las versiones:

```powershell
git --version; node --version; uv --version
```

**Qué deberías ver.** Tres versiones; la de Node debe empezar por `v22` o más.

> Git para Windows trae **Git Bash**, que Claude Code usa para ejecutar comandos. El cerebro de voz lo necesita para lanzar `bin\jarvis-herramienta` (un script de `sh`), así que no lo desinstales.

> **Opcional pero recomendado:** la extensión en Rust de OpenJarvis (memoria y seguridad) necesita Rust y las herramientas de compilación de Visual Studio. Para tenerla, instala también:
> ```powershell
> winget install --id Rustlang.Rustup -e
> winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
> ```
> La segunda pide permisos de administrador y tarda unos minutos.

## 2. Claude Code

**Qué hacer.** Instala Claude Code con el instalador nativo e inicia sesión con tu cuenta de Claude:

```powershell
irm https://claude.ai/install.ps1 | iex
claude
```

Dentro de Claude Code escribe `/login`, elige **cuenta de Claude (suscripción)** y sigue los pasos en el navegador. Después sal con `/exit`.

**Qué deberías ver.** `claude --version` muestra la versión y, al abrir `claude`, no pide iniciar sesión.

> ⚠️ Inicia sesión con la **suscripción**, no con una clave de API. Jarvis quita a propósito `ANTHROPIC_API_KEY` de los procesos que lanza: así nunca hay cobros por uso.

**Opcional:** si quieres que las tareas usen tu Chrome con tus sesiones (publicar en una web, revisar algo con tu usuario), instala la extensión **Claude in Chrome** desde la Chrome Web Store e inicia sesión en ella.

## 3. Carpetas y código

**Qué hacer.** Crea la carpeta de Jarvis, descarga este repositorio y OpenJarvis en la versión con la que está probado:

```powershell
$J = "$env:USERPROFILE\claudeProjects\jarvis"
$R = "$env:USERPROFILE\claudeProjects\jarvis-claude-voice"
git clone https://github.com/ndoreste/jarvis-claude-voice.git $R
git clone https://github.com/open-jarvis/OpenJarvis.git "$J\src"
git -C "$J\src" checkout (Get-Content "$R\openjarvis-overlay\UPSTREAM_COMMIT")
New-Item -ItemType Directory -Force "$J\bin", "$J\data" | Out-Null
Copy-Item "$R\bin\*" "$J\bin\"
```

**Qué deberías ver.** En `%USERPROFILE%\claudeProjects\jarvis` hay tres carpetas: `bin` (con `escucha.py`, `cerebro_claude.py`…), `data` (vacía) y `src` (OpenJarvis). `git checkout` avisa de *detached HEAD*: es normal.

## 4. Aplicar los cambios de Jarvis sobre OpenJarvis

**Qué hacer.**

```powershell
& "$R\scripts\aplicar-overlay.ps1" -OpenJarvis "$J\src"
```

Si PowerShell bloquea el script («la ejecución de scripts está deshabilitada»), permítelo solo para tu usuario y repite:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Qué deberías ver.** «Cambios de Jarvis aplicados sobre OpenJarvis» y una lista de archivos con `M` (modificados) y `??` (nuevos), entre ellos `hud_routes.py`, `claude_code.py` y `components/Hud/`.

## 5. Dependencias de Python

**Qué hacer.** Crea el entorno de Python de OpenJarvis con la API, la voz y los modelos en la nube, y añade openWakeWord (el oyente lo carga; «Hey Jarvis» viene apagado y se enciende con `WAKE_BY_HEY_JARVIS` en `escucha.py`):

```powershell
cd "$J\src"
uv python install 3.11
uv sync --python 3.11 --extra desktop --extra inference-cloud
uv pip install openwakeword==0.6.0 scikit-learn
.\.venv\Scripts\python.exe -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'])"
```

Si instalaste Rust en el paso 1, compila también la extensión:

```powershell
uv run maturin develop -m rust/crates/openjarvis-python/Cargo.toml
```

> Jarvis necesita **Python 3.11 o superior** (lee los ajustes con `tomllib`), y con esta versión está probado. Las tareas se lanzan con el `pythonw.exe` de esa instalación de uv.

**Qué deberías ver.** Que `uv sync` termina sin errores rojos (tarda unos minutos la primera vez) y que la descarga de modelos acaba sin errores. Comprueba que todo carga:

```powershell
.\.venv\Scripts\python.exe -c "import faster_whisper, openwakeword, sounddevice; print('ok')"
```

> ⚠️ `openwakeword` y `scikit-learn` no están en `pyproject.toml` de OpenJarvis. Si algún día haces `uv sync --exact`, se borran y hay que repetir esta línea.

## 6. Web del HUD

**Qué hacer.**

```powershell
cd "$J\src\frontend"
npm install
```

**Qué deberías ver.** `added … packages` sin errores. Los avisos (`npm warn`) se pueden ignorar.

## 7. Tus ajustes

**Qué hacer.** Copia las plantillas a `data` y ábrelas para editarlas:

```powershell
Copy-Item "$R\config\usuario.example.toml" "$J\data\usuario.toml"
Copy-Item "$R\config\config.example.toml"  "$J\data\config.toml"
Copy-Item "$R\config\SOUL.example.md"      "$J\data\SOUL.md"
notepad "$J\data\usuario.toml"
```

En `usuario.toml` cambia:

- `nombre`: cómo quieres que te llame Jarvis.
- `perfil`: una o dos frases sobre ti; el cerebro de voz las usa como contexto.
- `proyectos`: tu carpeta de proyectos de Claude Code. Sirve para «en el proyecto de…».
- `[ciudad]`: el nombre, la latitud y la longitud para el tiempo del HUD.

Haz lo mismo en `SOUL.md`: cambia «Alex» por tu nombre.

**Qué deberías ver.** Tres archivos en `data`. `claude_exe` puede quedarse vacío si el paso 2 funcionó, porque Jarvis lo busca solo.

## 8. Claves y voz

**Qué hacer.**

1. **ElevenLabs (voz).** En [elevenlabs.io](https://elevenlabs.io), *Voices*, elige o diseña una voz y copia su **Voice ID**. En `data\config.toml`, pégalo en `voice_id`. Después, en *Developers → API keys*, crea una clave.
   > ⚠️ Si limitas los créditos de la clave, ponle un límite alto: cuando la clave llega a su tope, ElevenLabs responde 401 **aunque la cuenta tenga saldo**, y Jarvis pasa a la voz de Windows.
2. **OpenRouter (reserva, opcional).** En [openrouter.ai](https://openrouter.ai), *Keys*, crea una clave.
3. Guarda las claves como variables de tu usuario. El script las pide sin mostrarlas en pantalla:

```powershell
powershell -ExecutionPolicy Bypass -File "$R\scripts\configurar-claves.ps1"
```

**Qué deberías ver.** «guardada» junto a cada clave que hayas pegado. Cierra y abre PowerShell para que las vean los programas nuevos.

## 9. Google Calendar (opcional)

Sin este paso, Jarvis funciona igual pero no puede leer tu agenda: quita `agenda_google` de `[tools] enabled` en `config.toml`.

**Qué hacer.**

1. En [console.cloud.google.com](https://console.cloud.google.com) crea un proyecto y activa **Google Calendar API** (*APIs y servicios → Biblioteca*).
2. En *Pantalla de consentimiento de OAuth*, elige tipo **Externo**, añade el permiso `.../auth/calendar.readonly` y tu correo como usuario de prueba.
3. En *Credenciales → Crear credenciales → ID de cliente de OAuth*, elige tipo **Aplicación de escritorio**. Copia el ID y el secreto.
4. Conecta la cuenta (se abre el navegador para dar permiso):

```powershell
$env:OPENJARVIS_HOME = "$J\data"
cd "$J\src"
uv run jarvis connect gcalendar
```

Pega el *Client ID* y el *Client Secret* cuando los pida.

**Qué deberías ver.** Google pide permiso **solo para ver tu calendario** y la consola confirma la conexión.

> 💡 Con la app en modo *Prueba*, Google caduca el permiso a los 7 días. Para que no caduque hay que pasarla a *En producción*. Google lo permite sin verificar si eres el único usuario, pero exige una página de privacidad pública: la mía está en [ndoreste/jarvis-asistente](https://github.com/ndoreste/jarvis-asistente).

## 10. Primer arranque

**Qué hacer.** Arranca Jarvis con el lanzador oculto:

```powershell
wscript "$J\bin\jarvis-arranque.vbs"
```

Espera **1 o 2 minutos**: la primera vez se descargan los modelos de Whisper. Después abre [http://localhost:5173](http://localhost:5173).

**Qué deberías ver.** El HUD con la CPU, la RAM y el tiempo de tu ciudad, y en el centro el estado del oyente en «en espera». Di **«¿Qué pasa, Jarvis?»**: si solo le llamas, te saluda («Hola, aquí estoy…»). Es la única frase que lo despierta («Hola, Jarvis» no). Cuando el HUD pase a «escuchando», pregunta «¿qué hora es?».

Para ver qué está pasando por dentro:

```powershell
Get-Content "$J\data\escucha.log" -Tail 20 -Wait
```

## 11. Arranque automático y vigilante

**Qué hacer.**

1. Que Jarvis arranque al iniciar sesión, con un acceso directo en la carpeta *Inicio*:

```powershell
$lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Jarvis.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = "wscript.exe"
$s.Arguments = "`"$J\bin\jarvis-arranque.vbs`""
$s.WorkingDirectory = "$J\bin"
$s.Save()
```

2. Que un vigilante lo levante si se cae. Es una tarea programada cada 5 minutos que no necesita administrador:

```powershell
powershell -ExecutionPolicy Bypass -File "$J\bin\instalar-vigilante.ps1"
```

3. *(Opcional)* Para usar el comando `jarvis` desde cualquier sitio, añade `bin` al PATH de tu usuario:

```powershell
[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ";$J\bin", 'User')
```

**Qué deberías ver.** En el *Programador de tareas* aparece «Jarvis vigilante» y, en `data\vigilante.log`, una línea cada vez que levanta algo.

## 12. Comprobación final

Prueba estas cuatro frases en orden:

| Di | Debería |
|---|---|
| «¿Qué pasa, Jarvis? ¿Qué hora es?» | Responder en un par de segundos |
| «¿Qué tengo mañana?» | Leer tu agenda (si hiciste el paso 9) |
| «Crea en el escritorio un archivo prueba.txt que diga hola» | Decir «me pongo con ello» y, unos 20–30 s después, «ya está» |
| «Borra el archivo prueba.txt del escritorio» | **Pedirte permiso** («¿Lo hago?») y borrarlo solo si dices que sí |

Si las cuatro funcionan, Jarvis está listo. Para desinstalarlo, deshaz lo del paso 11 (borra el acceso directo y ejecuta `Unregister-ScheduledTask -TaskName 'Jarvis vigilante' -Confirm:$false`), quita las variables de las claves y borra la carpeta `jarvis`.
