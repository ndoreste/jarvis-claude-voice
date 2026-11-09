# Historial

Jarvis lo uso a diario desde el 06/11/2026. Este es el historial real de cambios e incidencias, con síntoma, causa y arreglo.

## 2026-11-09

### Añadido
- **Primer repositorio público.** Los datos personales salen del código a `data/usuario.toml` (`bin/ajustes.py`). Se añaden plantillas en `config/`, la guía de instalación y el escáner de secretos.
- **Tareas en el PC con Claude Code.** La herramienta `encargar_a_claude` lanza `claude -p` en segundo plano con todos los MCP, conectores y skills, y Jarvis avisa por voz al terminar.
- **Tareas en un proyecto.** «En el proyecto de X» hace que la tarea arranque dentro de esa carpeta. El nombre se reconoce aunque Whisper lo escriba mal.
- **Confirmación obligatoria** para enviar, publicar, pagar o borrar, con la sesión reanudada solo tras el «sí».
- **Confirmación blindada** (tras la revisión de seguridad previa a publicar): `tarea_claude.py` solo acepta `--confirmado` para un permiso ya preguntado por voz, de esa sesión, de menos de 10 min y de un solo uso, y borra cualquier imitación de la marca. Antes, una inyección de prompt leída por el cerebro podía pedir una acción nueva «ya confirmada».
- **Cerebro de voz con Sonnet por la suscripción**: una sesión de `claude -p` en *stream-json* siempre abierta. Haiku por OpenRouter queda de reserva.
- **Voz por frases e interrupción**: empieza a hablar con la primera frase, y hablar ~0,4 s mientras Jarvis habla lo corta.
- **Hilo de 5 minutos**: si vuelves a llamarle pronto, sigue la conversación.

### Arreglado
| Síntoma | Causa | Arreglo |
|---|---|---|
| Cada tarea abría una ventana de Terminal | El `pythonw.exe` del entorno de uv es un lanzador que abre `python.exe` con consola | `_python()` usa el `pythonw.exe` de la instalación base |
| Voz de Windows teniendo 74.000 créditos | La clave de ElevenLabs estaba limitada a 10.000 créditos por periodo (401) | Subir el límite. Además, la reserva ahora **lee la respuesta** en vez de perderla |
| Tardaba 5–21 s y perdía el hilo | Haiku con herramientas por OpenRouter; cada conversación empezaba de cero | Cerebro Sonnet, voz mientras escribe e hilo de 5 min |

## 2026-11-08

| Síntoma | Causa | Arreglo |
|---|---|---|
| La reserva local no respondía | Ollama buscado en el puerto 11435 | Puerto 11434 en `config.toml`, `jarvis.cmd` y `vigilante.ps1` |
| Saltaba mientras dictaba con Escriba y contestaba dos veces | Falso «Hey Jarvis» (0,73) dentro del dictado y dos oyentes a la vez | Ignora lo que oye mientras se ve la ventana de grabación (`escriba_activo.py`) y mutex de instancia única |
| Sordo ~5 s tras conversaciones largas | El contador de silencio no se reiniciaba y reabría el micro a los 600 s | Reinicio del contador al acabar cada conversación |
| `MemoryError` en el oyente | PC sin RAM: servidores MCP huérfanos y otro modelo local cargado | `liberar-ram.ps1` desde el vigilante |
| Agenda | — | Herramienta `agenda_google` (Google Calendar de solo lectura). OAuth reducido a `calendar.readonly` |
| Privacidad | Se registraba lo que el micro oía sin «Jarvis» | Solo se apuntan el número de palabras y el volumen máximo |

## 2026-11-07

| Síntoma | Causa | Arreglo |
|---|---|---|
| No contestaba tras encender el PC | Whisper escribía «Yarby», «Yarmish»… | Variantes del nombre y, por la noche, reconocimiento por similitud fonética |
| Perdía la frase tras un rato parado | Los tramos de más de 6 s se descartaban enteros | Troceo en trozos de ~6 s con ~2 s de solape |
| Respondía a medias frases | Cortaba en pausas de 0,7 s | Si la frase acaba en «…», «que», «para»… espera hasta 3 s más |
| No contestaba tras un silencio o al arrancar | Los auriculares mandan ceros en silencio y la primera voz se tomaba como ruido | Umbral mínimo tras silencio digital y 0,6 s de audio previo |
| El oyente se caía | Nada lo supervisaba | Tarea programada «Jarvis vigilante» |
| HUD | — | Escritorio holográfico con datos reales del PC y el estado del oyente |

## 2026-11-06

- Instalación de OpenJarvis en Windows, voz con faster-whisper y ElevenLabs, y «Hey Jarvis» con openWakeWord.
- `hotwords="Jarvis"` y `vad_filter` en Whisper, porque escribía «Yarmis».
