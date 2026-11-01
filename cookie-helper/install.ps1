#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$dockerPath = (Get-Command docker.exe -ErrorAction Stop).Source
if ([int]((& $nodePath --version).TrimStart('v').Split('.')[0]) -lt 22) { throw '需要 Node.js 22 或更新版本。' }
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$installRoot = Join-Path ([Environment]::GetFolderPath('CommonDocuments')) ('BiliNote\' + $userSid)
$destination = Join-Path $installRoot 'cookie-helper'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
& icacls.exe $installRoot /inheritance:r /grant:r ("*${userSid}:(OI)(CI)F") '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw '安装目录权限设置失败' }
foreach ($name in @('server.mjs', 'browser.mjs', 'lifecycle.mjs', 'docker-monitor.mjs', 'launch.cjs')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $destination $name) -Force
}
$profile = Join-Path $installRoot 'cookie-browser'
$oldProfile = Join-Path $env:LOCALAPPDATA 'BiliNote\cookie-browser'
if ((Test-Path -LiteralPath $oldProfile) -and -not (Test-Path -LiteralPath $profile)) {
    Copy-Item -LiteralPath $oldProfile -Destination $profile -Recurse
}
@{ dockerPath = $dockerPath; container = 'bilinote-2'; profile = $profile } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'runtime.json') -Encoding utf8
$action = New-ScheduledTaskAction -Execute $nodePath -Argument ('"' + (Join-Path $destination 'launch.cjs') + '"')
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
# A timer recovers the bridge after sign-in or a crash; there is no logon trigger.
# Duplicate monitor starts exit through the named-pipe singleton.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName BiliNoteCookieDockerBridge -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description '仅监听 Docker 状态；Cookie 服务随 bilinote-2 容器启动停止。' -Force | Out-Null
Unregister-ScheduledTask -TaskName BiliNoteCookieHelper -Confirm:$false -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name BiliNoteCookieHelper -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName BiliNoteCookieDockerBridge
Write-Output '已安装 Docker 状态桥接。Cookie 服务只在 bilinote-2 容器运行时开放。'
