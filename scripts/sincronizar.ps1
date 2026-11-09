<#
.SYNOPSIS
  Copia al repositorio el código de una instalación de Jarvis que ya funciona.

.DESCRIPTION
  Uso del autor: trae a este repo los scripts de bin\, los archivos de OpenJarvis que
  Jarvis cambia o añade (openjarvis-overlay\) y el parche con los cambios. Después pasa
  el escáner de secretos. Nunca copia data\ (configuración, claves, registros, memoria).

.EXAMPLE
  .\scripts\sincronizar.ps1 -Origen C:\Users\yo\claudeProjects\jarvis
#>
param(
    [string]$Origen = (Join-Path $PSScriptRoot '..\..\jarvis')
)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..')
$origen = Resolve-Path $Origen
$src = Join-Path $origen 'src'

# 1. Scripts de Jarvis (sin cachés)
$bin = Join-Path $repo 'bin'
Get-ChildItem $bin -File | Remove-Item
Get-ChildItem (Join-Path $origen 'bin') -File | Copy-Item -Destination $bin

# 2. Archivos de OpenJarvis que Jarvis cambia (M) o añade (A)
$overlay = @(
    'src/openjarvis/connectors/oauth.py'                     # M  permisos de Google: solo calendario
    'src/openjarvis/core/utils.py'                           # M  abrir el navegador sin cortar la URL
    'src/openjarvis/engine/cloud.py'                         # M  herramientas con OpenRouter
    'src/openjarvis/server/api_routes.py'                    # M  rutas del HUD
    'src/openjarvis/server/routes.py'                        # M  modelo cloud en /v1/models
    'src/openjarvis/speech/faster_whisper.py'                # M  hotwords y filtro de voz
    'src/openjarvis/tools/__init__.py'                       # M  registra las herramientas nuevas
    'src/openjarvis/server/cpu_utility.py'                   # A  CPU como el Administrador de tareas
    'src/openjarvis/server/hud_routes.py'                    # A  API del HUD
    'src/openjarvis/tools/claude_code.py'                    # A  herramienta encargar_a_claude
    'src/openjarvis/tools/google_calendar.py'                # A  herramienta agenda_google
    'frontend/src/App.tsx'                                   # M  el HUD es la portada
    'frontend/src/components/Chat/InputArea.tsx'             # M  modo conversación
    'frontend/src/hooks/useSpeech.ts'                        # M  modo conversación
    'frontend/src/components/Sidebar/ConversationList.tsx'   # M  el chat pasa a /chat
    'frontend/src/components/Sidebar/Sidebar.tsx'            # M  el chat pasa a /chat
    'frontend/src/pages/GetStartedPage.tsx'                  # M  el chat pasa a /chat
    'frontend/src/pages/HudPage.tsx'                         # A  HUD
    'frontend/src/components/Hud'                            # A  HUD (carpeta)
)
$dest = Join-Path $repo 'openjarvis-overlay'
Get-ChildItem $dest -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
foreach ($rel in $overlay) {
    $from = Join-Path $src $rel
    $to = Join-Path $dest $rel
    New-Item -ItemType Directory -Force (Split-Path $to) | Out-Null
    Copy-Item $from $to -Recurse -Force
}

# 3. Parche legible con los cambios sobre OpenJarvis y versión de partida
$modified = $overlay | Where-Object { Test-Path (Join-Path $src $_) -PathType Leaf } |
    Where-Object { git -C $src ls-files --error-unmatch $_ 2>$null }
git -C $src diff -- @modified | Set-Content -Encoding utf8 (Join-Path $dest 'cambios-openjarvis.patch')
git -C $src rev-parse HEAD | Set-Content -Encoding ascii (Join-Path $dest 'UPSTREAM_COMMIT')

# 4. Escáner de secretos
node (Join-Path $repo 'scripts\check-secrets.mjs')
if ($LASTEXITCODE -ne 0) { throw 'El escáner ha encontrado datos privados: revisa antes de hacer commit.' }
Write-Host 'Sincronizado.' -ForegroundColor Green
