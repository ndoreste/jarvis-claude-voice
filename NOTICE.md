# Avisos de terceros

## OpenJarvis

Jarvis se monta sobre [OpenJarvis](https://github.com/open-jarvis/OpenJarvis)
(Copyright 2025 The OpenJarvis Authors), con licencia Apache 2.0. El texto completo está
en [`openjarvis-overlay/LICENSE-OpenJarvis`](openjarvis-overlay/LICENSE-OpenJarvis).

Este repositorio **no redistribuye OpenJarvis completo**. Solo incluye, en
[`openjarvis-overlay/`](openjarvis-overlay/), estos archivos:

- **Archivos de OpenJarvis modificados.** Siguen bajo Apache 2.0, con los cambios marcados
  con `[Nau]` en el propio código. Todos los cambios están en
  [`cambios-openjarvis.patch`](openjarvis-overlay/cambios-openjarvis.patch), contra el commit
  de [`UPSTREAM_COMMIT`](openjarvis-overlay/UPSTREAM_COMMIT):
  `oauth.py`, `utils.py`, `cloud.py`, `api_routes.py`, `routes.py`, `faster_whisper.py`,
  `tools/__init__.py`, `App.tsx`, `InputArea.tsx`, `useSpeech.ts`, `ConversationList.tsx`,
  `Sidebar.tsx` y `GetStartedPage.tsx`.
- **Archivos nuevos escritos para Jarvis.** Licencia MIT, igual que el resto del repo:
  `cpu_utility.py`, `hud_routes.py`, `claude_code.py`, `google_calendar.py`, `HudPage.tsx`
  y la carpeta `components/Hud/`.

## Modelos y servicios

- **openWakeWord**: modelo `hey_jarvis_v0.1` (Apache 2.0). Se descarga al instalar el paquete.
- **faster-whisper / Whisper**: MIT. Los pesos del modelo los descarga OpenJarvis.
- **Claude Code**: herramienta de Anthropic que se usa con la cuenta de cada persona. No se
  incluye en el repo.
- **ElevenLabs**, **OpenRouter**, **Open-Meteo** y **Google Calendar API**: servicios
  externos, cada uno con sus condiciones de uso.

«Jarvis» es solo el nombre de la palabra de activación. Este proyecto no tiene relación con
Marvel ni con ninguna marca registrada.
