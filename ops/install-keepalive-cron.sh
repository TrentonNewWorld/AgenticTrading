#!/usr/bin/env bash
# Installs the Linux keepalive schedule: both watchdogs every 4 hours starting
# at midnight, plus once at reboot. Mirror of install-keepalive-task.ps1 +
# install-discord-keepalive-task.ps1 (Windows Scheduled Tasks). Idempotent --
# re-running replaces this repo's entries without duplicating them or touching
# any other cron lines.
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG="# newworldtrading-keepalive"

SERVER_LINE="0 0,4,8,12,16,20 * * * bash '$OPS_DIR/keep-bot-online.sh' >/dev/null 2>&1 $TAG"
BOT_LINE="0 0,4,8,12,16,20 * * * bash '$OPS_DIR/keep-discord-bot-online.sh' >/dev/null 2>&1 $TAG"
REBOOT_SERVER="@reboot sleep 30 && bash '$OPS_DIR/keep-bot-online.sh' >/dev/null 2>&1 $TAG"
REBOOT_BOT="@reboot sleep 45 && bash '$OPS_DIR/keep-discord-bot-online.sh' >/dev/null 2>&1 $TAG"

CURRENT="$(crontab -l 2>/dev/null | grep -v "$TAG" || true)"
{
    [ -n "$CURRENT" ] && echo "$CURRENT"
    echo "$SERVER_LINE"
    echo "$BOT_LINE"
    echo "$REBOOT_SERVER"
    echo "$REBOOT_BOT"
} | crontab -

echo "Installed. Current keepalive entries:"
crontab -l | grep "$TAG"
