@echo off
setlocal
cd /d "%~dp0"
echo Stop the bridge before manual login; its browser profile cannot be shared.
.venv\Scripts\python.exe -m server.login
pause
