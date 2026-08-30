<#
.SYNOPSIS
    Registers the Windows Scheduled Task that keeps the Discord bot online.

.DESCRIPTION
    Fires at 00:00 and every 4 hours after (00, 04, 08, 12, 16, 20), running
    keep-discord-bot-online.ps1 -- which checks first and only starts the bot
    if it isn't already running.

    StartWhenAvailable is what makes "continuously" hold across a sleeping or
    powered-off laptop: a missed 04:00 fires as soon as Windows is back rather
    than waiting until 08:00.

    MultipleInstances IgnoreNew drops an overlapping firing instead of queuing
    it -- two watchdogs racing to start the same bot is the one way this could
    produce a duplicate instance, which would double-post the 24h reminder.

    Re-running this script is safe; -Force replaces the existing registration.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'NewWorldSupport-KeepOnline',
    [int]$IntervalHours = 4
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Watchdog = Join-Path $PSScriptRoot 'keep-discord-bot-online.ps1'

if (-not (Test-Path $Watchdog)) { throw "Watchdog script not found at $Watchdog" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Watchdog) `
    -WorkingDirectory $RepoRoot

# Anchor at today's midnight so occurrences land on 00:00 + N*4h wall-clock
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
    -Description "Every $IntervalHours hours from 00:00: start the NewWorldSupport Discord bot if it is not already running." `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName' (every $IntervalHours h from 00:00)."
