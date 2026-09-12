#Requires -Version 5.1
# run.ps1 -- start Saykey.
#
# Launches the desktop tray app, which in turn starts the ASR server (a local
# process, no Docker) and the Windows dictation agent.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$installer = Join-Path $root 'scripts\install.ps1'
$pyw = Join-Path $root '.venv\Scripts\pythonw.exe'
$py = Join-Path $root '.venv\Scripts\python.exe'

function Write-Note($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Write-Bad($m) { Write-Host "  $m" -ForegroundColor Yellow }

function Confirm-YesNo($prompt) {
    try { $a = Read-Host "  $prompt [Y/n]" } catch { return $false }
    return ($a -eq "" -or $a -match '^(y|yes)$')
}

# ---- is Saykey set up on this machine? -------------------------------------
if (-not (Test-Path $py)) {
    Write-Host ""
    Write-Bad "Saykey isn't set up on this machine yet (no .venv)."
    Write-Bad "It needs a one-time install: a Python virtual environment, the"
    Write-Bad "desktop UI, the offline fallback model, and the ASR server dependencies."
    Write-Host ""
    Write-Host "    powershell -ExecutionPolicy Bypass -File `"$installer`"" -ForegroundColor White
    Write-Host ""

    if (-not (Test-Path $installer)) {
        Write-Bad "install.ps1 is missing from scripts\ -- re-clone the project."
        exit 1
    }

    $canPrompt = [Environment]::UserInteractive -and -not $env:SAYKEY_NO_PROMPT
    if ($canPrompt -and (Confirm-YesNo "Run the installer now?")) {
        & $installer
        if (-not (Test-Path $py)) {
            Write-Host ""
            Write-Bad "Install didn't finish -- fix the errors above, then run run.ps1 again."
            exit 1
        }
        Write-Host ""
        Write-Note "Install complete -- starting Saykey ..."
    }
    else {
        exit 1
    }
}

# ---- venv exists but the UI package is missing (interrupted install) -------
& $py -c "import PySide6" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Note "Finishing setup: installing the desktop UI (PySide6) ..."
    & $py -m pip install -r (Join-Path $root 'ui\requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "Could not install the desktop UI. Run install.ps1 to repair the setup."
        exit 1
    }
}

# ---- launch ---------------------------------------------------------------
$exe = if (Test-Path $pyw) { $pyw } else { $py }
Start-Process -FilePath $exe -ArgumentList '-m', 'ui' -WorkingDirectory $root
Write-Host "  Saykey started -- look for the tray icon." -ForegroundColor Green
