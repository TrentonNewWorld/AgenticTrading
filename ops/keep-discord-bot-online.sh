#!/usr/bin/env bash
# Watchdog for the NewWorldSupport Discord bot on Linux/macOS. Mirror of
# keep-discord-bot-online.ps1. Liveness is process-based, not an HTTP probe --
# a Discord bot exposes no health endpoint. We look for a python process whose
# command line contains the bot's module path, so we never confuse it with the
# dashboard server (that one runs uvicorn) or an unrelated python.
#
# A bad token makes the process exit within seconds, so "the process appeared"
# is not proof of anything: after starting we wait for the bot's own
# "Discord bot connected as" line in the log before reporting STARTED.
set -uo pipefail

STARTUP_WAIT="${1:-60}"

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$OPS_DIR/logs/discord-watchdog.log"
BOT_LOG="$OPS_DIR/logs/discord-bot.log"
MODULE="dashboard.backend.integrations.discord_bot"

mkdir -p "$OPS_DIR/logs"

log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S')  $*"
    echo "$line" | tee -a "$LOG_FILE"
}

bot_pid() {
    pgrep -f "$MODULE" 2>/dev/null | head -1
}

# --- 1. Already running? Nothing to do. ------------------------------------
PID="$(bot_pid)"
if [ -n "${PID:-}" ]; then
    log "OK      bot process is up (pid $PID) -- no action"
    exit 0
fi

log "DOWN    no bot process found -- starting it"
MARK=$(stat -c %s "$BOT_LOG" 2>/dev/null || echo 0)

setsid nohup bash "$OPS_DIR/run-discord-bot.sh" >/dev/null 2>&1 &

# --- 2. Wait for the bot's own "connected" line, not just a live pid. ------
DEADLINE=$(( $(date +%s) + STARTUP_WAIT ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    sleep 3
    if tail -c +"$((MARK + 1))" "$BOT_LOG" 2>/dev/null | grep -q "Discord bot connected as"; then
        log "STARTED bot connected (pid $(bot_pid))"
        exit 0
    fi
done

PID="$(bot_pid)"
if [ -n "${PID:-}" ]; then
    log "PARTIAL process is up (pid $PID) but no 'connected' line within ${STARTUP_WAIT}s -- see ops/logs/discord-bot.log"
else
    log "FAILED  process exited before connecting -- see ops/logs/discord-bot.log"
fi
exit 1
