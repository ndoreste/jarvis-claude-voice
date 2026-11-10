# Registra la tarea programada «Jarvis vigilante»: cada 2 minutos (y 3 minutos después
# de iniciar sesión) ejecuta vigilante.vbs, que levanta la API y el oyente si se han caído.
# Si la tarea ya existe y se registró con administrador, ejecútalo en un PowerShell como administrador (Acceso denegado si no). Para quitarla:
#   Unregister-ScheduledTask -TaskName 'Jarvis vigilante' -Confirm:$false

$vbs = Join-Path $PSScriptRoot 'vigilante.vbs'
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$vbs`""
$every2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT3M'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'Jarvis vigilante' -Force `
    -Description "Mantiene Jarvis encendido: levanta la API y el oyente si se caen ($PSScriptRoot\vigilante.ps1)." `
    -Action $action -Trigger @($every2, $logon) -Settings $settings -Principal $principal | Out-Null
Write-Host "Tarea 'Jarvis vigilante' registrada: revisa Jarvis cada 2 minutos." -ForegroundColor Green
