---
name: omniroute-down
description: >-
  Починка OmniRoute в WSL2: дашборд http://localhost:20128 показывает "Server is unreachable. Reconnecting..."
  и "Error Short: Failed to fetch", порт 20128 не отвечает, `omniroute start` падает с ошибкой
  "too many arguments for 'serve'", или aiWatcher показывает OmniRoute как работающий, хотя шлюз лежит.
---

# Скилл: OmniRoute не отвечает (:20128)

Полный разбор: `D:\My files\herdrControlGui\docs\AIWATCHER.md`, раздел 2б.

## 1. Диагностика (WSL)

```bash
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:20128/   # 000 = сервер не запущен
tail -20 ~/.omniroute/logs/application/app.log     # "[Shutdown] Received SIGHUP ... Bye." = убит закрытием сессии
tmux ls                                            # есть ли сессия omniroute
```

Не проверять живость через `ps aux | grep omniroute`: это совпадёт с любой командой, где встречается
слово «omniroute» (в том числе с вашей собственной), и покажет ложное «живой».

## 2. Запуск

```bash
tmux kill-session -t omniroute 2>/dev/null
tmux new -d -s omniroute bash -lc 'omniroute serve --no-open'
for i in $(seq 1 30); do c=$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' -m 2 http://127.0.0.1:20128/); [ "$c" != 000 ] && break; sleep 1; done; echo $c
```

- В OmniRoute v3.8+ **нет** подкоманды `start`, только `omniroute serve` (или просто `omniroute`).
- Запускать только в tmux: `nohup` не спасает, Node сам ловит SIGHUP и завершается при закрытии сессии WSL.
- Если порт так и не ответил, выполнить `omniroute serve --no-open` в терминале и прочитать ошибку.

## 3. Сторож (Herdr Control Center → «Маршруты» → aiWatcher)

В `watchdog_manager.py` команды OmniRoute заданы в `OMNIROUTE_CHECK_CMD` (проверка порта 20128) и
`OMNIROUTE_START_CMD` (tmux + `omniroute serve --no-open`). При загрузке `_upgrade_app` сам заменяет старые
команды в `watchdog_config.json`. Если сторож всё равно показывает «работает» при лежащем шлюзе:
перезапустить Herdr Control Center, чтобы подхватить новый код, и проверить `watchdog_config.json`.

После правок: `python -m pytest test_watchdog_manager.py test_aiwatcher_card.py -q`
(или `python -m unittest test_watchdog_manager test_aiwatcher_card`).
