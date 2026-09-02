@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PORT=8787
.venv\Scripts\python.exe -m uvicorn webapp.server:app --host 127.0.0.1 --port %PORT%
