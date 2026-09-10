#Requires -Version 5.1
<#
    install.ps1 -- one-shot setup for Saykey (client side).

    - checks for / installs AutoHotkey v2 and Python (via winget, with consent)
    - creates .venv and installs requirements.txt  (capture + local fallback)
    - downloads the faster-whisper fallback model
    - writes config.ini
    - optionally builds + starts the Docker ASR server (Parakeet)

    Usage:
      powershell -ExecutionPolicy Bypass -File .\install.ps1
      .\install.ps1 -Engine parakeet -Backend server -StartServer
      .\install.ps1 -Backend local -WhisperModel small.en
      .\install.ps1 -Yes -SkipModel
#>
[CmdletBinding()]
param(
    [ValidateSet("server", "local")] [string]$Backend = "server",
    [string]$Engine = "parakeet",
    [string]$WhisperModel = "base.en",
    [switch]$StartServer,
    [switch]$WithUI,
    [switch]$SkipModel,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir          # project root (scripts/ is one level down)
Set-Location $root

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "  [x]  $m" -ForegroundColor Red; exit 1 }
function Ask($m) {
    if ($Yes) { return $true }
    $a = Read-Host "$m [Y/n]"
    return ($a -eq "" -or $a -match '^(y|yes)$')
}

function Set-IniValue([string]$path, [string]$section, [string]$key, [string]$value) {
    $out = New-Object System.Collections.Generic.List[string]
    $cur = ""; $done = $false
    foreach ($line in (Get-Content $path)) {
        $t = $line.Trim()
        if ($t -match '^\[(.+)\]$') { $cur = $Matches[1].Trim() }
        if (-not $done -and $cur -eq $section -and $t -match "^$([regex]::Escape($key))\s*=") {
            $out.Add("$key = $value"); $done = $true; continue
        }
        $out.Add($line)
    }
    if (-not $done) { Warn "config.ini: [$section] $key not found (skipped)" }
    Set-Content -Path $path -Value $out -Encoding ASCII
}

Write-Host "`n=== Saykey installer ===`n"

# --- AutoHotkey v2 ------------------------------------------------------
Info "Checking AutoHotkey v2 ..."
$ahkCandidates = @(
    "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey64.exe",
    "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey32.exe",
    "$env:ProgramFiles\AutoHotkey\AutoHotkey64.exe",
    "${env:ProgramFiles(x86)}\AutoHotkey\v2\AutoHotkey64.exe",
    "$env:LOCALAPPDATA\Programs\AutoHotkey\v2\AutoHotkey64.exe"
)
$ahk = $ahkCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $ahk) {
    $c = Get-Command AutoHotkey64.exe, AutoHotkey.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $ahk = $c.Source }
}
if ($ahk) { Ok "AutoHotkey: $ahk" }
else {
    Warn "AutoHotkey v2 not found."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install AutoHotkey v2 with winget?")) {
        winget install --id AutoHotkey.AutoHotkey -e --accept-source-agreements --accept-package-agreements
        $ahk = $ahkCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if ($ahk) { Ok "AutoHotkey installed: $ahk" } else { Warn "Install v2 from https://www.autohotkey.com/" }
    }
    else { Warn "Install AutoHotkey v2 from https://www.autohotkey.com/ before running the agent" }
}

# --- Python 3.9+ ------------------------------------------------------
Info "Checking Python 3.9+ ..."
$pyExe = $null; $pyPre = @(); $pyver = $null; $seen = $null
foreach ($l in @(@{e = "py"; a = @("-3") }, @{e = "python"; a = @() }, @{e = "python3"; a = @() })) {
    if (-not (Get-Command $l.e -ErrorAction SilentlyContinue)) { continue }
    try { $v = & $l.e @($l.a) -c "import sys;print('{}.{}'.format(*sys.version_info[:2]))" 2>$null } catch { continue }
    if (-not $v) { continue }
    $seen = $v.Trim(); $mm = $seen.Split(".")
    if ([int]$mm[0] -eq 3 -and [int]$mm[1] -ge 9) { $pyExe = $l.e; $pyPre = $l.a; $pyver = $seen; break }
}
if (-not $pyExe) {
    Warn "No suitable Python found (need 3.9+; saw '$seen')."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install Python 3.12 with winget?")) {
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        Die "Python installed. Open a NEW PowerShell window and run install.ps1 again."
    }
    Die "Install Python 3.9+ from https://www.python.org/downloads/ and re-run."
}
Ok "Python $pyver"

