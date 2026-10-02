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

5. **Создание защищенной GUI-обертки `claude-gui` (`/usr/local/bin/claude-gui`):**
   - Проверяет и автоматически очищает зависший `SingletonLock`, если старый процесс умер после сна/перезапуска хоста.
   - Фиксирует стабильный X11-бэкенд (`--ozone-platform=x11`) во избежание GPU-краша 1002 в WSLg.
   - Запускает `claude-desktop` со снятием SUID-песочницы Electron (`--no-sandbox`).

6. **Создание надежных Windows-ярлыков на Рабочем столе:**
   - `Claude Code (WSL).lnk` (консольный запуск CLI в Windows Terminal / CMD).
   - `Claude Desktop (WSL).lnk` (бесшумный запуск GUI через `run_claude_gui.vbs` без всплывающего черного консольного окна и с иконкой `claude.ico`).
   - Исполняемые скрипты лаунчеров хранятся в рабочей папке программы (`C:\MyFiles\herdrCenter`), предотвращая случайное удаление с Рабочего стола.

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

---

## 4. Диагностика и устранение типовых сбоев GUI (Troubleshooting Runbook)

### Сбой 1: Ярлык не открывает окно, процесс молча завершается через 2 секунды
* **Причина:** Старый процесс `claude-desktop` завис после отключения/сна Windows и удерживает `~/.config/Claude/SingletonLock`. Новый процесс видит замок, пытается передать фокус мертвому окну (`Request ended (non-user cancelled)`) и сразу выходит.
* **Быстрое решение:**
  ```bash
  wsl -d Ubuntu bash -c "killall -9 claude-desktop chrome_crashpad_handler 2>/dev/null; rm -f ~/.config/Claude/Singleton*"
  ```
* **Персистентная защита:** В обертке `/usr/local/bin/claude-gui` внедрена автоматическая проверка `kill -0 $PID` замка перед запуском.

### Сбой 2: Циклический краш GPU (`GPU process launch failed: error_code=1002`)
* **Причина:** Флаги `--ozone-platform-hint=auto` или Wayland-декорации заставляют Electron использовать нативный Wayland в WSLg, который нестабилен в Chromium.
* **Решение:** Всегда явно указывать бэкенд X11:
  ```bash
  claude-desktop --no-sandbox --ozone-platform=x11 --password-store=basic
  ```

### Сбой 3: Ошибка `WSL_E_USER_NOT_FOUND` при клике по ярлыку
* **Причина:** Использование жесткого флага `-u default` при вызове `wsl.exe`. В системе установлен пользователь `79251`.
* **Решение:** Не указывать флаг `-u` (WSL автоматически подхватывает дефолтного пользователя из `/etc/wsl.conf`) либо использовать актуальное имя пользователя.

### Сбой 4: Приостановка процесса Electron (`State T / Stopped`)
* **Причина:** Вызов `bash -lic` (интерактивный шелл) внутри скрытого `wscript.exe` активирует Job Control, вызывая `SIGTTIN`/`SIGTTOU` при попытке чтения TTY дочерними скриптами.
* **Решение:** В [run_claude_gui.vbs](file:///C:/MyFiles/herdrCenter/run_claude_gui.vbs) использовать неинтерактивный шелл:
  ```vbs
  WshShell.Run "wsl.exe -d Ubuntu bash -lc ""/usr/local/bin/claude-gui""", 0, False
  ```

### Сбой 5: Claude Code выдает `API Error: ECONNRESET`, а OmniRoute — 502
* **Причина:** Рассинхронизация параметров локального сокета 1015/11015 в `vless2socks` (сменился локальный IP машины в `sendThrough` или устарел UUID ключа).
* **Решение:**
  1. Проверить сокет: `curl.exe -x socks5h://127.0.0.1:1015 https://icanhazip.com`
  2. Обновить `instances.json` и `xray-config-1015.json` актуальным UUID и IP.
  3. Перезапустить процесс `xray.exe`.
