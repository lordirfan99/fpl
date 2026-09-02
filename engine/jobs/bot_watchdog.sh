#!/bin/bash
# FPL bot watchdog - silent when healthy, restarts when heartbeat stale.
# Heartbeat: data/processed/bot_heartbeat.txt touched every 60s by the bot.
HB="/c/Users/irfan/fpl-autopilot/data/processed/bot_heartbeat.txt"
BOTDIR="/c/Users/irfan/fpl-autopilot"

# healthy? heartbeat exists and modified within last 4 minutes
if [ -f "$HB" ]; then
  AGE=$(( $(date +%s) - $(stat -c %Y "$HB") ))
  if [ "$AGE" -lt 240 ]; then
    exit 0   # silent - healthy
  fi
fi

# stale or missing -> restart
# Kill ALL matching bot PIDs (head -1 only leaves duplicates stacking on repeated restarts)
PIDS=$(wmic process where "name='python.exe'" get processid,commandline 2>/dev/null | tr -d '\r' | grep -i telegram_bot | awk '{print $NF}')
if [ -n "$PIDS" ]; then
  for PID in $PIDS; do
    taskkill -F -PID "$PID" >/dev/null 2>&1
  done
  sleep 2
fi
cd "$BOTDIR" && nohup .venv/Scripts/python.exe -u bot/telegram_bot.py >> data/bot.log 2>&1 &
echo "[$(date '+%H:%M')] FPL bot was down -> restarted"
