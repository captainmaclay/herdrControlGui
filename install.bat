@echo off
chcp 65001 >nul
setlocal

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"

set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found! Run setup.bat first.
        pause
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" "%APP_DIR%\install_herdr_stack.py" %*
if errorlevel 1 (
    echo.
    echo Installation failed or was cancelled.
    pause
    exit /b 1
)

echo.
pause
