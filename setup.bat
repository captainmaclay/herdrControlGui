@echo off
chcp 65001 >nul
setlocal

echo ===================================================
echo     Herdr Control Center - Automated Environment Setup
echo ===================================================
echo.

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3 was not found in PATH!
    echo Please install Python 3.10+ from python.org and add it to your PATH.
    pause
    exit /b 1
)

echo [1/3] Setting up Python virtual environment (.venv)...
if not exist "%APP_DIR%\.venv\Scripts\python.exe" (
    python -m venv "%APP_DIR%\.venv"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [2/3] Installing/updating requirements...
"%APP_DIR%\.venv\Scripts\python.exe" -m pip install --upgrade pip
"%APP_DIR%\.venv\Scripts\python.exe" -m pip install -r "%APP_DIR%\requirements.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo [3/3] Checking configuration files...
if not exist "%APP_DIR%\.env" (
    if exist "%APP_DIR%\.env.example" (
        copy "%APP_DIR%\.env.example" "%APP_DIR%\.env" >nul
        echo Created initial .env from template.
    )
)

echo.
echo ===================================================
echo   Setup successfully completed!
echo   You can now launch the application via:
echo     1. gui.bat (background mode with tray icon)
echo     2. gui_debug.bat (with console logs)
echo ===================================================
echo.
pause
