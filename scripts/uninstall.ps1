#Requires -Version 5.1
<#
    uninstall.ps1 -- reverse install.ps1.

    Removes everything install.ps1 / run-server.ps1 created inside this project:
      * running app + recorder daemon
      * the Docker ASR container and image
      * .venv, models, config.ini, generated logs / temp files, __pycache__

    Leaves ALL source files and .git untouched. To delete the project entirely,
    remove the folder afterwards.

    Options:
      -KeepModels        keep .\models (large; slow to re-download)
      -KeepImage         keep the saykey-asr Docker image
      -RemoveAutoHotkey  also `winget uninstall AutoHotkey.AutoHotkey`
      -RemovePython      also `winget uninstall Python.Python.3.12`
      -DryRun            show what would be removed, change nothing
      -Yes               do not prompt

    Usage:
      powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
      .\uninstall.ps1 -KeepModels -Yes
#>
[CmdletBinding()]
param(
    [switch]$KeepModels,
    [switch]$KeepImage,
    [switch]$RemoveAutoHotkey,
    [switch]$RemovePython,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir          # project root (scripts/ is one level down)
Set-Location $root

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Step($m) { Write-Host "`n== $m" -ForegroundColor White }
function Ask($m) {
    if ($Yes) { return $true }
    $a = Read-Host "$m [y/N]"
    return ($a -match '^(y|yes)$')
}

function Remove-Path($path, $desc) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    if ($DryRun) { Warn "would remove $desc  ($path)"; return }
    try {
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
        Ok "removed $desc"
    }
    catch { Warn "could not remove $desc : $($_.Exception.Message)" }
}

function Invoke-Native([scriptblock]$sb) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $sb } finally { $ErrorActionPreference = $prev }
}

Write-Host "`n=== Saykey uninstaller ===" -ForegroundColor White
if ($DryRun) { Warn "DRY RUN -- nothing will be changed" }
if (-not $Yes -and -not $DryRun) {
    if (-not (Ask "Remove the local environment (.venv, models, config.ini, Docker image)?")) {
        Write-Host "aborted."; exit 0
    }
}

# --- 1. stop the running app + recorder daemon -------------------------
Step "Stopping Saykey processes"
# the resident daemon records its PID here
$upFile = Join-Path $env:TEMP 'saykey_ctl\up'
if (Test-Path $upFile) {
    $dpid = (Get-Content $upFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($dpid -match '^\d+$') {
        if ($DryRun) { Warn "would stop recorder daemon PID $dpid" }
        else { Stop-Process -Id ([int]$dpid) -Force -ErrorAction SilentlyContinue; Ok "stopped recorder daemon" }
    }
}
# the desktop UI (python -m ui)
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match '\bui\b' -and $_.CommandLine -match '-m' -and $_.CommandLine -match [regex]::Escape($root) } |
    ForEach-Object {
        if ($DryRun) { Warn "would stop desktop UI PID $($_.ProcessId)" }
        else { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Ok "stopped desktop UI (PID $($_.ProcessId))" }
    }
# AutoHotkey instances running this script
$ahkProcs = Get-CimInstance Win32_Process -Filter "Name='AutoHotkey64.exe' OR Name='AutoHotkey32.exe' OR Name='AutoHotkey.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'saykey\.ahk' }
foreach ($p in $ahkProcs) {
    if ($DryRun) { Warn "would stop AutoHotkey PID $($p.ProcessId)" }
    else { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Ok "stopped saykey.ahk (PID $($p.ProcessId))" }
}
# UI "launch on startup" registry entry
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (Get-ItemProperty -Path $runKey -Name 'Saykey' -ErrorAction SilentlyContinue) {
    if ($DryRun) { Warn "would remove HKCU Run value 'Saykey'" }
    else { Remove-ItemProperty -Path $runKey -Name 'Saykey' -Force; Ok "removed 'launch on startup' registry entry" }
}
# any stray record.py in this venv
$venvPyw = Join-Path $root '.venv\Scripts'
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'record\.py' -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venvPyw) } |
    ForEach-Object {
        if ($DryRun) { Warn "would stop record.py PID $($_.ProcessId)" }
        else { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Ok "stopped record.py (PID $($_.ProcessId))" }
    }
if (-not $ahkProcs -and -not $DryRun) { Ok "no saykey.ahk running" }
Info "hotkeys are only registered while saykey.ahk runs -- stopping it deregisters them"

# auto-start shortcuts pointing at this project (install.ps1 doesn't create one,
# but the README suggests adding it manually)
$startupDirs = @(
    [Environment]::GetFolderPath('Startup'),
    [Environment]::GetFolderPath('CommonStartup')
)
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in $startupDirs) {
    if (-not (Test-Path $dir)) { continue }
    Get-ChildItem -LiteralPath $dir -Filter '*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
        $t = ""
        try { $t = ($sh.CreateShortcut($_.FullName)).TargetPath + " " + ($sh.CreateShortcut($_.FullName)).Arguments } catch {}
        if ($t -match [regex]::Escape($root) -and $t -match 'saykey|run-agent|run-ui|\bui\b') {
            Remove-Path $_.FullName "auto-start shortcut ($($_.Name))"
        }
    }
}

