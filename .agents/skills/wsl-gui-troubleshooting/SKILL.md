---
name: wsl-gui-troubleshooting
description: >-
  Руководство по запуску и отладке Linux GUI / Electron приложений (Claude Desktop, etc.) в WSL2 с интеграцией в Windows через WSLg, устранению зомби-процессов, настройке DBus и оконного цикла.
---

# Устранение неполадок и интеграция Linux GUI приложений в WSL2 (WSLg)

## Основные проблемы Electron-приложений в WSL2
1. **Зомби-процессы при закрытии окна:**
   - В Linux Electron приложения (включая Claude Desktop) при закрытии окна через крестик `[X]` не завершают процесс, а сворачиваются в трей.
   - В WSLg трей Windows отсутствует, поэтому окно скрывается, а процесс продолжает висеть в памяти.
2. **Блокировка Single-Instance Lock:**
   - Electron создает файл замка `~/.config/<App>/SingletonLock`.
   - При повторном клике по ярлыку новый экземпляр видит замок и пытается передать фокус старому экземпляру через DBus / сокет.
   - В WSL2 сессионный DBus (`/run/user/<uid>/bus`) по умолчанию не запущен, и сокет может быть недоступен.
   - В результате второй экземпляр мгновенно завершается (`code 0`), старый экземпляр не открывает окно, и пользователю кажется, что ярлык «не работает».

## Решение для лаунчера (`/usr/local/bin/<app>-gui`)

Скрипт-обертка должен реализовывать следующий алгоритм:

1. **Гарантия наличия `XDG_RUNTIME_DIR` и `DBus`:**
   ```bash
   export DISPLAY=${DISPLAY:-:0}
   export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

   if [ ! -d "$XDG_RUNTIME_DIR" ]; then
       mkdir -m 700 -p "$XDG_RUNTIME_DIR" 2>/dev/null || sudo mkdir -m 700 -p "$XDG_RUNTIME_DIR" 2>/dev/null
       sudo chown "$(id -u):$(id -g)" "$XDG_RUNTIME_DIR" 2>/dev/null
   fi

   if [ ! -e "$XDG_RUNTIME_DIR/bus" ] && command -v dbus-daemon >/dev/null 2>&1; then
       dbus-daemon --session --fork --address="unix:path=$XDG_RUNTIME_DIR/bus" 2>/dev/null
   fi
   export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
   ```

2. **Проверка активного окна и его активация:**
   - Если окно уже открыто — активировать его через `xdotool windowactivate` и выйти:
   ```bash
   if command -v xdotool >/dev/null 2>&1; then
       VISIBLE_WIN=$(xdotool search --onlyvisible --class <app_class> 2>/dev/null | head -n 1)
       if [ -n "$VISIBLE_WIN" ]; then
           xdotool windowactivate "$VISIBLE_WIN" 2>/dev/null
           exit 0
       fi
   fi
   ```

3. **Очистка зависших процессов и замков перед новым запуском:**
   - Если видимого окна нет, любые фоновые процессы приложения — это зомби:
   ```bash
   killall -9 <app_name> chrome_crashpad_handler 2>/dev/null
   rm -f "$HOME/.config/<App>/Singleton"* 2>/dev/null
   ```

4. **Запуск и мониторинг закрытия окна:**
   - Запускать приложение в фоне с флагами `--no-sandbox --ozone-platform=x11`.
   - Отслеживать закрытие окна через `xdotool search --onlyvisible`. Когда окно исчезает, штатно завершать фоновый процесс (`kill -TERM`), предотвращая накопление зомби:
   ```bash
   <app_binary> --no-sandbox --ozone-platform=x11 "$@" &
   MAIN_PID=$!

   if command -v xdotool >/dev/null 2>&1; then
       # Ждем появления окна
       for i in $(seq 1 30); do
           kill -0 "$MAIN_PID" 2>/dev/null || exit 1
           WIN_ID=$(xdotool search --onlyvisible --class <app_class> 2>/dev/null | head -n 1)
           [ -n "$WIN_ID" ] && break
           sleep 0.5
       done

       # Мониторим закрытие окна
       if [ -n "$WIN_ID" ]; then
           while kill -0 "$MAIN_PID" 2>/dev/null; do
               sleep 2
               CURRENT_WIN=$(xdotool search --onlyvisible --class <app_class> 2>/dev/null | head -n 1)
               if [ -z "$CURRENT_WIN" ]; then
                   kill -TERM "$MAIN_PID" 2>/dev/null
                   sleep 1
                   killall -9 <app_name> 2>/dev/null
                   rm -f "$HOME/.config/<App>/Singleton"* 2>/dev/null
                   break
               fi
           done
           exit 0
       fi
   fi
   wait "$MAIN_PID"
   ```

5. **Бесшумный запуск из Windows:**
   - Использовать `WScript.Shell` через `.vbs` для запуска без мелькания черной консоли:
   ```vbscript
   Set WshShell = CreateObject("WScript.Shell")
   WshShell.Run "wsl.exe -d <distro> bash -lc ""/usr/local/bin/<app>-gui""", 0, False
   ```
