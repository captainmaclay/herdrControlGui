@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Enabling Windows Virtual Machine Platform for WSL2
echo ============================================================
echo.

echo [1/2] Enabling VirtualMachinePlatform...
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

echo.
echo [2/2] Enabling Microsoft-Windows-Subsystem-Linux...
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart

echo.
echo ============================================================
echo   SUCCESS! Features have been enabled.
echo   Please REBOOT your computer now to apply the changes.
echo ============================================================
echo.
pause
