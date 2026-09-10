#Requires -Version 5.1
# Launch the Saykey desktop UI (tray app).
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pyw = Join-Path $root '.venv\Scripts\pythonw.exe'
$py = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Error "No .venv found. Run scripts\install.ps1 first."
    exit 1
}
& $py -c "import PySide6" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing UI dependencies (PySide6) ..." -ForegroundColor Cyan
    & $py -m pip install -r (Join-Path $root 'ui\requirements.txt')
}

$exe = if (Test-Path $pyw) { $pyw } else { $py }
Start-Process -FilePath $exe -ArgumentList '-m', 'ui' -WorkingDirectory $root
