#Requires -Version 5.1
<#
    install.ps1 -- one-shot, standalone setup for Saykey (client side).

    Creates every dependency on the machine and then STOPS. It starts nothing --
    run.ps1 does that.

    - checks for / installs AutoHotkey v2 and Python (via winget, with consent)
    - creates .venv and installs the recorder + desktop-UI + ASR server dependencies
    - picks onnxruntime vs onnxruntime-gpu based on detected GPU
    - downloads the faster-whisper fallback model
    - writes config.ini

    No Docker required -- the ASR server (Parakeet by default) runs as a
    plain local process in this same .venv, managed by the app itself.

    Usage:
      powershell -ExecutionPolicy Bypass -File .\install.ps1
      .\install.ps1 -Engine parakeet-v3     # multilingual Parakeet
      .\install.ps1 -Backend local          # Whisper-only, no ASR server install
      .\install.ps1 -Yes -SkipModel
#>
[CmdletBinding()]
param(
    [ValidateSet("server", "local")] [string]$Backend = "server",
    [string]$Engine = "parakeet",
    [string]$WhisperModel = "base.en",
    [switch]$InstallHF,
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

# --- Python 3.10+ (onnx-asr, the default Parakeet engine, needs 3.10+) ----
Info "Checking Python 3.10+ ..."
$pyExe = $null; $pyPre = @(); $pyver = $null; $seen = $null
$pyCandidates = @(
    @{e = "py"; a = @("-3.13") }, @{e = "py"; a = @("-3.12") }, @{e = "py"; a = @("-3.11") },
    @{e = "py"; a = @("-3.10") }, @{e = "py"; a = @("-3") },
    @{e = "python"; a = @() }, @{e = "python3"; a = @() }
)
foreach ($l in $pyCandidates) {
    if (-not (Get-Command $l.e -ErrorAction SilentlyContinue)) { continue }
    try { $v = & $l.e @($l.a) -c "import sys;print('{}.{}'.format(*sys.version_info[:2]))" 2>$null } catch { continue }
    if (-not $v) { continue }
    $seen = $v.Trim(); $mm = $seen.Split(".")
    if ([int]$mm[0] -eq 3 -and [int]$mm[1] -ge 10) { $pyExe = $l.e; $pyPre = $l.a; $pyver = $seen; break }
}
if (-not $pyExe) {
    Warn "No suitable Python found (need 3.10+; saw '$seen')."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install Python 3.12 with winget?")) {
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        Die "Python installed. Open a NEW PowerShell window and run install.ps1 again."
    }
    Die "Install Python 3.10+ from https://www.python.org/downloads/ and re-run."
}
Ok "Python $pyver"

# --- virtual environment ---------------------------------------------
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $existingVer = $null
    try { $existingVer = (& $venvPy -c "import sys;print('{}.{}'.format(*sys.version_info[:2]))" 2>$null).Trim() } catch {}
    $emm = if ($existingVer) { $existingVer.Split(".") } else { @() }
    if (-not $existingVer -or [int]$emm[0] -ne 3 -or [int]$emm[1] -lt 10) {
        Warn "Existing .venv uses Python $existingVer, but the ASR server needs 3.10+."
        if (Ask "Delete and recreate .venv with Python ${pyver}?") {
            Remove-Item -Recurse -Force (Join-Path $root ".venv")
        }
        else { Die "Cannot continue with an incompatible .venv. Delete .venv manually and re-run." }
    }
}
Info "Creating virtual environment (.venv) ..."
if (-not (Test-Path $venvPy)) { & $pyExe @pyPre -m venv .venv }
if (-not (Test-Path $venvPy)) { Die "venv creation failed." }
Ok "venv ready"

Info "Installing Python dependencies ..."
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r (Join-Path $root "recorder\requirements.txt")
if ($LASTEXITCODE -ne 0) { Die "pip install failed." }
Ok "dependencies installed"

Info "Installing desktop UI dependencies (PySide6) ..."
& $venvPy -m pip install -r (Join-Path $root "ui\requirements.txt")
if ($LASTEXITCODE -eq 0) { Ok "desktop UI installed" }
else { Warn "UI dependency install failed; re-run, or 'pip install -r ui\requirements.txt' into .venv." }

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

# --- ASR server dependencies (no Docker -- a plain local process) ----
if ($Backend -eq "server") {
    Info "Installing ASR server dependencies ..."
    & $venvPy -m pip install -r (Join-Path $root "server\requirements.txt")
    if ($LASTEXITCODE -ne 0) { Warn "server dependency install failed; re-run, or 'pip install -r server\requirements.txt' into .venv." }

    Info "Checking for an NVIDIA GPU ..."
    $gpu = $false
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        nvidia-smi -L 2>&1 | Out-Null
        $gpu = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $eap
    }
    $ortPkg = if ($gpu) { "onnxruntime-gpu" } else { "onnxruntime" }
    Info "Installing $ortPkg ..."
    # onnxruntime and onnxruntime-gpu both install the same `onnxruntime` module and
    # cannot coexist cleanly -- remove whichever is already there before switching.
    & $venvPy -m pip uninstall -y onnxruntime onnxruntime-gpu --quiet 2>$null | Out-Null
    & $venvPy -m pip install $ortPkg
    if ($LASTEXITCODE -eq 0) { Ok "$ortPkg installed ($(if ($gpu) { 'GPU detected' } else { 'no GPU detected' }))" }
    else { Warn "$ortPkg install failed; re-run, or 'pip install $ortPkg' into .venv." }

    if ($InstallHF) {
        Info "Installing optional HuggingFace/transformers engine support (-InstallHF) ..."
        & $venvPy -m pip install -r (Join-Path $root "server\requirements-hf.txt")
        if ($LASTEXITCODE -eq 0) { Ok "transformers engine support installed" }
        else { Warn "HF dependency install failed; re-run with -InstallHF, or install server\requirements-hf.txt by hand." }
    }
}

Write-Host ""
Ok "Setup complete -- nothing is running yet."
Write-Host @"

  Start / stop Saykey
  -------------------
    scripts\run.ps1     start the tray app (-> ASR server + dictation agent)
    scripts\stop.ps1    stop all of it again (nothing is uninstalled)

  Right-click the tray icon for Settings.

  Swap the transcription model
  ----------------------------
    Settings -> Models,  or  edit config.ini -> [server] engine
      (parakeet | parakeet-v3 | canary | whisper | onnx:<id> | fw:<id> | hf:<id>)
    then:  scripts\run-server.ps1 restart

  Use it
  ------
    1. Click into your VDI / remote window.
    2. Hold Ctrl+Space (or use the floating talk button), speak, release.

  If the VDI drops/repeats characters or capitalises everything:
    config.ini -> [injection] mode = Text
"@ -ForegroundColor Gray

if (-not $ahk) { Warn "AutoHotkey v2 is still required -- install it, then run.ps1" }
