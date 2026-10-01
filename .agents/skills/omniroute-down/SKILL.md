---
name: omniroute-down
description: >-
  Починка OmniRoute в WSL2: дашборд http://localhost:20128 показывает "Server is unreachable. Reconnecting..."
  и "Error Short: Failed to fetch", порт 20128 не отвечает, `omniroute start` падает с ошибкой
  "too many arguments for 'serve'", aiWatcher показывает OmniRoute как работающий, хотя шлюз лежит, или
  OmniRoute снова и снова умирает по SIGHUP (в app.log "[Shutdown] Received SIGHUP") после перезапуска AionUi.
---

# Скилл: OmniRoute не отвечает (:20128)

Полный разбор: `D:\My files\herdrControlGui\docs\AIWATCHER.md`, разделы 2б и 2в.

## 1. Диагностика (WSL)

```bash
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:20128/   # 000 = сервер не запущен
tail -20 ~/.omniroute/logs/application/app.log     # "[Shutdown] Received SIGHUP ... Bye." = убит закрытием сессии
tmux -L omniroute ls                               # есть ли сессия omniroute (свой сервер tmux!)
```

Не проверять живость через `ps aux | grep omniroute`: это совпадёт с любой командой, где встречается
слово «omniroute» (в том числе с вашей собственной), и покажет ложное «живой».

## 2. Запуск и восстановление через Actions Framework

Самый быстрый и безопасный способ запуска / перезапуска:
```powershell
& ".venv\Scripts\python.exe" -m Omni_Aion.actions --start --force-restart
# Либо через указание блока 5:
& ".venv\Scripts\python.exe" -m Omni_Aion.actions --block 5
```

### Ручной запуск (WSL):
```bash
tmux -L omniroute kill-session -t omniroute 2>/dev/null
tmux -L omniroute new -d -s omniroute bash -lc 'omniroute serve --no-open'
for i in $(seq 1 30); do c=$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' -m 2 http://127.0.0.1:20128/); [ "$c" != 000 ] && break; sleep 1; done; echo $c
```

- В OmniRoute v3.8+ **нет** подкоманды `start`, только `omniroute serve` (или просто `omniroute`).
- Запускать только в tmux: `nohup` не спасает, Node сам ловит SIGHUP и завершается при закрытии сессии WSL.
- Только на **своём** сервере tmux (`-L omniroute`), не на общем с AionUi: см. раздел 3.
- **Ошибка HTTP 500 при старте (`markAsUncloneable is not a function`):** вызвана старой версией Node.js 20 в Edge runtime Next.js 16. Решается обновлением до Node.js 22 LTS через `python -m Omni_Aion.actions --block 1`.

## 3. OmniRoute умирает снова и снова (SIGHUP после перезапуска AionUi)

Признак: в `~/.omniroute/logs/application/app.log` строка `[Shutdown] Received SIGHUP ... Bye.`, а сессия
`aionui` в `tmux ls` создана заново в ту же минуту. Цепочка:

1. Сервер tmux носит командную строку клиента, который его создал: `tmux new -d -s aionui ... aionui-web start`.
   Проверка: `pgrep -af 'aionui-web start'` покажет и `tmux new ...`.
2. Любой `pkill -f 'aionui-web start'` / `pkill -f aionui-web` / `tmux kill-server` при остановке AionUi убивает
   **весь** сервер tmux. Все сессии, в том числе OmniRoute, получают SIGHUP.
3. Остановку AionUi запускал сам aiWatcher: «лечение» (`fix_aionui_login.py --fix` → `aionui_maint.stop_aionui()`)
   срабатывало каждые 10 мин, потому что проверка здоровья через `wsl.exe bash -lc` всегда падала (см. ниже).

Исправлено:
- OmniRoute живёт на отдельном сервере tmux `-L omniroute`;
- `aionui_maint.PROC_PATTERNS` привязаны к началу командной строки (`^[^ ]*[a]ionui-web start`) и не задевают tmux;
- правило: не останавливать AionUi через `pkill -f aionui-web` или `tmux kill-server`, только `maint.stop_aionui()`.

## 4. Ловушка `wsl.exe` без `-e` (Windows → WSL)

`wsl.exe bash -lc "<cmd>"` сначала отдаёт строку оболочке по умолчанию, и та заранее раскрывает `$c`, `$(...)`
и срезает кавычки. В итоге:
- проверка OmniRoute `c=$(curl ...); [ "$c" != 000 ]` из Windows **всегда** успешна (ложное «живой»);
- проверка здоровья AionUi **всегда** падает: `[: : integer expression expected`.

Правильно: `wsl.exe -e bash -lc "<cmd>"`. Проверить команду сторожа именно так, как её выполняет Windows:

```bash
"/mnt/d/My files/herdrControlGui/.venv/Scripts/python.exe" -c "import watchdog_manager as w; print(w.run_cmd(w.OMNIROUTE_CHECK_CMD))"
```

Проверка из WSL-терминала этот баг **не показывает**, только из Windows.

## 5. Сторож (Herdr Control Center → «Маршруты» → aiWatcher)

В `watchdog_manager.py` команды OmniRoute заданы в `OMNIROUTE_CHECK_CMD` (проверка порта 20128) и
`OMNIROUTE_START_CMD` (`tmux -L omniroute` + `omniroute serve --no-open`), команды выполняются через
`wsl.exe -e bash -lc`. При загрузке `_upgrade_app` сам заменяет старые команды, а `start()` записывает их
в `watchdog_config.json`. Если сторож всё равно показывает «работает» при лежащем шлюзе:
перезапустить Herdr Control Center, чтобы подхватить новый код, и проверить `watchdog_config.json`.

После правок: `python -m pytest test_watchdog_manager.py test_aiwatcher_card.py -q`
(или `python -m unittest test_watchdog_manager test_aiwatcher_card`).
