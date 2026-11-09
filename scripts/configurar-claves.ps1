# Guarda las claves de Jarvis como variables de entorno de tu usuario.
# Las escribes tú: no se muestran en pantalla ni se guardan en ningún archivo.
# Uso: clic derecho > "Ejecutar con PowerShell", o en PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\configurar-claves.ps1

function Read-Secret([string] $prompt) {
    $secure = Read-Host -Prompt $prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

$claves = @(
    @{ Nombre = 'OPENROUTER_API_KEY'; Texto = 'Pega la clave de OpenRouter (sk-or-v1-...) y pulsa Enter' },
    @{ Nombre = 'ELEVENLABS_API_KEY'; Texto = 'Pega la clave de ElevenLabs (o pulsa Enter para dejarla para luego)' }
)

foreach ($c in $claves) {
    $valor = Read-Secret $c.Texto
    if ([string]::IsNullOrWhiteSpace($valor)) {
        Write-Host "  - $($c.Nombre): sin cambios" -ForegroundColor Yellow
        continue
    }
    [Environment]::SetEnvironmentVariable($c.Nombre, $valor, 'User')
    Write-Host "  - $($c.Nombre): guardada" -ForegroundColor Green
}

Write-Host ""
Write-Host "Listo. Cierra esta ventana. Las claves se aplican a los programas que abras a partir de ahora." -ForegroundColor Cyan
Read-Host "Pulsa Enter para salir"
