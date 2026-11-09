# [Nau] Libera RAM y limpia temporales. Lo llama vigilante.ps1 cada 5 minutos
# (también 3 minutos después de iniciar sesión).
# 1. MCP huérfanos: cierra los servidores MCP (chrome-devtools, whatsapp, playwright,
#    fal-ai, n8n) cuya sesión de Claude ya no existe.
# 2. Modelo de Escriba: si su llama-server (~4 GB) lleva 30 min sin usar la CPU, lo cierra.
#    Escriba solo pierde ese modelo; la transcripción (Parakeet) no se toca.
# 3. Temporales: una vez al día borra de %TEMP% lo que tenga más de 7 días.
# Registro: ..\data\liberar-ram.log   Estado: ..\data\liberar-ram.json

$ErrorActionPreference = 'Stop'
$data = Join-Path (Split-Path -Parent $PSScriptRoot) 'data'
$logFile = Join-Path $data 'liberar-ram.log'
$stateFile = Join-Path $data 'liberar-ram.json'
$MCP_PATTERN = 'chrome-devtools-mcp|whatsapp-mcp-ts|@playwright/mcp|fal-ai-mcp-server|fal-mcp-server|n8n-mcp'
$ESCRIBA_IDLE_MINUTES = 30
$TEMP_MAX_AGE_DAYS = 7

function Write-Log([string]$message) {
    Add-Content -Path $logFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" -Encoding utf8
}

function Read-State {
    try { return Get-Content $stateFile -Raw | ConvertFrom-Json } catch { return [pscustomobject]@{} }
}

# ¿Cuelga este proceso (por la cadena de padres) de una sesión de Claude viva?
function Test-HasClaudeAncestor($proc, $byId) {
    $current = $proc
    foreach ($level in 1..8) {
        $parent = $byId[[int]$current.ParentProcessId]
        # Padre muerto, o PID reutilizado por un proceso más nuevo que el hijo
        if (-not $parent -or $parent.CreationDate -gt $current.CreationDate) { return $false }
        if ($parent.Name -eq 'claude.exe' -or $parent.Name -eq 'Code.exe') { return $true }
        $current = $parent
    }
    return $true  # cadena muy larga: ante la duda, no se toca
}

function Stop-OrphanMcp($all) {
    $byId = @{}
    foreach ($p in $all) { $byId[[int]$p.ProcessId] = $p }
    $orphans = @($all | Where-Object {
        $_.Name -in @('node.exe', 'cmd.exe') -and ([string]$_.CommandLine) -match $MCP_PATTERN -and
        -not (Test-HasClaudeAncestor $_ $byId)
    })
    if ($orphans.Count -eq 0) { return }
    $mb = [math]::Round(($orphans | Measure-Object WorkingSetSize -Sum).Sum / 1MB)
    $orphans | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Log "MCP huérfanos cerrados: $($orphans.Count) procesos (~$mb MB)"
}

function Stop-IdleEscribaModel($state) {
    $llama = Get-Process llama-server -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like '*com.escriba.app*' } | Select-Object -First 1
    if (-not $llama) { return $null }
    $now = Get-Date
    $previous = $state.escriba
    $isSame = $previous -and $previous.pid -eq $llama.Id
    $isIdle = $isSame -and ($llama.CPU - $previous.cpu) -lt 1
    $since = if ($isIdle) { [datetime]$previous.since } else { $now }
    if (($now - $since).TotalMinutes -ge $ESCRIBA_IDLE_MINUTES) {
        $mb = [math]::Round($llama.WorkingSet64 / 1MB)
        Stop-Process -Id $llama.Id -Force -ErrorAction SilentlyContinue
        Write-Log "Modelo de Escriba sin usar $ESCRIBA_IDLE_MINUTES min: cerrado (~$mb MB)"
        return $null
    }
    return [pscustomobject]@{ pid = $llama.Id; cpu = $llama.CPU; since = $since.ToString('o') }
}

function Clear-OldTemp($state) {
    $today = (Get-Date).ToString('yyyy-MM-dd')
    if ($state.tempCleaned -eq $today) { return $state.tempCleaned }
    $limit = (Get-Date).AddDays(-$TEMP_MAX_AGE_DAYS)
    $files = @(Get-ChildItem $env:TEMP -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $limit })
    $freed = 0
    foreach ($f in $files) {
        try { Remove-Item $f.FullName -Force -ErrorAction Stop; $freed += $f.Length } catch {}
    }
    Write-Log "Temporales de más de $TEMP_MAX_AGE_DAYS días: $([math]::Round($freed / 1MB)) MB borrados"
    return $today
}

try {
    $state = Read-State
    Stop-OrphanMcp (Get-CimInstance Win32_Process)
    $newState = [pscustomobject]@{
        escriba     = Stop-IdleEscribaModel $state
        tempCleaned = Clear-OldTemp $state
    }
    $newState | ConvertTo-Json | Set-Content $stateFile -Encoding utf8
} catch {
    Write-Log "Error: $($_.Exception.Message)"
}
