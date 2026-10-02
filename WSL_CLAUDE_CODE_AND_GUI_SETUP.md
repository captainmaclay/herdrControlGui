# Полное руководство: Установка и настройка Claude Code (CLI) и Claude Desktop (GUI) в Linux WSL2 с обходом всех типичных ошибок

В данном руководстве описан процесс развертывания официального стека Anthropic (**Claude Code CLI** и **Claude Desktop Linux GUI**) внутри среды **WSL2 (Ubuntu)** под управлением Windows 11 с интеграцией сетевого туннелирования и защитного Killswitch.

---

## 1. Архитектура решения

```
┌────────────────────────────────────────────────────────┐
│                   Windows 11 Host                      │
│  - vless2socks (SOCKS5: 127.0.0.1:1015, HTTP: :11015)  │
│  - herdrCenter (маршрутизация, мониторинг, Killswitch) │
│  - Браузер пользователя (Chrome / Edge / Firefox)      │
└───────────────────────────▲────────────────────────────┘
                            │ (локальный loopback туннель)
┌───────────────────────────▼────────────────────────────┐
│                  WSL2 Ubuntu 24.04                     │
│  - Сетевая изоляция (nftables / zero-leak firewall)    │
│  - Системный прокси: HTTP_PROXY -> 127.0.0.1:11015     │
│  - Claude Code CLI (Node.js 20+ npm global)           │
│  - Claude Desktop Linux GUI (Electron + WSLg Wayland)  │
│  - xdg-open мост -> powershell.exe Start-Process       │
└────────────────────────────────────────────────────────┘
```

---

## 2. Подводные камни и ошибки (Gotchas & Pitfalls)

### Ошибка 1: Загрязнение сетевого стека Windows-прокси (`autoProxy=true`)
* **Симптом:** При включенном системном прокси в Windows (например, v2rayN или sing-box на порту `2080`) WSL2 автоматически перехватывает этот прокси. В результате трафик идет не через выделенный сокет `1015`/`11015`, а через случайный порт `2080`, нарушая региональную изоляцию.
* **Причина:** В новых версиях WSL2 включена опция зеркалирования сетевых параметров хоста.
* **Решение:** В файле `C:\Users\<ИМЯ_ПОЛЬЗОВАТЕЛЯ>\.wslconfig` явно отключить `autoProxy`:
  ```ini
  [wsl2]
  networkingMode=mirrored

  [experimental]
  autoProxy=false
  ```
  После изменения выполнить в PowerShell: `wsl --shutdown`.

---

### Ошибка 2: «HTTP 400 / User location is not supported for the API use»
* **Симптом:** При запуске Claude Code появляется ошибка:
  ```json
  {
    "error": {
      "code": 400,
      "message": "User location is not supported for the API use.",
      "status": "FAILED_PRECONDITION"
    }
  }
  ```
* **Причина:** Прямой выход в интернет с российского IP или некорректная маршрутизация туннеля.
* **Решение:** Строго настроить системные переменные прокси в `/etc/profile.d/herdr_claude_env.sh`:
  ```bash
  export HTTP_PROXY="http://127.0.0.1:11015"
  export HTTPS_PROXY="http://127.0.0.1:11015"
  export ALL_PROXY="socks5h://127.0.0.1:1015"
  export http_proxy="http://127.0.0.1:11015"
  export https_proxy="http://127.0.0.1:11015"
  export all_proxy="socks5h://127.0.0.1:1015"
  ```
  Порт `11015` — это встроенный в `vless2socks` HTTP CONNECT прокси, идеально совместимый с Node.js, `fetch` и Python.

---

### Ошибка 3: Авторизация OAuth зависает («xdg-open: no display»)
* **Симптом:** При вводе команды `claude login` в терминале выводится ссылка для авторизации, но браузер на компьютере не открывается, либо выводится ошибка отсутствия дисплея.
* **Причина:** Headless-консоль WSL не знает, как открыть стандартный браузер Windows.
* **Решение:** Создать исполняемый мост `/usr/local/bin/xdg-open`, который перенаправляет вызовы открытия ссылок в PowerShell хостовой Windows:
  ```bash
  sudo cat << 'EOF' > /usr/local/bin/xdg-open
  #!/bin/bash
  TARGET="$1"
  if [ -z "$TARGET" ]; then
      exit 0
  fi
  powershell.exe -NoProfile -NonInteractive -Command "Start-Process '$TARGET'" >/dev/null 2>&1 &
  exit 0
  EOF
  sudo chmod +x /usr/local/bin/xdg-open
  ```
  После этого при логине в Claude Code авторизационная страница мгновенно откроется в браузере по умолчанию на рабочем столе Windows.

