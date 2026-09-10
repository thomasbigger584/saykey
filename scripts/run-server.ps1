#Requires -Version 5.1
<#
    run-server.ps1 -- manage the Saykey ASR Docker container.

    The server-side model is chosen in config.ini -> [server] engine.
    Change it, then:  .\run-server.ps1 restart

      .\run-server.ps1 up          build (if needed) + start, wait for health
      .\run-server.ps1 down        stop and remove the container
      .\run-server.ps1 restart     recreate with current config.ini settings
      .\run-server.ps1 status      container + /health
      .\run-server.ps1 logs        recent logs        (-Follow to stream)
      .\run-server.ps1 build       build the image
      .\run-server.ps1 rebuild     build --no-cache
      .\run-server.ps1 shell       shell inside the container

    Flags:
      -Cpu     force a CPU-only build/run even if a GPU is present
      -Hf      include torch + transformers in the image (enables engine = hf:<id>)
      -Follow  stream logs (with the 'logs' action)
      -WaitSeconds N   health-check timeout for 'up' (default 300)
#>
[CmdletBinding()]
param(
    [ValidateSet("up", "down", "restart", "status", "logs", "build", "rebuild", "pull", "shell")]
    [string]$Action = "up",
    [switch]$Cpu,
    [switch]$Hf,
    [switch]$Follow,
    [int]$WaitSeconds = 300
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir           # project root (scripts/ is one level down)
$serverDir = Join-Path $root "server"
Set-Location $serverDir

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "  [x] $m" -ForegroundColor Red; exit 1 }

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
$device = Get-Ini "server" "device" "auto"
$quant = Get-Ini "server" "quantization" "none"
$port = Get-Ini "server" "port" "9000"
$image = Get-Ini "server" "image" "saykey-asr:latest"
$upUrl = Get-Ini "server" "upstream_url" ""
$upKey = Get-Ini "server" "upstream_key" ""

# ---- docker present + running ------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Die "Docker not found. Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
}
$eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
docker info 2>&1 | Out-Null
$dockerUp = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $eap
if (-not $dockerUp) { Die "Docker daemon not reachable. Start Docker Desktop and retry." }

# ---- GPU detection ----------------------------------------------------
$gpu = $false
if (-not $Cpu -and $device -ne "cpu" -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    nvidia-smi -L 2>&1 | Out-Null
    $gpu = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $eap
}

# ---- compose environment -------------------------------------------
$env:ASR_ENGINE = $engine
$env:ASR_MODEL = $model
$env:ASR_DEVICE = $device
$env:ASR_QUANTIZATION = $quant
$env:ASR_PORT = $port
$env:ASR_IMAGE = $image
$env:ASR_UPSTREAM_URL = $upUrl
$env:ASR_UPSTREAM_KEY = $upKey
$env:ORT_PACKAGE = if ($gpu) { "onnxruntime-gpu" } else { "onnxruntime" }
$env:INSTALL_HF = if ($Hf) { "true" } else { "false" }

$composeArgs = @("compose", "-f", "docker-compose.yml")
if ($gpu) { $composeArgs += @("-f", "docker-compose.gpu.yml") }

New-Item -ItemType Directory -Force -Path (Join-Path $root "models") | Out-Null

Info ("engine={0}  model={1}  device={2}  gpu={3}  port={4}  hf={5}" -f `
        $engine, ($(if ($model) { $model } else { "(default)" })), $device, $gpu, $port, $Hf)

switch ($Action) {
    "up" {
        & docker @composeArgs up -d --build
        if ($LASTEXITCODE -ne 0) { Die "compose up failed" }
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
    "down" { & docker @composeArgs down }
    "restart" {
        & docker @composeArgs up -d --build --force-recreate
        if ($LASTEXITCODE -ne 0) { Die "restart failed" }
        Info "restarted; check:  .\run-server.ps1 status"
    }
    "status" {
        & docker @composeArgs ps
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 4 | Format-List
        }
        catch { Warn "/health not responding on port $port" }
    }
    "logs" {
        if ($Follow) { & docker @composeArgs logs -f --tail 200 }
        else { & docker @composeArgs logs --tail 200 }
    }
    "build" { & docker @composeArgs build }
    "rebuild" { & docker @composeArgs build --no-cache }
    "pull" { & docker @composeArgs pull }
    "shell" { & docker @composeArgs exec asr bash }
}
