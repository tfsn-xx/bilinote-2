#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
if ([int]((& $nodePath --version).TrimStart('v').Split('.')[0]) -lt 22) { throw '需要 Node.js 22 或更新版本。' }
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$installRoot = Join-Path ([Environment]::GetFolderPath('CommonDocuments')) ('BiliNote\' + $userSid)
$destination = Join-Path $installRoot 'cookie-helper'
$compiler = Get-ChildItem (Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4*/csc.exe') | Select-Object -First 1 -ExpandProperty FullName
if (-not $compiler) { throw '未找到 Windows .NET Framework C# 编译器。' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
& icacls.exe $installRoot /inheritance:r /grant:r ("*${userSid}:(OI)(CI)F") '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw '安装目录权限设置失败' }
foreach ($taskName in @('BiliNoteCookieDockerBridge', 'BiliNoteCookieHelper')) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name BiliNoteCookieHelper -ErrorAction SilentlyContinue
# Stop only the old helper processes in this installation, leaving other Node apps alone.
Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($destination) -and ($_.CommandLine.Contains('launch.cjs') -or $_.CommandLine.Contains('server.mjs'))
} | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
foreach ($name in @('server.mjs', 'browser.mjs', 'on-demand.mjs', 'launch.cjs', 'register-protocol.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $destination $name) -Force
}
$profile = Join-Path $installRoot 'cookie-browser'
$oldProfile = Join-Path $env:LOCALAPPDATA 'BiliNote\cookie-browser'
if ((Test-Path -LiteralPath $oldProfile) -and -not (Test-Path -LiteralPath $profile)) {
    Copy-Item -LiteralPath $oldProfile -Destination $profile -Recurse
}
@{ profile = $profile } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'runtime.json') -Encoding utf8
Set-Content -LiteralPath (Join-Path $destination 'node-path.txt') -Value $nodePath -Encoding utf8
$launcher = Join-Path $destination 'BiliNoteCookieHelper.exe'
& $compiler /nologo /target:winexe ("/out:$launcher") (Join-Path $PSScriptRoot 'launcher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Cookie 助手启动器编译失败' }
# Install from the interactive desktop session: isolated shells can see a private HKCU view.
$resultPath = Join-Path $destination 'protocol-install-result.json'
Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue
$taskName = 'BiliNoteCookieProtocolInstall'
$pwshPath = (Get-Process -Id $PID).Path
$action = New-ScheduledTaskAction -Execute $pwshPath -Argument ('-NoProfile -WindowStyle Hidden -File "' + (Join-Path $destination 'register-protocol.ps1') + '"')
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
try {
    Start-ScheduledTask -TaskName $taskName
    $deadline = (Get-Date).AddSeconds(20)
    while (-not (Test-Path -LiteralPath $resultPath)) {
        if ((Get-Date) -gt $deadline) { throw '桌面会话中的 Cookie 助手协议安装超时。' }
        Start-Sleep -Milliseconds 200
    }
    $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
    if (-not $result.success) { throw $result.error }
} finally {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Output '已安装按需 Cookie 助手。点击获取按钮时后台启动，获取结束后自动退出，无常驻任务和命令窗口。'
