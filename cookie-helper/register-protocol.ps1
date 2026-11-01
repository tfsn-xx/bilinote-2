#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$resultPath = Join-Path $PSScriptRoot 'protocol-install-result.json'
try {
    $launcher = Join-Path $PSScriptRoot 'BiliNoteCookieHelper.exe'
    if (-not (Test-Path -LiteralPath $launcher)) { throw 'Cookie 助手启动器不存在。' }
    $protocol = 'HKCU:\Software\Classes\bilinote-cookie'
    New-Item -Path (Join-Path $protocol 'shell\open\command') -Force | Out-Null
    Set-Item -LiteralPath $protocol -Value 'URL:BiliNote Cookie 助手'
    New-ItemProperty -LiteralPath $protocol -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
    Set-Item -LiteralPath (Join-Path $protocol 'shell\open\command') -Value ('"' + $launcher + '"')
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class BiliNoteAssociationNotification {
    [DllImport("shell32.dll")]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
'@
    [BiliNoteAssociationNotification]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
    @{ success = $true } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding utf8
} catch {
    @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding utf8
    exit 1
}
