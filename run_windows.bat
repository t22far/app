@echo off
title Leave Tracker
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python is not installed.
  echo Download it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" during install.
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: setting things up, this takes a minute...
  py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

set IP=
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do if not defined IP set IP=%%a
set IP=%IP: =%

cls
echo ==========================================
echo   Leave Tracker is running
echo ==========================================
echo.
echo   On this PC:      http://localhost:8080
echo   On the shop wifi: http://%IP%:8080
echo.
echo   Keep this window open. Closing it stops the app.
echo   If Windows asks about the firewall, click Allow.
echo.
python -m waitress --listen=0.0.0.0:8080 wsgi:application
pause
