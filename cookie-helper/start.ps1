#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName BiliNoteCookieDockerBridge -ErrorAction SilentlyContinue
if ($task) { Start-ScheduledTask -TaskName BiliNoteCookieDockerBridge }
else { & (Join-Path $PSScriptRoot 'install.ps1') }
