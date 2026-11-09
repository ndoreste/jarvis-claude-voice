@echo off
setlocal
set "SRC=%~dp0..\src"
rem Datos de Jarvis (config, memoria, logs) dentro de la carpeta jarvis
set "OPENJARVIS_HOME=%~dp0..\data"
rem La app de Ollama escucha en 11434 aunque la variable OLLAMA_HOST del sistema diga 11435
set "OLLAMA_HOST=127.0.0.1:11434"
uv run --project "%SRC%" jarvis %*
