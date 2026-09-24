@echo off
chcp 65001 >nul
setlocal

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

cd /d "%APP_DIR%"

set "PY=%APP_DIR%\.venv\Scripts\pythonw.exe"
set "PY_CON=%APP_DIR%\.venv\Scripts\python.exe"

if not exist "%PY_CON%" (
    echo Creating virtual environment...
    python -m venv "%APP_DIR%\.venv"
)

"%PY_CON%" -c "import pystray, PIL, dotenv, requests, socks" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies from requirements.txt...
    "%PY_CON%" -m pip install -r "%APP_DIR%\requirements.txt"
)

if exist "%PY%" (
    start "" "%PY%" "%APP_DIR%\config_app.py"
) else (
    start "" "%PY_CON%" "%APP_DIR%\config_app.py"
)
