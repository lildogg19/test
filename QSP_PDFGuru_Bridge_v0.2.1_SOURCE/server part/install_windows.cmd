@echo off
setlocal
cd /d "%~dp0"
py -3.11 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto failed
.venv\Scripts\python.exe configure.py
echo Configure .env before starting. Use a unique API token. Keep credentials on the server.
pause
exit /b 0
:failed
echo Installation failed. Existing services and databases were not changed.
pause
exit /b 1
