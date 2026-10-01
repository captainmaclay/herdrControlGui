@echo off
chcp 65001 >nul
:: BatchGotAdmin
:-------------------------------------
REM  --> Check for permissions
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"

if '%errorlevel%' NEQ '0' (
    echo [i] Запрос прав Администратора для установки компонентов WSL...
    goto UACPrompt
) else ( goto gotAdmin )

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin_wsl.vbs"
    echo UAC.ShellExecute "cmd.exe", "/c ""%~s0""", "", "runas", 1 >> "%temp%\getadmin_wsl.vbs"
    "%temp%\getadmin_wsl.vbs"
    del "%temp%\getadmin_wsl.vbs"
    exit /B

:gotAdmin
    pushd "%CD%"
    CD /D "%~dp0"
:--------------------------------------

echo ============================================================
echo   Установка компонентов Windows Subsystem for Linux (WSL)
echo ============================================================
echo.

echo [1/3] Включение компонентов виртуализации Windows...
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

echo.
echo [2/3] Установка пакета ядра WSL из wsl.msi...
if exist "%~dp0wsl.msi" (
    msiexec.exe /i "%~dp0wsl.msi" /quiet /norestart
    echo     Пакет wsl.msi установлен.
) else (
    echo     [!] Файл wsl.msi не найден в текущей папке.
)

echo.
echo [3/3] Настройка версии WSL по умолчанию...
wsl.exe --set-default-version 2

echo.
echo ============================================================
echo   Проверка состояния WSL:
echo ============================================================
wsl.exe --version 2>nul || wsl.exe --status 2>nul

echo.
echo Установка компонентов ядра завершена!
echo.
pause
