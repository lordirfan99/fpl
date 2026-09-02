#!/bin/bash
# FPL bot heartbeat watchdog - silent when healthy, systemctl restart when stale.
# The bot touches data/processed/bot_heartbeat.txt every 60s while polling.
set -u
HB="/opt/fpl-autopilot/data/processed/bot_heartbeat.txt"
BOT_SVC="fpl-bot.service"

if [ -f "$HB" ]; then
  AGE=$(( $(date +%s) - $(stat -c %Y "$HB") ))
  if [ "$AGE" -lt 300 ]; then
    exit 0   # healthy - silent
  fi
fi

echo "[$(date '+%F %T')] heartbeat stale/missing -> restarting $BOT_SVC"
systemctl restart "$BOT_SVC"