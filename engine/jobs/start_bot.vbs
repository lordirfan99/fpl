' FPL Autopilot bot launcher - detached, invisible, no console window.
' Used by start_bot.cmd (scheduled task) - WshShell.Run with 0 (hidden), False (don't wait).
' PYTHONUTF8=1 forces UTF-8 output - cp1252 cannot encode the bot's 4-byte emoji.
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
Base = FSO.GetParentFolderName(FSO.GetParentFolderName(WScript.ScriptFullName))
WshShell.Run "cmd /c set PYTHONUTF8=1&& cd /d """ & Base & """ && .venv\Scripts\python.exe -u bot\telegram_bot.py >> data\bot.log 2>&1", 0, False
