@echo off
chcp 65001 >nul
setlocal

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

cd /d "%APP_DIR%"

echo ============================================================
echo   Herdr Config (Debug Launcher)
echo ============================================================
echo.

set "PY_CON=%APP_DIR%\.venv\Scripts\python.exe"

if not exist "%PY_CON%" (
    echo [ERROR] Virtual environment not found at: %PY_CON%
    pause
    exit /b 1
)

echo Starting config_app.py with console output enabled...
echo.
"%PY_CON%" "%APP_DIR%\config_app.py"

echo.
echo Process finished with exit code %errorlevel%.
pause
