#Requires -Version 5.1
<#
    run-server.ps1 -- manage the local Saykey ASR server (no Docker).

    Runs server/app.py directly with the project's own .venv via `uvicorn`.
    The server-side model is chosen in config.ini -> [server] engine.
    Change it, then:  .\run-server.ps1 restart

      .\run-server.ps1 up          start (if needed), wait for /health
      .\run-server.ps1 down        stop the server process
      .\run-server.ps1 restart     stop + start with current config.ini settings
      .\run-server.ps1 status      process + /health
      .\run-server.ps1 logs        recent logs        (-Follow to stream)

    Flags:
      -Cpu     force CPU-only device selection even if a GPU is present
      -Follow  stream logs (with the 'logs' action)
      -WaitSeconds N   health-check timeout for 'up' (default 300)
#>
[CmdletBinding()]
param(
    [ValidateSet("up", "down", "restart", "status", "logs")]
    [string]$Action = "up",
    [switch]$Cpu,
    [switch]$Follow,
    [int]$WaitSeconds = 300
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir           # project root (scripts/ is one level down)
$serverDir = Join-Path $root "server"
$venvPy = Join-Path $root ".venv\Scripts\python.exe"

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "  [x] $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $venvPy)) { Die "no .venv -- run install.ps1 first" }

# ---- read [server] from config.ini (fall back to the example) ------------
$cfgFile = Join-Path $root "config.ini"
if (-not (Test-Path $cfgFile)) { $cfgFile = Join-Path $root "config.example.ini" }

function Get-Ini([string]$section, [string]$key, [string]$default = "") {
    $inSection = $false
    foreach ($line in Get-Content $cfgFile) {
        $t = $line.Trim()
        if ($t.StartsWith(";") -or $t.Length -eq 0) { continue }
        if ($t -match '^\[(.+)\]$') { $inSection = ($Matches[1].Trim() -eq $section); continue }
        if ($inSection -and $t -match "^$([regex]::Escape($key))\s*=\s*(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return $default
}

$engine = Get-Ini "server" "engine" "parakeet"
$model = Get-Ini "server" "model" ""
$device = if ($Cpu) { "cpu" } else { Get-Ini "server" "device" "auto" }
$quant = Get-Ini "server" "quantization" "none"
$port = Get-Ini "server" "port" "9000"

$ctl = Join-Path $env:TEMP 'saykey_ctl'
New-Item -ItemType Directory -Force -Path $ctl | Out-Null
$pidFile = Join-Path $ctl 'server.pid'
$logFile = Join-Path $env:TEMP 'saykey_asr_server.log'
$errFile = "$logFile.err"
$modelsDir = Join-Path $root 'models'
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null

function Get-ServerProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    $spid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($spid -notmatch '^\d+$') { return $null }
    return Get-Process -Id ([int]$spid) -ErrorAction SilentlyContinue
}

function Start-Server {
    $existing = Get-ServerProcess
    if ($existing) { Info "already running (PID $($existing.Id))"; return }
    $env:ASR_ENGINE = $engine
    $env:ASR_MODEL = $model
    $env:ASR_DEVICE = $device
    $env:ASR_QUANTIZATION = $quant
    $env:HF_HOME = $modelsDir
    Info ("engine={0}  model={1}  device={2}  port={3}" -f `
            $engine, ($(if ($model) { $model } else { "(default)" })), $device, $port)
    $p = Start-Process -FilePath $venvPy `
        -ArgumentList @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", $port) `
        -WorkingDirectory $serverDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $logFile -RedirectStandardError $errFile
    Set-Content -LiteralPath $pidFile -Value $p.Id
}

function Stop-Server {
    $p = Get-ServerProcess
    if (-not $p) { Info "not running"; return }
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    Info "stopped (PID $($p.Id))"
}

switch ($Action) {
    "up" {
        Start-Server
        Info "waiting for the model to load ..."
        $healthUrl = "http://127.0.0.1:$port/health"
        $ready = $null
        for ($i = 0; $i -lt $WaitSeconds; $i += 3) {
            Start-Sleep -Seconds 3
            try {
                $r = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 4
                if ($r.status -eq "ok") { $ready = $r; break }
                Write-Host "    ... $($r.status): $($r.detail)" -ForegroundColor DarkGray
            }
            catch { Write-Host "    ... loading" -ForegroundColor DarkGray }
        }
        if ($ready) {
            Write-Host ("  READY  {0}  ->  engine={1} model={2} device={3}" -f `
                    $healthUrl, $ready.engine, $ready.model, $ready.device) -ForegroundColor Green
        }
        else {
            Warn "server not healthy after $WaitSeconds s. Logs:  .\run-server.ps1 logs"
        }
    }
    "down" { Stop-Server }
    "restart" {
        Stop-Server
        Start-Server
        Info "restarted; check:  .\run-server.ps1 status"
    }
    "status" {
        $p = Get-ServerProcess
        if ($p) { Info "running (PID $($p.Id))" } else { Info "not running" }
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 4 | Format-List
        }
        catch { Warn "/health not responding on port $port" }
    }
    "logs" {
        if (-not (Test-Path $logFile)) { Warn "no log file yet ($logFile)"; break }
        if ($Follow) { Get-Content -LiteralPath $logFile -Tail 200 -Wait }
        else {
            Get-Content -LiteralPath $logFile -Tail 200
            if ((Test-Path $errFile) -and (Get-Item $errFile).Length -gt 0) {
                Write-Host "`n-- stderr ($errFile) --" -ForegroundColor DarkGray
                Get-Content -LiteralPath $errFile -Tail 200
            }
        }
    }
}
