#!/usr/bin/env bash
# Launches the NewWorldSupport Discord bot on Linux/macOS. Mirror of
# run-discord-bot.cmd. PYTHONUNBUFFERED is load-bearing, not cosmetic: output
# is redirected to a file, so Python block-buffers print(). The watchdog waits
# for the "Discord bot connected as" print() and would report PARTIAL forever
# on a perfectly healthy bot if that line sat in a buffer.
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$OPS_DIR")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1

mkdir -p "$OPS_DIR/logs"
cd "$REPO"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

{
    echo
    echo "===== starting $(date '+%Y-%m-%d %H:%M:%S') ====="
} >> "$OPS_DIR/logs/discord-bot.log"

exec "$PY" -m dashboard.backend.integrations.discord_bot \
    >> "$OPS_DIR/logs/discord-bot.log" 2>&1
