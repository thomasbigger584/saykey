#Requires -Version 5.1
# Launch vdi-dictate.ahk with whichever AutoHotkey v2 runtime is installed.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$candidates = @(
    "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey64.exe",
    "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey32.exe",
    "$env:ProgramFiles\AutoHotkey\AutoHotkey64.exe",
    "${env:ProgramFiles(x86)}\AutoHotkey\v2\AutoHotkey64.exe",
    "$env:LOCALAPPDATA\Programs\AutoHotkey\v2\AutoHotkey64.exe"
)
$ahk = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $ahk) {
    $c = Get-Command AutoHotkey64.exe, AutoHotkey.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $ahk = $c.Source }
}
if (-not $ahk) {
    Write-Error "AutoHotkey v2 not found. Run install.ps1 or install it from https://www.autohotkey.com/"
    exit 1
}

$script = Join-Path $root "vdi-dictate.ahk"
Write-Host "Starting VDI Dictate ..." -ForegroundColor Cyan
Start-Process -FilePath $ahk -ArgumentList "`"$script`""
