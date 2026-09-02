@echo off
rem FPL Autopilot bot launcher - idempotent (no duplicate processes).
rem Used by Windows Task Scheduler (ONLOGON) and safe to run manually.
rem Launches detached via VBScript (no console window, returns immediately).
rem CRITICAL (2026-08-12): the old check `findstr /i "telegram_bot"` also
rem matched the BETTING engine's `report.telegram_bot` process, so whenever
rem that bot ran, this launcher wrongly skipped starting the FPL bot. Match
rem the FPL bot's exact path instead.
cd /d "%~dp0.."
wmic process where "name='python.exe'" get commandline 2>nul | findstr /i /c:"bot\telegram_bot.py" >nul
if %errorlevel%==0 exit /b 0
wscript.exe //nologo "%~dp0start_bot.vbs"
exit /b 0
