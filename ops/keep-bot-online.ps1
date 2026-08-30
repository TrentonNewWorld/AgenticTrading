<#
.SYNOPSIS
    Watchdog for the Agentic Trading Lab backend. Probes /health and starts the
    server if the probe fails.

.DESCRIPTION
    Designed to be run on a schedule (see install-keepalive-task.ps1). It is
    idempotent: when the server is already answering /health it writes one line
    to the log and exits 0 without touching anything, so running it every four
    hours never restarts a healthy process or produces a second instance.

    Binds 127.0.0.1 on purpose, never 0.0.0.0. This deployment runs with
    LOCAL_AUTO_LOGIN_ENABLED=true, and POST /api/auth/dev-auto-login has no
    localhost check of its own -- it is gated on that env var alone. On a
    LAN-reachable bind, any visitor would land already signed in as the admin
    account of an app whose live-trading switch is armed.

    A listener on the port that fails the health probe is only killed when the
    owning process is this repo's own .venv python; anything else is left alone
    and reported, rather than assuming the port belongs to us.
#>
[CmdletBinding()]
param(
    [string]$BindHost = '127.0.0.1',
    [int]$Port = 8000,
    [int]$ProbeTimeoutSeconds = 10,
    [int]$StartupWaitSeconds = 120
)

$ErrorActionPreference = 'Stop'

$RepoRoot  = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$RunServer = Join-Path $PSScriptRoot 'run-server.cmd'
$LogDir    = Join-Path $PSScriptRoot 'logs'
$LogFile   = Join-Path $LogDir 'watchdog.log'
$HealthUrl = "http://${BindHost}:${Port}/health"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

function Write-Log {
    param([string]$Message)
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Output $line
}

function Test-Health {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec $ProbeTimeoutSeconds -UseBasicParsing
        return ($response.StatusCode -eq 200 -and $response.Content -match '"ok"')
    } catch {
        return $false
    }
}

function Get-PortOwner {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object -First 1
        if ($null -eq $conn) { return $null }
        return Get-Process -Id $conn.OwningProcess -ErrorAction Stop
    } catch {
        return $null
    }
}

# --- 1. Already healthy? Nothing to do. ------------------------------------
if (Test-Health) {
    Write-Log "OK      healthy at $HealthUrl -- no action"
    exit 0
}

Write-Log "DOWN    $HealthUrl did not answer -- starting the server"

# --- 2. Clear a stale listener, but only if it is ours. --------------------
$owner = Get-PortOwner
if ($null -ne $owner) {
    $ownerPath = ''
    try { $ownerPath = $owner.Path } catch { $ownerPath = '' }
    if ($ownerPath -and ($ownerPath -ieq $VenvPython)) {
        Write-Log "STALE   pid $($owner.Id) ($ownerPath) holds port $Port but is unhealthy -- stopping it"
        Stop-Process -Id $owner.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    } else {
        Write-Log "ABORT   port $Port is held by pid $($owner.Id) ($($owner.ProcessName)), which is not this repo's venv python. Refusing to kill it."
        exit 1
    }
}

# --- 3. Start it, detached, and wait for the health probe to come up. ------
Start-Process -FilePath $RunServer -ArgumentList $BindHost, $Port -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds($StartupWaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if (Test-Health) {
        Write-Log "STARTED healthy at $HealthUrl"
        exit 0
    }
}

Write-Log "FAILED  no healthy response within ${StartupWaitSeconds}s -- see ops\logs\server.log"
exit 1
