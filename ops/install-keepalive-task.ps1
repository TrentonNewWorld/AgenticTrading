<#
.SYNOPSIS
    Registers the Windows Scheduled Task that keeps the trading bot online.

.DESCRIPTION
    Windows has no cron, so this is the equivalent: a task that fires at
    00:00 and every 4 hours after it (00, 04, 08, 12, 16, 20), running
    keep-bot-online.ps1. That script is a health probe first and a launcher
    second, so a firing that finds the server already up costs one HTTP
    request and changes nothing.

    StartWhenAvailable is what makes "continuously" hold across a sleeping or
    powered-off machine: a missed 04:00 fires as soon as Windows is back,
    instead of waiting until 08:00.

    MultipleInstances IgnoreNew means a firing that overlaps a still-running
    previous one is dropped, never queued -- two watchdogs racing to start the
    same server is the one way this could produce a duplicate instance.

    Re-running this script is safe; -Force replaces the existing registration.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'AlpacaTrader-KeepOnline',
    [int]$IntervalHours = 4
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Watchdog = Join-Path $PSScriptRoot 'keep-bot-online.ps1'

if (-not (Test-Path $Watchdog)) { throw "Watchdog script not found at $Watchdog" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Watchdog) `
    -WorkingDirectory $RepoRoot

# Anchor at today's midnight so the occurrences land on 00:00 + N*4h wall-clock
# boundaries. A start time in the past is fine -- the first firing is the next
# boundary, not an immediate catch-up run.
$midnight = (Get-Date).Date
$trigger = New-ScheduledTaskTrigger -Once -At $midnight -RepetitionInterval (New-TimeSpan -Hours $IntervalHours)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Every $IntervalHours hours from 00:00: probe http://127.0.0.1:8000/health and start the Agentic Trading Lab backend if it is down." `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName' (every $IntervalHours h from 00:00)."
