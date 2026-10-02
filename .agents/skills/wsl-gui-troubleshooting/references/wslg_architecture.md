# Архитектура WSLg и жизненный цикл GUI / Electron приложений

## 1. Как работает WSLg (Windows Subsystem for Linux GUI)
- **Композитор Weston (System Distro):** WSL2 запускает изолированный системный дистрибутив WSLg, в котором работает модифицированный композитор Weston Wayland.
- **Мост XWayland:** Для приложений X11 работает XWayland-сервер, слушающий сокет `/tmp/.X11-unix/X0` (или `DISPLAY=:0`).
- **Связь с Windows через RemoteApp (RDP RAIL):** Weston транслирует каждое окно Linux в отдельное окно Windows по протоколу RDP RAIL (Remote Applications Integrated Locally). На стороне Windows за отрисовку и интеграцию в панель задач отвечает процесс `C:\Program Files\WSL\msrdc.exe`.

## 2. Почему стандартные Electron-приложения «ломаются» в WSL2

### А. Сворачивание в трей при закрытии (Ghost Process)
- В современных Electron-приложениях событие закрытия окна (`close` / `window-all-closed`) по умолчанию перехватывается, и приложение прячет окно (`window.hide()`), оставляя процесс в фоне для быстрого открытия через системный трей.
- **Проблема в WSLg:** В Windows панель задач / системный трей не интегрированы с Linux Tray (StatusNotifierWatcher / AppIndicator). Когда окно скрывается, у пользователя нет никакого способа вызвать его обратно.
- Процесс Electron (включая Node.js event loop, renderers, GPU-процесс, crashpad) остается висеть в памяти, потребляя сотни мегабайт ОЗУ.

### Б. Блокировка повторного запуска (Single Instance Lock Deadlock)
- Electron использует `app.requestSingleInstanceLock()` для предотвращения параллельного запуска двух копий с одной директорией пользователя (`~/.config/<App>/SingletonLock`).
- При повторном клике по ярлыку запускается второй процесс. Он видит, что `SingletonLock` указывает на PID первого процесса.
- Второй процесс пытается отправить межпроцессное уведомление (`app.on('second-instance')`) первому процессу, чтобы тот развернул свое окно (`mainWindow.show()`).
- В Linux это уведомление передается через пользовательскую сессию DBus (`/run/user/<uid>/bus`) или локальный сокет.
- **Проблема в WSL2:** WSL2 не запускает полноценный сеанс PAM/logind, поэтому директория `/run/user/<uid>` и сокет DBus отсутствуют.
- В результате второй экземпляр не может достучаться до первого и аварийно/штатно завершает работу (`code 0`). Первый экземпляр сигнал не получает и окно не открывает.

### В. Сбои GPU и песочницы Chrome
- В виртуализированной среде WSLg аппаратное ускорение и рендеринг через Wayland Ozone часто приводят к ошибкам `GPU crash dump id: 1002` или падениям песочницы ядра Linux.
- **Оптимальные флаги запуска:**
  - `--no-sandbox`: избегает конфликтов с пространством имен ядра WSL2.
  - `--ozone-platform=x11`: принудительно направляет рендеринг через стабильный XWayland вместо сырого Wayland-бэкенда.
  - `--password-store=basic`: предотвращает зависание при попытке обращения к отсутствующему gnome-keyring / kwallet.

## 3. Чек-лист диагностики проблем с ярлыком WSL GUI

1. **Проверить запущенные процессы:**
   ```bash
   wsl -d Ubuntu ps aux | grep -iE '<app_name>|electron'
   ```
2. **Проверить наличие окон X11:**
   ```bash
   wsl -d Ubuntu DISPLAY=:0 xdotool search --onlyvisible --class <app_name>
   wsl -d Ubuntu DISPLAY=:0 xwininfo -root -tree
   ```
3. **Проверить состояние `SingletonLock`:**
   ```bash
   wsl -d Ubuntu ls -la ~/.config/<App>/Singleton*
   ```
4. **Проверить логи WSLg:**
   ```bash
   wsl -d Ubuntu tail -n 50 /mnt/wslg/weston.log
   ```
5. **Проверить ошибки при прямом запуске:**
   ```bash
   wsl -d Ubuntu bash -lc "/usr/local/bin/<app>-gui"
   ```