# --- 2. Docker ASR container + image ---------------------------------
Step "Removing the Docker ASR server"
$dockerOk = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Invoke-Native { docker info 2>&1 | Out-Null }
    $dockerOk = ($LASTEXITCODE -eq 0)
}
if ($dockerOk) {
    $img = "saykey-asr:latest"
    if (Test-Path (Join-Path $root 'config.ini')) {
        # honour a custom [server] image tag
        $line = Select-String -Path (Join-Path $root 'config.ini') -Pattern '^\s*image\s*=\s*(.+)$' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($line) { $img = $line.Matches[0].Groups[1].Value.Trim() }
    }
    $composeF = Join-Path $root 'server\docker-compose.yml'
    $rmi = if ($KeepImage) { "none" } else { "local" }
    if ($DryRun) {
        Warn "would run: docker compose -f server\docker-compose.yml down --remove-orphans --volumes --rmi $rmi"
        Warn "would run: docker rm -f saykey-asr"
        if (-not $KeepImage) { Warn "would run: docker image rm -f $img" }
    }
    else {
        Invoke-Native { docker compose -f $composeF down --remove-orphans --volumes --rmi $rmi 2>&1 | Out-Null }
        # belt-and-braces: the container / image by name, in case compose state is gone
        Invoke-Native { docker rm -f saykey-asr 2>&1 | Out-Null }
        Ok "ASR server stopped and container removed"
        if (-not $KeepImage) {
            Invoke-Native { docker image rm -f $img 2>&1 | Out-Null }
            Ok "image $img removed (if it existed)"
        }
        else { Info "kept image $img (-KeepImage)" }
    }
}
else {
    Warn "Docker not running -- container/image (if any) left in place."
    Warn "Start Docker Desktop and re-run, or:  docker rm -f saykey-asr ; docker image rm saykey-asr:latest"
}

# --- 3. project-local files ----------------------------------------
Step "Removing generated files in the project"
Remove-Path (Join-Path $root '.venv')        "Python virtual environment (.venv)"
if ($KeepModels) { Info "kept .\models (-KeepModels)" }
else { Remove-Path (Join-Path $root 'models') "downloaded models (.\models)" }
Remove-Path (Join-Path $root 'config.ini')    "config.ini (generated from config.example.ini)"
Remove-Path (Join-Path $root 'server\.env')   "server\.env"
foreach ($pc in 'recorder\__pycache__', 'server\__pycache__', 'ui\__pycache__', 'ui\widgets\__pycache__') {
    Remove-Path (Join-Path $root $pc) $pc
}
# stray transcripts / recordings / logs in the project (never recurse -- .venv etc.)
foreach ($dir in @($root, (Join-Path $root 'recorder'), (Join-Path $root 'agent'))) {
    if (-not (Test-Path $dir)) { continue }
    foreach ($pat in 'transcript.txt', '*.log', '*.wav') {
        Get-ChildItem -Path $dir -Filter $pat -File -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Path $_.FullName $_.Name }
    }
}

# --- 4. temp / IPC files ------------------------------------------
Step "Removing temp files"
Remove-Path (Join-Path $env:TEMP 'saykey_ctl')       "recorder control directory"
Get-ChildItem -LiteralPath $env:TEMP -Filter 'saykey*' -ErrorAction SilentlyContinue | ForEach-Object { Remove-Path $_.FullName "%TEMP%\$($_.Name)" }

# --- 5. optional: winget-installed prerequisites -----------------
if ($RemoveAutoHotkey -or $RemovePython) {
    Step "Removing prerequisites (winget)"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Warn "winget not found" }
    else {
        if ($RemoveAutoHotkey -and (Ask "Uninstall AutoHotkey v2 system-wide?")) {
            if ($DryRun) { Warn "would run: winget uninstall AutoHotkey.AutoHotkey" }
            else { winget uninstall --id AutoHotkey.AutoHotkey -e --accept-source-agreements }
        }
        if ($RemovePython -and (Ask "Uninstall Python 3.12 system-wide? (only if nothing else uses it)")) {
            if ($DryRun) { Warn "would run: winget uninstall Python.Python.3.12" }
            else { winget uninstall --id Python.Python.3.12 -e --accept-source-agreements }
        }
    }
}

Write-Host ""
Ok "Uninstall complete."
Write-Host @"

  Left untouched: all source files, config.example.ini, and .git
  Still installed (by design): AutoHotkey / Python / Docker Desktop
      -> pass -RemoveAutoHotkey / -RemovePython to also remove those
  You may also want to clear the shared HF cache:  $env:USERPROFILE\.cache\huggingface
  To remove the project entirely, delete this folder.
"@ -ForegroundColor Gray
