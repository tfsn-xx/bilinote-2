#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$helperPath = Join-Path $PSScriptRoot 'server.mjs'
Start-Process -FilePath $nodePath -ArgumentList @('"' + $helperPath + '"') -WindowStyle Hidden -WorkingDirectory $PSScriptRoot
