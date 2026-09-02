#!/bin/bash
# Start the FPL Autopilot Telegram bot service (logging to data/bot.log)
cd /c/Users/irfan/fpl-autopilot
.venv/Scripts/python.exe -u bot/telegram_bot.py >> data/bot.log 2>&1
