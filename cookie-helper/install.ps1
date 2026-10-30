#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$pwshPath = (Get-Process -Id $PID).Path
$startScript = Join-Path $PSScriptRoot 'start.ps1'
$runCommand = '"' + $pwshPath + '" -NoProfile -WindowStyle Hidden -File "' + $startScript + '"'
New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name BiliNoteCookieHelper -Value $runCommand -PropertyType String -Force | Out-Null
& $startScript
Write-Output 'Cookie 获取服务已启动，并设为登录 Windows 后自动启动。请保留此项目目录。'
