@echo off
chcp 65001 >nul
echo ========================================================
echo   Herdr / AionUi — Автоматическое восстановление базы
echo ========================================================
echo.
echo Запуск восстановления базы данных AionUi внутри WSL2...
wsl -d Ubuntu -- python3 /mnt/d/My\ files/herdrControlGui/scripts/repair_aionui_db.py
echo.
pause
