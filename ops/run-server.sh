#!/usr/bin/env bash
# Launches the NewWorldTrading backend the canonical way on Linux/macOS:
#   uvicorn dashboard.backend.app:app, by import string, from the repo root.
# Mirror of run-server.cmd (Windows). UTF-8 is the platform default here, but
# the exports keep behavior identical if a weird locale is in play.
set -euo pipefail

BIND="${1:-127.0.0.1}"
PORT="${2:-8000}"

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$OPS_DIR")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

mkdir -p "$OPS_DIR/logs"
cd "$REPO"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

{
    echo
    echo "===== starting $(date '+%Y-%m-%d %H:%M:%S') on $BIND:$PORT ====="
} >> "$OPS_DIR/logs/server.log"

exec "$PY" -m uvicorn dashboard.backend.app:app --host "$BIND" --port "$PORT" \
    >> "$OPS_DIR/logs/server.log" 2>&1
