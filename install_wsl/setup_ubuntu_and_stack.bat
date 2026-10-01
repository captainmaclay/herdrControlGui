@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Setting up Ubuntu 22.04 LTS and Omni_Aion Stack
echo ============================================================
echo.

set "APP_DIR=C:\MyFiles\herdrCenter"
set "ROOTFS=%APP_DIR%\install_wsl\ubuntu.rootfs.tar.gz"
set "WSL_DIR=C:\WSL\Ubuntu"

if not exist "%WSL_DIR%" mkdir "%WSL_DIR%"

echo [1/4] Importing Ubuntu 22.04 LTS into WSL2...
wsl.exe --import Ubuntu "%WSL_DIR%" "%ROOTFS%" --version 2
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to import Ubuntu into WSL2.
    echo Please make sure your computer was restarted after enabling virtualization.
    echo.
    pause
    exit /b 1
)

echo.
echo [2/4] Setting Ubuntu as default WSL distribution...
wsl.exe --set-default Ubuntu

echo.
echo [3/4] Initializing user environment in WSL...
wsl.exe -d Ubuntu -u root bash -c "useradd -m -s /bin/bash -G sudo %USERNAME% 2>/dev/null; echo '%USERNAME% ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers"

echo.
echo [4/4] Launching Omni_Aion stack installer...
"%APP_DIR%\.venv\Scripts\python.exe" "%APP_DIR%\Omni_Aion\install_herdr_stack.py" --yes

echo.
echo ============================================================
echo   Omni_Aion stack setup completed!
echo ============================================================
echo.
pause
