# Solución de problemas

## «No contesta»: revisa en este orden

1. **¿Está vivo?** Mira `data\vigilante.log` y `data\escucha-estado.json` (estado, nivel del micro y tiempo de respuesta). El HUD lo muestra en el centro.
2. **El registro del oyente:**

   ```powershell
   Get-Content "$env:USERPROFILE\claudeProjects\jarvis\data\escucha.log" -Tail 40
   ```

   | Ves | Significa | Qué hacer |
   |---|---|---|
   | `Oído (sin «Jarvis»)` con palabras y volumen alto, pero no se activa | Whisper ha escrito mal el nombre | Prueba **«Hey Jarvis»**, que no pasa por Whisper, o «Hola, Jarvis» |
   | `Casi` | openWakeWord se ha quedado cerca del umbral (0,5) | Habla más cerca o más claro |
   | Volumen máximo muy bajo (0,002–0,05) | El micro no recoge: con voz cerca suele marcar ~0,6 | Revisa el micrófono predeterminado de Windows |
   | Ninguna línea | El oyente no está en marcha | Mira `vigilante.log` o lanza `bin\jarvis-arranque.vbs` |
3. **Cuenta con el arranque.** Tras encender el PC, Jarvis tarda unos **2 minutos** en estar listo.

## Problemas conocidos

| Síntoma | Causa | Arreglo |
|---|---|---|
| Habla con la voz de Windows teniendo créditos | La clave de ElevenLabs tiene un límite propio de créditos y lo ha alcanzado (401) | Sube el límite de la clave en ElevenLabs → *Developers → API keys* |
| Se abre una ventana de Terminal en cada tarea | Las tareas se lanzaban con el `pythonw.exe` del entorno virtual, que es un lanzador que abre `python.exe` con consola | Ya está resuelto: se usa el `pythonw.exe` de la instalación base. Si vuelve a pasar, comprueba `_python()` en `claude_code.py` |
| Contesta dos veces | Hay dos oyentes a la vez | Ya está resuelto con un mutex (`Local\JarvisEscucha`). Si pasa, termina los `pythonw.exe` de `escucha.py` y deja que el vigilante levante uno |
| Salta mientras dictas con otra herramienta | Falsa activación dentro del dictado | Si tu dictado tiene ventana de grabación, adapta `escriba_activo.py` |
| Con altavoces se interrumpe a sí mismo | Su propia voz supera el umbral de interrupción | Usa auriculares o sube `BARGE_IN_MIN_RMS` en `escucha.py` |
| La reserva local (Ollama) no responde | Ollama escucha en el 11434 aunque `OLLAMA_HOST` diga otro puerto | Usa `http://127.0.0.1:11434` en `config.toml` |
| `jarvis connect gcalendar` da error 400 «response_type» | `cmd /c start` cortaba la URL en el primer `&` | Ya está resuelto en `core/utils.py` (`os.startfile`) |
| Las tareas dicen «no encuentro el proyecto» | El nombre dicho no se parece a ninguna carpeta | Revisa `proyectos` en `data\usuario.toml`, o di el nombre como se llama la carpeta |

## Reiniciar a mano

Tras cambiar `escucha.py`, reinicia el oyente: termina el `pythonw.exe` cuya línea de comandos contiene `escucha.py` y deja que el vigilante lo levante (como mucho en 5 minutos), o lánzalo con `bin\jarvis-arranque.vbs`.

```powershell
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
  Where-Object CommandLine -like '*escucha.py*' |
  ForEach-Object { Stop-Process -Id $_.ProcessId }
```

Para la API, lo mismo con los procesos cuya línea de comandos contiene `openjarvis.cli serve`.
