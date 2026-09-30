@echo off
chcp 65001 >nul
setlocal

set "OMNI_DIR=%~dp0"
if "%OMNI_DIR:~-1%"=="\" set "OMNI_DIR=%OMNI_DIR:~0,-1%"
set "ROOT_DIR=%OMNI_DIR%\.."
cd /d "%ROOT_DIR%"

set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found! Run setup.bat first.
        pause
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" "%OMNI_DIR%\install_herdr_stack.py" %*
if errorlevel 1 (
    echo.
    echo Installation failed or was cancelled.
    pause
    exit /b 1
)

echo.
pause
