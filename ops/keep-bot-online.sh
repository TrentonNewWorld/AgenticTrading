#!/usr/bin/env bash
# Watchdog for the NewWorldTrading backend on Linux/macOS. Probes /health and
# starts the server if the probe fails. Mirror of keep-bot-online.ps1:
#   - idempotent: healthy server -> one log line, exit 0, touch nothing
#   - binds 127.0.0.1 on purpose (LOCAL_AUTO_LOGIN means a LAN bind would hand
#     any visitor an admin session on a live-armed app)
#   - a stale listener on the port is killed ONLY if it is this repo's own
#     .venv python; anything else is reported and left alone.
set -uo pipefail

BIND_HOST="${1:-127.0.0.1}"
PORT="${2:-8000}"
PROBE_TIMEOUT="${3:-10}"
STARTUP_WAIT="${4:-120}"

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$OPS_DIR")"
VENV_PY="$REPO/.venv/bin/python"
LOG_FILE="$OPS_DIR/logs/watchdog.log"
HEALTH_URL="http://$BIND_HOST:$PORT/health"

mkdir -p "$OPS_DIR/logs"

log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S')  $*"
    echo "$line" | tee -a "$LOG_FILE"
}

healthy() {
    curl -fsS --max-time "$PROBE_TIMEOUT" "$HEALTH_URL" 2>/dev/null | grep -q '"ok"'
}

port_owner_pid() {
    # ss is standard on modern Linux; fall back to lsof (macOS / older boxes).
    # sed, not awk: the 3-arg match() is gawk-only and Ubuntu ships mawk.
    local pid
    pid="$(ss -ltnp 2>/dev/null | grep -F ":$PORT " | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1)"
    if [ -z "${pid:-}" ] && command -v lsof >/dev/null 2>&1; then
        pid="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1)"
    fi
    echo "${pid:-}"
}

# --- 1. Already healthy? Nothing to do. ------------------------------------
if healthy; then
    log "OK      healthy at $HEALTH_URL -- no action"
    exit 0
fi

log "DOWN    $HEALTH_URL did not answer -- starting the server"

# --- 2. Clear a stale listener, but only if it is ours. --------------------
OWNER_PID="$(port_owner_pid)"
if [ -n "$OWNER_PID" ]; then
    OWNER_EXE="$(readlink -f "/proc/$OWNER_PID/exe" 2>/dev/null || echo '')"
    if [ "$OWNER_EXE" = "$(readlink -f "$VENV_PY" 2>/dev/null || echo "$VENV_PY")" ]; then
        log "STALE   pid $OWNER_PID ($OWNER_EXE) holds port $PORT but is unhealthy -- stopping it"
        kill -9 "$OWNER_PID" 2>/dev/null
        sleep 3
    else
        log "ABORT   port $PORT is held by pid $OWNER_PID ($OWNER_EXE), which is not this repo's venv python. Refusing to kill it."
        exit 1
    fi
fi

# --- 3. Start it, detached, and wait for the health probe to come up. ------
setsid nohup bash "$OPS_DIR/run-server.sh" "$BIND_HOST" "$PORT" >/dev/null 2>&1 &

DEADLINE=$(( $(date +%s) + STARTUP_WAIT ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    sleep 3
    if healthy; then
        log "STARTED healthy at $HEALTH_URL"
        exit 0
    fi
done

log "FAILED  no healthy response within ${STARTUP_WAIT}s -- see ops/logs/server.log"
exit 1