---

### Ошибка 4: Ошибка парсинга батника (`'езопасная' is not recognized`)
* **Симптом:** При попытке запустить `.bat` ярлык с рабочего стола консоль `cmd.exe` падает с ошибкой синтаксиса или искаженными символами (кракозябрами).
* **Причина:** `cmd.exe` в Windows крайне чувствителен к кодировке файлов. Если файл `.bat` сохранен в UTF-8 с BOM (Byte Order Mark) или содержит русские комментарии в несовместимой кодовой странице, парсер ломает первую строку.
* **Решение:**
  1. Файлы `.bat` для запуска WSL должны быть строго в кодировке **ASCII (ANSI)** и использовать только латинские символы.
  2. Переносы строк должны быть строго **CRLF (`\r\n`)**.
  3. Для бесшумного запуска GUI без мерцания черного окна консоли используйте связку с `.vbs` (VBScript).

---

### Ошибка 5: Запуск Electron GUI в WSL2 (`--no-sandbox` и GPU)
* **Симптом:** При запуске `claude-desktop` процесс падает с сообщением:
  `[FATAL:zygote_host_impl_linux.cc] The SUID sandbox helper binary was found or is not functioning correctly.`
* **Причина:** Внутри контейнеров и пространств имен WSL2 стандартный песочный механизм Chromium SUID sandbox требует прав, которых у непривилегированного пользователя нет.
* **Решение:** Приложение `claude-desktop` необходимо запускать с флагом `--no-sandbox`:
  ```bash
  claude-desktop --no-sandbox --ozone-platform=x11 --password-store=basic
  ```

---

### Ошибка 6: Зависший `SingletonLock` после сна/перезапуска Windows
* **Симптом:** При клике по ярлыку окно не появляется, в логах: `Request ended (non-user cancelled)`.
* **Причина:** Процесс `claude-desktop` со вчерашнего дня остался в памяти, потеряв дисплей, и удерживает `~/.config/Claude/SingletonLock`. Новый запуск пытается передать фокус мертвому окну и сразу завершается.
* **Решение:** Добавить в `/usr/local/bin/claude-gui` автоматическую проверку: если PID в замке не отвечает (`! kill -0 $PID`), файл замка удаляется перед стартом. Экстренное снятие вручную:
  ```bash
  killall -9 claude-desktop chrome_crashpad_handler; rm -f ~/.config/Claude/Singleton*
  ```

---

### Ошибка 7: Циклический краш GPU (`GPU process launch failed: error_code=1002`)
* **Симптом:** Окно не отображается, в `~/.claude-desktop.log` бесконечные строки `GPU process launch failed: error_code=1002`.
* **Причина:** При флаге `--ozone-platform-hint=auto` Electron выбирает Wayland, который в текущем WSLg крашит процесс GPU.
* **Решение:** Всегда явно указывать стабильный бэкенд: `--ozone-platform=x11`.

---

### Ошибка 8: Ошибка `WSL_E_USER_NOT_FOUND` при запуске ярлыков
* **Симптом:** Окно не запускается, ошибка `getpwnam(default) failed 0. Wsl/WSL_E_USER_NOT_FOUND`.
* **Причина:** Флаг `-u default` при вызове `wsl.exe`. Пользователя `default` в системе нет.
* **Решение:** Не указывать `-u` (WSL берет дефолтного пользователя из `/etc/wsl.conf`) или использовать реального пользователя системы.

---

## 3. Пошаговая установка с нуля

### Шаг 1. Настройка Windows и WSL2
В терминале PowerShell (Администратор):
```powershell
# 1. Проверяем наличие конфигурации .wslconfig
$wslconf = @"
[wsl2]
networkingMode=mirrored

[experimental]
autoProxy=false
"@
Set-Content -Path "$env:USERPROFILE\.wslconfig" -Value $wslconf -Encoding ASCII

# 2. Перезапускаем WSL
wsl --shutdown
```