# --- virtual environment ---------------------------------------------
Info "Creating virtual environment (.venv) ..."
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $pyExe @pyPre -m venv .venv }
if (-not (Test-Path $venvPy)) { Die "venv creation failed." }
Ok "venv ready"

Info "Installing Python dependencies ..."
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r (Join-Path $root "recorder\requirements.txt")
if ($LASTEXITCODE -ne 0) { Die "pip install failed." }
Ok "dependencies installed"

if ($WithUI -or (-not $Yes -and (Ask "Install the desktop UI (tray app + settings)?"))) {
    Info "Installing desktop UI dependencies (PySide6) ..."
    & $venvPy -m pip install -r (Join-Path $root "ui\requirements.txt")
    if ($LASTEXITCODE -eq 0) { Ok "UI installed -- launch it with:  scripts\run-ui.ps1" }
    else { Warn "UI dependency install failed; run 'pip install -r ui\requirements.txt' later." }
}

# --- config.ini ----------------------------------------------------
Info "Writing config.ini ..."
$cfg = Join-Path $root "config.ini"
if (-not (Test-Path $cfg)) { Copy-Item (Join-Path $root "config.example.ini") $cfg }
Set-IniValue $cfg "general" "python" ".venv\Scripts\pythonw.exe"
Set-IniValue $cfg "transcription" "backend" $Backend
Set-IniValue $cfg "server" "engine" $Engine
Set-IniValue $cfg "local" "model" $WhisperModel
Ok "config.ini ready (backend = $Backend, server engine = $Engine, fallback = $WhisperModel)"

# --- faster-whisper fallback model -------------------------------
if ($SkipModel) {
    Warn "Skipping fallback-model download (-SkipModel)."
}
else {
    Info "Downloading faster-whisper fallback model '$WhisperModel' ..."
    & $venvPy (Join-Path $root "recorder\record.py") --backend local --config $cfg --warmup
    if ($LASTEXITCODE -eq 0) {
        Ok "fallback model ready"
        Set-IniValue $cfg "general" "offline" "true"
        Ok "offline mode enabled for the local fallback"
    }
    else { Warn "Fallback model download failed; re-run later or use the tray menu." }
}

# --- audio devices -----------------------------------------------
Info "Audio input devices:"
& $venvPy (Join-Path $root "recorder\record.py") --list-devices

# --- Docker ASR server -----------------------------------------
if ($Backend -eq "server") {
    Info "Checking Docker (for the Parakeet ASR server) ..."
    $dockerOk = $false
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try { docker info 2>&1 | Out-Null; $dockerOk = ($LASTEXITCODE -eq 0) } catch { $dockerOk = $false }
        $ErrorActionPreference = $eap
    }
    if ($dockerOk) {
        Ok "Docker is running."
        if ($StartServer -or (Ask "Build and start the ASR server now? (first build pulls several GB)")) {
            & (Join-Path $scriptDir "run-server.ps1") up
        }
        else {
            Info "Start it later with:   scripts\run-server.ps1 up"
        }
    }
    else {
        Warn "Docker not available. The client will use the local Whisper fallback until you run:"
        Warn "  (start Docker Desktop)  then   scripts\run-server.ps1 up"
    }
}

Write-Host ""
Ok "Setup complete."
$howToRun = if ($ahk) { "`"$ahk`" `"$root\agent\saykey.ahk`"" } else { "(install AutoHotkey v2 first)" }
Write-Host @"

  Start dictation
  ---------------
    * desktop UI:   scripts\run-ui.ps1
    * agent only:   powershell -ExecutionPolicy Bypass -File "$root\scripts\run-agent.ps1"
    * or:  $howToRun

  Swap the transcription model
  ----------------------------
    edit config.ini -> [server] engine   (parakeet | parakeet-v3 | canary |
                                          whisper | onnx:<id> | fw:<id> | hf:<id>)
    then:  scripts\run-server.ps1 restart   (or the UI's Models tab)

  Use it
  ------
    1. Click into your VDI / remote window.
    2. Hold Ctrl+Space, speak, release. Text is typed in -- no clipboard.

  If the VDI drops/repeats characters: config.ini -> [injection] mode = Event
"@ -ForegroundColor Gray

if (-not $ahk) { Warn "AutoHotkey v2 is still required to run the hotkey script." }
