# Руководство по интеграции исследовательских ИИ-скиллов

В студенческом проекте **Herdr Research Control Center** оркестрация баз данных, управление конфигурациями узлов и балансировка инференса выполняются специализированными **исследовательскими агентами (LLM)**.

Это руководство описывает скиллы (Skills) и пошаговые протоколы, с помощью которых агенты конфигурируют интеграции `AionUi`, рабочие узлы Type-B и ансамбль рабочих узлов Type-A (`OmniRoute`) для проведения сравнительных тестов.

---

## Исследовательская гипотеза проекта

> **Гипотеза:** *Ансамбль распределённых специализированных сабагентов, закреплённых за независимыми сетевыми сокетами, превосходит монолитные модели по показателям устойчивости к нагрузке, широте охвата задач и стабильности задержки.*

---

## Скилл 1: Оркестрация кластера узлов Type-A в OmniRoute (Round-Robin)

### Назначение:
Агенты используют этот протокол для автоматического подключения и балансировки пула исследовательских узлов Type-A через шлюз OmniRoute на порту `20128`. При заполнении окна запросов отдельного узла роутер автоматически переключает поток на соседний узел для непрерывного замера метрик.

### Протокол действий:
1. **Проверка сокетов исследовательских узлов:**
   Опросить локальные TCP-порты начиная с `1081`. 
   Каждый узел связывается со своим выделенным портом (`node-1` -> 127.0.0.1:1081, `node-2` -> 127.0.0.1:1082...).
2. **Регистрация провайдера в AionUi:**
   ```bash
   "$AIONUI_HELPER_BIN" config providers create <<'JSON'
   {
     "name": "OmniRoute Type-A Cluster",
     "platform": "custom",
     "base_url": "http://127.0.0.1:20128/v1"
   }
   JSON
   ```
3. **Обновление роутинга OmniRoute:**
   Зафиксировать активные порты в таблице `proxy_registry` и обновить состав моделей в мультиагентном комбо-маршруте (`storage.sqlite`).

---

## Скилл 2: Интеграция исследовательского узла Type-B в AionUi

### Назначение:
Обеспечивает подключение специализированного узла Type-B к мультиагентному графу задач AionUi через унифицированный интерфейс протокола ACP (`agent_type = 'acp'`) с изолированным каналом замера задержки (порт `1015`).

### Протокол действий:
*Примечание для исследователя:* Не вызывайте консольный бинарник `sqlite3` напрямую — он может отсутствовать в среде. Используйте Python-модуль `sqlite3` через `python3 -c "..."` для прямого обращения к `/home/f/.aionui-web/aionui-backend.db`.

**Шаг 1: Извлечение учетных данных и регистрация провайдера**
```bash
TOKEN=$(python3 -c "import json, os; p=os.path.expanduser('~/.credentials.json'); print(json.load(open(p)).get('accessToken','')) if os.path.exists(p) else print('')")

"$AIONUI_HELPER_BIN" config providers create <<JSON
{
  "name": "Specialized Reasoning (Node Type-B)",
  "platform": "custom",
  "base_url": "http://127.0.0.1:1015/v1",
  "api_key": "${TOKEN}"
}
JSON
```