### Шаг 2. Установка Node.js 20+ и Claude Code CLI
В терминале Ubuntu WSL2:
```bash
# 1. Обновляем пакеты
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl wget git build-essential ca-certificates

# 2. Устанавливаем Node.js LTS (версия 20 или 22)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# 3. Настраиваем системный прокси
sudo tee /etc/profile.d/herdr_claude_env.sh > /dev/null << 'EOF'
export HTTP_PROXY="http://127.0.0.1:11015"
export HTTPS_PROXY="http://127.0.0.1:11015"
export ALL_PROXY="socks5h://127.0.0.1:1015"
export http_proxy="http://127.0.0.1:11015"
export https_proxy="http://127.0.0.1:11015"
export all_proxy="socks5h://127.0.0.1:1015"
EOF
source /etc/profile.d/herdr_claude_env.sh

# 4. Создаем мост для открытия браузера
sudo tee /usr/local/bin/xdg-open > /dev/null << 'EOF'
#!/bin/bash
TARGET="$1"
if [ -z "$TARGET" ]; then
    exit 0
fi
powershell.exe -NoProfile -NonInteractive -Command "Start-Process '$TARGET'" >/dev/null 2>&1 &
exit 0
EOF
sudo chmod +x /usr/local/bin/xdg-open

# 5. Устанавливаем Claude Code глобально через npm
sudo npm install -g @anthropic-ai/claude-code
```

### Шаг 3. Установка официального Claude Desktop Linux GUI
```bash
# 1. Добавляем официальный репозиторий Anthropic (или скачиваем deb-пакет)
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://claude.ai/download/linux/keys/anthropic.asc | sudo gpg --dearmor -o /etc/apt/keyrings/anthropic.gpg 2>/dev/null || true

# 2. Если используется официальный deb:
# wget https://storage.googleapis.com/claude-desktop-linux/claude-desktop_amd64.deb
# sudo apt install -y ./claude-desktop_amd64.deb

# 3. Создаем удобный скрипт-обертку запуска с флагом --no-sandbox
sudo tee /usr/local/bin/claude-gui > /dev/null << 'EOF'
#!/bin/bash
source /etc/profile.d/herdr_claude_env.sh 2>/dev/null
exec claude-desktop --no-sandbox "$@"
EOF
sudo chmod +x /usr/local/bin/claude-gui
```

---

## 4. Ярлыки запуска для рабочего стола Windows

### 1. Ярлык для консольного Claude Code CLI
Файл `C:\Users\<User>\Desktop\Claude Code (WSL).bat` (строго в ASCII/CRLF):
```bat
@echo off
title Claude Code (WSL)
wsl.exe -d Ubuntu bash -lic "claude"
```

### 2. Ярлык для графического интерфейса Claude Desktop (без черного окна)
Создаются два файла:

* **Батник-исполнитель:** `C:\Users\<User>\Desktop\run_claude_gui.bat`
  ```bat
  @echo off
  wsl.exe -d Ubuntu bash -lic "claude-desktop --no-sandbox >/dev/null 2>&1 &"
  ```

* **VBScript запуска (скрывает черное окно консоли):** `C:\Users\<User>\Desktop\Claude Desktop (WSL).vbs`
  ```vbs
  Set WshShell = CreateObject("WScript.Shell")
  WshShell.Run "cmd /c ""C:\Users\79251\Desktop\run_claude_gui.bat""", 0, False
  ```
  *(На этот `.vbs` файл можно назначить иконку приложения `claude.ico` через стандартные свойства Windows).*

---

## 5. Проверка работоспособности и чек-лист

1. **Проверка туннеля из WSL2:**
   ```bash
   curl -I https://api.anthropic.com
   # Должен вернуть HTTP 200 / 301 / 403 от Cloudflare, но НЕ Connection Refused и НЕ 400 Location Unsupported
   ```
2. **Проверка внешнего IP в WSL2:**
   ```bash
   curl https://ifconfig.me
   # IP должен совпадать с выходным адресом инстанса vless2socks (например, Финляндия / Франция / Латвия)
   ```
3. **Запуск CLI:**
   Выполнить команду `claude`. Пройти первичную авторизацию — ссылка автоматически откроется в Windows-браузере.
4. **Запуск GUI:**
   Дважды кликнуть на ярлык `Claude Desktop (WSL).vbs` на рабочем столе — через 2–3 секунды откроется нативное окно Claude Desktop на рабочем столе Windows.
