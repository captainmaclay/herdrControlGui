@echo off
:: BatchGotAdmin
:-------------------------------------
REM  --> Check for permissions
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"

REM --> If error flag set, we do not have admin.
if '%errorlevel%' NEQ '0' (
    echo Requesting administrative privileges...
    goto UACPrompt
) else ( goto gotAdmin )

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    set params = %*
    echo UAC.ShellExecute "cmd.exe", "/c ""%~s0"" %params%", "", "runas", 1 >> "%temp%\getadmin.vbs"

    "%temp%\getadmin.vbs"
    del "%temp%\getadmin.vbs"
    exit /B

:gotAdmin
    pushd "%CD%"
    CD /D "%~dp0"
:--------------------------------------
chcp 65001 >nul
echo ============================================================
echo   Herdr Config (Administrator Mode)
echo ============================================================
echo.

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "PY_CON=%APP_DIR%\.venv\Scripts\python.exe"

if not exist "%PY_CON%" (
    echo [ERROR] Virtual environment not found. Please run gui.bat first.
    pause
    exit /b 1
)

echo Running App with Admin rights to allow Windows Firewall modifications!
echo.
"%PY_CON%" "%APP_DIR%\config_app.py"

echo.
echo Process finished.
pause
