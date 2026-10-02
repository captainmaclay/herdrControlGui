@echo off
title Claude Code (WSL)
where wt.exe >nul 2>&1
if %ERRORLEVEL% equ 0 (
    wt.exe --title "Claude Code (WSL)" wsl.exe -d Ubuntu bash -lic "claude %*"
    exit /b
)
wsl.exe -d Ubuntu bash -lic "claude %*"
if %ERRORLEVEL% neq 0 pause
