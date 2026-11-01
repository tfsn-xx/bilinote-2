#requires -Version 7.0
$ErrorActionPreference = 'Stop'
if (-not (Test-Path 'HKCU:\Software\Classes\bilinote-cookie')) { & (Join-Path $PSScriptRoot 'install.ps1') }
Start-Process -FilePath 'bilinote-cookie://start' -WindowStyle Hidden
