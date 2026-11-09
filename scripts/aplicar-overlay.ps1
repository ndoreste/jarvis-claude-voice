<#
.SYNOPSIS
  Aplica los cambios de Jarvis sobre una copia de OpenJarvis.

.DESCRIPTION
  Comprueba que OpenJarvis está en la versión con la que se probó Jarvis (UPSTREAM_COMMIT)
  y copia encima los archivos de openjarvis-overlay\. Avisa si la versión es otra, porque
  los archivos modificados podrían no encajar.

.EXAMPLE
  .\scripts\aplicar-overlay.ps1 -OpenJarvis C:\Users\<usuario>\claudeProjects\jarvis\src
#>
param(
    [Parameter(Mandatory)][string]$OpenJarvis,
    [switch]$Forzar
)
$ErrorActionPreference = 'Stop'
$overlay = Resolve-Path (Join-Path $PSScriptRoot '..\openjarvis-overlay')
$target = Resolve-Path $OpenJarvis
$expected = (Get-Content (Join-Path $overlay 'UPSTREAM_COMMIT') -Raw).Trim()
$current = (git -C $target rev-parse HEAD).Trim()

if ($current -ne $expected -and -not $Forzar) {
    Write-Host "OpenJarvis está en $current y Jarvis se probó en $expected." -ForegroundColor Yellow
    Write-Host "Ejecuta:  git -C `"$target`" checkout $expected   (o repite con -Forzar)." -ForegroundColor Yellow
    exit 1
}

foreach ($dir in 'src', 'frontend') {
    Copy-Item (Join-Path $overlay $dir) $target -Recurse -Force
}
Write-Host 'Cambios de Jarvis aplicados sobre OpenJarvis.' -ForegroundColor Green
git -C $target status --short
