# Vigilante de Jarvis: lo ejecuta cada 5 minutos la tarea programada «Jarvis vigilante»
# (a través de vigilante.vbs, para que no se vea ninguna ventana).
# 1. Si la API (:8000) no responde, la arranca (o la reinicia si está colgada).
# 2. Si la web del HUD (5173) no responde, la arranca.
# 3. Si el oyente (escucha.py) no está o su marca de vida lleva más de 10 min sin
#    actualizarse, lo reinicia.
# Registro: ..\data\vigilante.log

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root 'data'
$venv = Join-Path $root 'src\.venv\Scripts'
$logFile = Join-Path $data 'vigilante.log'
$heartbeat = Join-Path $data 'escucha.heartbeat'
$staleSeconds = 600

function Write-Log([string]$message) {
    Add-Content -Path $logFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" -Encoding utf8
}

function Test-Api {
    foreach ($attempt in 1..2) {
        try {
            Invoke-WebRequest 'http://127.0.0.1:8000/health' -TimeoutSec 5 -UseBasicParsing | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 10
        }
    }
    return $false
}

try {
    $processes = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'"

    # 1. API de Jarvis
    if (-not (Test-Api)) {
        $servers = $processes | Where-Object { $_.CommandLine -match 'openjarvis\.cli serve' }
        if ($servers) {
            Write-Log 'La API no responde: reinicio el servidor'
            $servers | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        } else {
            Write-Log 'La API no está en marcha: arranco el servidor'
        }
        $env:OPENJARVIS_HOME = $data
        $env:OLLAMA_HOST = '127.0.0.1:11434'  # la app de Ollama ignora la variable del sistema (11435)
        Start-Process -FilePath (Join-Path $venv 'python.exe') `
            -ArgumentList '-m', 'openjarvis.cli', 'serve', '--port', '8000' `
            -WorkingDirectory (Join-Path $root 'src') -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $data 'server.log') `
            -RedirectStandardError (Join-Path $data 'server.err.log')
    }

    # 2. Web del HUD (Vite en el 5173)
    $webOk = $false
    try { Invoke-WebRequest 'http://127.0.0.1:5173/' -TimeoutSec 5 -UseBasicParsing | Out-Null; $webOk = $true } catch {}
    if (-not $webOk) {
        Write-Log 'La web (5173) no responde: la arranco'
        $env:OPENJARVIS_VITE_PROXY_TARGET = 'http://127.0.0.1:8000'
        $env:VITE_API_URL = ''
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort' `
            -WorkingDirectory (Join-Path $root 'src\frontend') -WindowStyle Hidden
    }

    # 3. Oyente manos libres
    $listeners = @($processes | Where-Object { $_.CommandLine -like '*escucha.py*' })
    $reason = $null
    if ($listeners.Count -eq 0) {
        $reason = 'El oyente no está en marcha'
    } else {
        $oldest = ($listeners | ForEach-Object { $_.CreationDate } | Sort-Object | Select-Object -First 1)
        $runningFor = ((Get-Date) - $oldest).TotalSeconds
        $beatAge = if (Test-Path $heartbeat) { ((Get-Date) - (Get-Item $heartbeat).LastWriteTime).TotalSeconds } else { [double]::MaxValue }
        if ($runningFor -gt $staleSeconds -and $beatAge -gt $staleSeconds) {
            $reason = "El oyente no da señales desde hace $([int]$beatAge) s"
        }
    }
    if ($reason) {
        Write-Log "${reason}: lo reinicio"
        $listeners | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Process -FilePath (Join-Path $venv 'pythonw.exe') `
            -ArgumentList ('"' + (Join-Path $PSScriptRoot 'escucha.py') + '"') -WindowStyle Hidden
    }
} catch {
    Write-Log "Error del vigilante: $($_.Exception.Message)"
}

# 4. [Nau] Libera RAM (MCP huérfanos, modelo de Escriba sin uso) y limpia temporales
& (Join-Path $PSScriptRoot 'liberar-ram.ps1')
