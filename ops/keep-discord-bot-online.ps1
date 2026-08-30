<#
.SYNOPSIS
    Watchdog for the NewWorldSupport Discord bot. Starts it if it isn't running.

.DESCRIPTION
    Idempotent: when the bot is already running this writes one log line and
    exits 0 without touching it, so running every four hours never restarts a
    healthy bot or produces a second instance. Two instances would double-post
    the 24h reminder and answer every /support twice, so the "already running"
    check is the important half of this script.

    Liveness is process-based, not an HTTP probe -- a Discord bot exposes no
    health endpoint. We look for a python process whose command line contains
    the bot's module path, which is specific enough not to match the web
    server (that one runs uvicorn) or an unrelated python.

    Limitation worth knowing: this detects "the process exists", not "the
    gateway session is healthy". discord.py reconnects internally, so a
    running-but-disconnected bot recovers on its own; a crashed one is what
    this restarts. A config error that makes the bot exit immediately shows up
    as FAILED here rather than as a silent no-op.
#>
[CmdletBinding()]
param(
    [int]$StartupWaitSeconds = 90
)

$ErrorActionPreference = 'Stop'

$RepoRoot  = Split-Path -Parent $PSScriptRoot
$RunBot    = Join-Path $PSScriptRoot 'run-discord-bot.cmd'
$LogDir    = Join-Path $PSScriptRoot 'logs'
$LogFile   = Join-Path $LogDir 'discord-watchdog.log'
$BotLog    = Join-Path $LogDir 'discord-bot.log'
$ModuleTag = 'dashboard.backend.integrations.discord_bot'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

function Write-Log {
    param([string]$Message)
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Output $line
}

function Get-BotProcess {
    try {
        return Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
               Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ModuleTag*" } |
               Select-Object -First 1
    } catch {
        return $null
    }
}

# --- 1. Already running? Nothing to do. ------------------------------------
$existing = Get-BotProcess
if ($null -ne $existing) {
    Write-Log "OK      bot already running (pid $($existing.ProcessId)) -- no action"
    exit 0
}

Write-Log "DOWN    Discord bot not running -- starting it"
Start-Process -FilePath $RunBot -WindowStyle Hidden

# --- 2. Wait for it to actually connect, not just to spawn. ----------------
# A bad token / missing env var makes the process exit within seconds, so
# "the process appeared" is not proof of anything. The bot prints
# "Discord bot connected as ..." on a successful gateway handshake.
$deadline = (Get-Date).AddSeconds($StartupWaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $proc = Get-BotProcess
    if ($null -ne $proc) {
        $tail = ''
        try { $tail = (Get-Content $BotLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n" } catch { }
        if ($tail -match 'Discord bot connected as') {
            Write-Log "STARTED bot connected (pid $($proc.ProcessId))"
            exit 0
        }
    }
}

$proc = Get-BotProcess
if ($null -ne $proc) {
    Write-Log "PARTIAL process is up (pid $($proc.ProcessId)) but no 'connected' line within ${StartupWaitSeconds}s -- see ops\logs\discord-bot.log"
    exit 0
}
Write-Log "FAILED  bot did not stay running -- see ops\logs\discord-bot.log"
exit 1