**Шаг 2: Настройка изоляции канала связи (Dedicated Channel :1015)**
Для обеспечения чистоты телеметрии трафик рабочего узла маршрутизируется через выделенный интерфейс `127.0.0.1:1015`:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/f/.aionui-web/aionui-backend.db')
cur = conn.cursor()
cur.execute('''
  UPDATE agent_metadata 
  SET environment_variables = '{\"HTTP_PROXY\":\"http://127.0.0.1:1015\",\"HTTPS_PROXY\":\"http://127.0.0.1:1015\"}'
  WHERE agent_type = 'acp'
''')
conn.commit()
conn.close()
"
```

---

## Скилл 3: Проверка воспроизводимости результатов (Verification Protocol)

1. Отправить контрольный запрос к тестовому комбо:
   ```bash
   curl --noproxy '*' -s -X POST http://127.0.0.1:20128/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer sk-omniroute-secret" \
     -d '{"model":"subagent-ensemble","messages":[{"role":"user","content":"ping"}],"max_tokens":10}'
   ```
2. Проверить в логах OmniRoute (`~/.omniroute/logs/application/app.log`) выбор узла и время отклика (`duration_ms`).
3. Зафиксировать статус валидности схемы данных SQLite через `PRAGMA integrity_check`.

---

## Скилл 4: aiWatcher — сторож сервисов WSL2 (встроен в Herdr Control Center)

### Назначение:
Следить, чтобы AionUi (:25808) и OmniRoute (:20128) в WSL2 всегда были запущены, и не мешать ремонту базы AionUi.

### Протокол действий:
1. Состояние сторожа смотреть на вкладке «Маршруты» → карточка «👁 aiWatcher» (значок, статусы, журнал) или в `watchdog_config.json`.
2. Сервис «🔴 НЕ ЗАПУСТИЛСЯ»: прочитать строку `Failed to start <сервис>: <вывод>` в журнале и выполнить `start_cmd` вручную в WSL, чтобы увидеть ошибку.
3. Перед любыми работами с `~/.aionui-web/aionui-backend.db*` ставить флаг обслуживания: использовать скрипты `aionUi_helper` (`repair_aionui.bat`, `restore_aionui.bat`, `fix_aionui_login.bat`) или `aionui_maint.maintenance()`. Без флага сторож поднимет AionUi посреди работ.
4а. Статус «🟠 НЕ ОТВЕЧАЕТ (502)»: AionUi жив, но `/api/auth/status` = 502, и в браузере экран входа «Connection failed». Сторож сам сделает чистый перезапуск после 3 неудачных проверок. Если статус «🔴 ЛЕЧЕНИЕ НЕ ПОМОГЛО», причина почти всегда в битой базе: нажать «🛠 Починить базу AionUi», затем «♻ Перезапустить AionUi (чисто)». Разбор: `docs/AIWATCHER.md`, раздел 2а.
4б. OmniRoute лежит (дашборд :20128 «Server is unreachable», `curl http://127.0.0.1:20128/` = `000`), а сторож показывает «работает»: проверить, что в `watchdog_config.json` у omniroute `check_cmd` проверяет порт 20128, а `start_cmd` = `tmux ... 'omniroute serve --no-open'`. Команды `omniroute start` в v3.8+ **нет**, и `grep` по имени процесса даёт ложное «живой». Скилл: `.agents/skills/omniroute-down/SKILL.md`, разбор: `docs/AIWATCHER.md`, раздел 2б.
4в. OmniRoute умирает по SIGHUP сразу после перезапуска AionUi: не останавливать AionUi через `pkill -f aionui-web` / `tmux kill-server` (убивают сервер tmux со всеми сессиями), только `aionui_maint.stop_aionui()`. OmniRoute держать на своём сервере `tmux -L omniroute`. Команды из Windows в WSL — только `wsl.exe -e bash -lc`, проверять запуском из Windows-Python. Разбор: `docs/AIWATCHER.md`, раздел 2в.
4. Статус «Внешний aiWatcher»: работает старое отдельное приложение. Нажать «Остановить внешний aiWatcher» (отключит и его автозапуск) или закрыть его вручную.
5. **Никогда** не запускать `aionui-web start` вручную из терминала агента: окружение агента содержит прокси сокета :1015, и AionUi покажет экран входа «Connection failed».
6. После правок `config_app.py` или `watchdog_manager.py` запускать: `python -m pytest test_watchdog_manager.py test_aiwatcher_card.py test_ui_command_refs.py test_app_ui.py -q`.

Подробно: [docs/AIWATCHER.md](docs/AIWATCHER.md).
