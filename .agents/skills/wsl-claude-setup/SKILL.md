---
name: wsl-claude-setup
description: >-
  Автоматизация установки, настройки и устранения ошибок Claude Code CLI и Claude Desktop (Linux GUI)
  внутри WSL2 с интеграцией изолированного туннеля и созданием бесшумных Windows-ярлыков.
---

# Скилл: Развертывание Claude Code CLI и Claude Desktop GUI в WSL2

Этот скилл полностью опирается на автоматизированное системное действие **`actions.install_claude_wsl`** из пакета `actions` программы `herdrCenter`.

---

## 1. Автоматический запуск (в 1 команду)

Для полной автоматической настройки окружения запустите из директории `C:\MyFiles\herdrCenter`:

```powershell
.\.venv\Scripts\python.exe -m actions install_claude_wsl
```

### Флаги и опции:
* `--dry-run`: Симуляция выполнения без изменения файлов на диске и без запуска команд в WSL.
* `--port <PORT>`: Порт локального SOCKS5 прокси (по умолчанию `1015`).
* `--http-port <PORT>`: Порт локального HTTP CONNECT прокси (по умолчанию `11015`).
* `--no-shortcuts`: Пропустить создание ярлыков на рабочем столе Windows.
* `--shutdown-wsl`: Принудительно перезагрузить WSL2 при обновлении `.wslconfig`.
* `--json`: Вывод подробного структурированного отчета в формате JSON для агентов и программ.

---

## 2. Что делает действие под капотом (Пошаговый протокол)

1. **Конфигурация `%USERPROFILE%\.wslconfig`:**
   - Гарантирует наличие секций:
     ```ini
     [wsl2]
     networkingMode=mirrored

     [experimental]
     autoProxy=false
     ```
   - Предотвращает перехват случайного Windows-прокси (`autoProxy=true`) и утечку трафика на порт 2080.
   - Записывается строго в ASCII с CRLF (`\r\n`).

2. **Настройка сетевого окружения Linux (`/etc/profile.d/herdr_claude_env.sh`):**
   - Экспортирует переменные `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` на сокеты `127.0.0.1:11015` и `socks5h://127.0.0.1:1015`.
   - Синхронизирует конфигурацию для Node.js и системного шелла.

3. **Установка системного моста `xdg-open` (`/usr/local/bin/xdg-open`):**
   - Перехватывает вызовы открытия ссылок авторизации OAuth из headless-консоли WSL и перенаправляет их в браузер Windows хоста:
     `powershell.exe -NoProfile -NonInteractive -Command "Start-Process '$TARGET'"`

4. **Проверка среды Node.js и Claude Code CLI:**
   - Проверяет версию `node -v` (LTS 20+) и пакет `@anthropic-ai/claude-code`.
   - При отсутствии автоматически устанавливает их в WSL.

5. **Создание GUI-обертки `claude-gui` (`/usr/local/bin/claude-gui`):**
   - Запускает `claude-desktop` со снятием SUID-песочницы Electron (`--no-sandbox`) для нативной отрисовки в WSLg Wayland/X11 на рабочем столе Windows.

6. **Создание Windows-ярлыков на Рабочем столе:**
   - `Claude Code CLI (WSL).bat` (консольный запуск CLI в ANSI/CRLF без кракозябр).
   - `Claude Desktop (WSL).vbs` (бесшумный Zero-Flash запуск GUI без всплывающего черного консольного окна).
   - `Claude Desktop (WSL).lnk` с иконкой `claude.ico`.

---

## 3. Контроль качества и проверка работоспособности

После выполнения настройки запустите валидацию сетевого туннеля и эндпоинта Anthropic:

```powershell
.\.venv\Scripts\python.exe -m actions verify_connectivity
```

Успешный вывод должен подтвердить:
* Доступность сокетов 1015 и 11015
* Получение внешнего нероссийского IP
* Доступность `api.anthropic.com` без ошибки `400 User location is not supported`
* Активность сетевой тюрьмы (Zero-Leak)
