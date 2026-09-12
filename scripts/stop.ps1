#Requires -Version 5.1
<#
    stop.ps1 -- stop everything Saykey is running. Does NOT uninstall.

    Stops the tray app, the dictation agent (which deregisters the global
    hotkey and removes the tray icon), the resident recorder, and the local
    ASR server process. Clears the transient signal files in %TEMP%.

    Left untouched: .venv, models, config.ini, the HuggingFace cache, and
    the "launch on startup" setting. run.ps1 brings it all straight back;
    uninstall.ps1 is the one that removes things.

      .\stop.ps1                stop everything
      .\stop.ps1 -KeepServer    leave the ASR server process running
#>
[CmdletBinding()]
param(
    [switch]$KeepServer
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "  [ok] $m" -ForegroundColor Green }

function Stop-Proc($proc, $desc) {
    try {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        Ok "stopped $desc (PID $($proc.ProcessId))"
        return 1
    }
    catch { return 0 }
}

Write-Host "`n=== Stopping Saykey ===`n"

$ctl = Join-Path $env:TEMP 'saykey_ctl'
$venvScripts = Join-Path $root '.venv\Scripts'
$rootRe = [regex]::Escape($root)
$stopped = 0

# 1. ask the agent + recorder to quit cleanly (deregisters the hotkey, frees the mic)
if (Test-Path $ctl) {
    foreach ($f in 'ui.quit', 'quit') {
        try { Set-Content -LiteralPath (Join-Path $ctl $f) -Value '1' -ErrorAction Stop } catch {}
    }
    Start-Sleep -Milliseconds 600
}

$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue

# 2. dictation agent (AutoHotkey running saykey.ahk) -- deregisters the hotkey
foreach ($p in $procs | Where-Object {
        $_.Name -match '^AutoHotkey' -and $_.CommandLine -match 'saykey\.ahk' }) {
    $stopped += Stop-Proc $p "dictation agent"
}

# 3. desktop UI  (python -m ui, from this project)
foreach ($p in $procs | Where-Object {
        ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and
        $_.CommandLine -match '-m\s+ui\b' -and $_.CommandLine -match $rootRe }) {
    $stopped += Stop-Proc $p "desktop UI"
}

# 4. resident recorder (record.py in this venv) -- usually already gone with the agent
foreach ($p in $procs | Where-Object {
        ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and
        $_.CommandLine -match 'record\.py' -and $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($venvScripts, [StringComparison]::OrdinalIgnoreCase) }) {
    $stopped += Stop-Proc $p "recorder daemon"
}
# ... and the PID the daemon records, in case the scan missed it
$upFile = Join-Path $ctl 'up'
if (Test-Path $upFile) {
    $dpid = Get-Content -LiteralPath $upFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($dpid -match '^\d+$' -and (Get-Process -Id ([int]$dpid) -ErrorAction SilentlyContinue)) {
        try {
            Stop-Process -Id ([int]$dpid) -Force -ErrorAction Stop
            Ok "stopped recorder daemon (PID $dpid)"; $stopped++
        }
        catch {}
    }
}

if ($stopped -eq 0) { Info "no Saykey client processes were running" }

# transient IPC / last transcript -- regenerated on the next run
if (Test-Path $ctl) {
    try { Remove-Item -LiteralPath $ctl -Recurse -Force -ErrorAction Stop; Ok "cleared runtime signal files" } catch {}
}

# 5. local ASR server process (server/app.py via uvicorn, in this venv)
if ($KeepServer) {
    Info "left the ASR server running (-KeepServer)"
}
else {
    $found = $false
    foreach ($p in $procs | Where-Object {
            ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and
            $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app:app' -and
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venvScripts, [StringComparison]::OrdinalIgnoreCase) }) {
        $found = $true
        $stopped += Stop-Proc $p "ASR server"
    }
    if (-not $found) { Info "ASR server not running" }
}

Write-Host ""
Ok "Stopped. Nothing was removed -- scripts\run.ps1 starts it again."
