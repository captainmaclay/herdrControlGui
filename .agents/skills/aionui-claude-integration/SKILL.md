---
name: aionui-claude-integration
description: >-
  Руководство и автоматизация добавления Claude Code на главный экран AionUi (/#/guid),
  настройка метаданных агента в aioncore (agent_metadata), оверлеев ассистентов
  (assistant_definitions, assistant_overlays, assistant_overrides) и проверка работоспособности.
---

# Скилл: Интеграция Claude Code в AionUi

Этот скилл содержит полное описание архитектуры взаимодействия AionUi с Claude Code, решение типичных проблем при добавлении агента на главный экран и автоматизированные сценарии настройки.

---

## Архитектурные особенности AionUi и aioncore

1. **Главный экран (`/#/guid`):**
   - Кнопки выбора ассистентов на главной странице формируются бэкендом через эндпоинт `GET /api/assistants`.
   - На экран выводятся только ассистенты, у которых `enabled: true`.
   - Для того чтобы ассистент появился в этом списке, должны согласованно существовать 4 сущности в SQLite базе `~/.aionui-web/aionui-backend.db`:
     - `agent_metadata`: карточка агента (ID: `2d23ff1c`).
     - `assistant_definitions`: определение ассистента (`bare:2d23ff1c`).
     - `assistant_overlays`: настройки видимости (`enabled = 1`, `sort_order = -3`).
     - `assistant_overrides`: зеркальная таблица обратной совместимости (`enabled = 1`).

2. **Критическое требование к типизации `agent_type`:**
   - В ядре `aioncore` перечисление `AgentType` в Rust строго типизировано:
     `AgentType::Acp`, `AgentType::Nanobot`, `AgentType::Remote`, `AgentType::Aionrs`, `AgentType::Antigravity`, `AgentType::Gemini`, `AgentType::Codex`.
   - **Важно:** Варианта `AgentType::Claude` в перечислении **нет**! Claude Code работает через протокол ACP.
   - Поэтому в таблице `agent_metadata`:
     - `agent_type` **ОБЯЗАТЕЛЬНО** должен быть равен `'acp'`.
     - `backend` равен `'claude'`.
     - `agent_source` равен `'builtin'`.
     - `agent_source_info` равен `'{"binary_name":"claude"}'`.
     - `command` равен `'claude'`.
   - Если указать `agent_type = 'claude'`, Serde-десериализация Rust завершается ошибкой `unknown variant 'claude'`, ядро отбрасывает агента из `/api/agents/management`, а вызов `GET /api/assistants/bare:2d23ff1c` возвращает `404 Not Found` с ошибкой `assistant could not resolve a runtime backend`.

3. **Синхронизация Overlays и Overrides:**
   - При старте `aioncore` выполняет процедуру `sync_legacy_overrides_to_new_states`.
   - Если запись обновлена только в одной из таблиц (`assistant_overlays` или `assistant_overrides`), вторая перезапишет ее старыми значениями при загрузке.
   - Обе таблицы должны обновляться согласованно.

---

## Автоматическая настройка (1 клик)

Для добавления или восстановления Claude Code на главном экране запустите скрипт:

### В WSL:
```bash
python3 /mnt/d/My\ files/herdrControlGui/scripts/setup_claude_aionui.py
```

### В Windows:
Двойной клик по файлу:
`D:\My files\herdrControlGui\add_claude_to_aionui.bat`

Скрипт автоматически:
1. Останавливает сессию AionUi в tmux.
2. Делает резервную копию базы данных.
3. Проверяет и записывает правильные метаданные `2d23ff1c` (`agent_type = 'acp'`).
4. Настраивает оверлеи с высоким приоритетом отображения (`sort_order = -3`).
5. Проверяет `PRAGMA integrity_check`.
6. Перезапускает AionUi и верифицирует `GET /api/assistants` и `GET /api/assistants/bare:2d23ff1c`.

---

## Ручной пошаговый алгоритм

Если требуется выполнить процедуру вручную:

### Шаг 1: Остановка AionUi
```bash
tmux kill-session -t aionui 2>/dev/null
pkill -f 'aioncore' 2>/dev/null
pkill -f 'aionui-web' 2>/dev/null
sleep 2
```

### Шаг 2: Обновление базы данных SQLite
Выполните через Python в WSL:
```python
import sqlite3

db = "/home/f/.aionui-web/aionui-backend.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

# 1. agent_metadata
cur.execute("""
UPDATE agent_metadata
SET agent_type = 'acp',
    backend = 'claude',
    agent_source = 'builtin',
    agent_source_info = '{"binary_name":"claude"}',
    command = 'claude',
    args = '[]',
    env = '[]',
    native_skills_dirs = '[".claude/skills"]',
    behavior_policy = '{"supports_side_question":true,"self_identity_sticky":true,"session_load_via_meta_field":true,"supports_team":true}',
    yolo_id = 'bypassPermissions',
    sort_order = 3100,
    last_check_status = 'online',
    last_check_kind = 'manual',
    last_check_error_code = NULL,
    last_check_error_message = 'claude 2.1.280',
    last_check_latency_ms = 50,
    last_check_at = CAST(strftime('%s','now') AS INTEGER)*1000,
    last_success_at = CAST(strftime('%s','now') AS INTEGER)*1000,
    updated_at = CAST(strftime('%s','now') AS INTEGER)*1000
WHERE id = '2d23ff1c'
""")

# 2. assistant_overrides (legacy sync)
cur.execute("""
INSERT INTO assistant_overrides (user_id, assistant_id, enabled, sort_order, last_used_at, updated_at)
VALUES ('system_default_user', 'bare:2d23ff1c', 1, -3, NULL, CAST(strftime('%s','now') AS INTEGER)*1000)
ON CONFLICT(user_id, assistant_id) DO UPDATE SET
    enabled = 1,
    sort_order = -3,
    updated_at = CAST(strftime('%s','now') AS INTEGER)*1000
""")

# 3. assistant_overlays (current state)
cur.execute("""
INSERT INTO assistant_overlays (user_id, assistant_definition_id, enabled, sort_order, agent_id_override, last_used_at, created_at, updated_at)
VALUES ('system_default_user', 'asstdef_01a0d0f4-9a89-7181-b141-34efd1806613', 1, -3, NULL, NULL, CAST(strftime('%s','now') AS INTEGER)*1000, CAST(strftime('%s','now') AS INTEGER)*1000)
ON CONFLICT(user_id, assistant_definition_id) DO UPDATE SET
    enabled = 1,
    sort_order = -3,
    updated_at = CAST(strftime('%s','now') AS INTEGER)*1000
""")

conn.commit()
assert cur.execute("PRAGMA integrity_check").fetchall() == [('ok',)]
conn.close()
```

### Шаг 3: Перезапуск сервиса
```bash
tmux new -d -s aionui 'env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY NO_PROXY=* no_proxy=* /home/f/.local/bin/aionui-web start --no-open --port 25808'
sleep 3
```

### Шаг 4: Проверка доступности API
```bash
# Проверка в списке ассистентов
curl -s --noproxy '*' http://127.0.0.1:25808/api/assistants | grep -o '"id":"bare:2d23ff1c"[^}]*'

# Проверка статуса онлайн
curl -s --noproxy '*' http://127.0.0.1:25808/api/assistants/bare:2d23ff1c
```
Ожидаемый ответ: `"success": true`, `"agent_status": "online"`, `"enabled": true`.

---

## Диагностический чек-лист

Если кнопка не отображается в веб-интерфейсе:
1. Нажмите **`Ctrl + F5`** в браузере (для очистки закэшированного состояния React-хранилища).
2. Проверьте, доступен ли бинарник Claude в `$PATH`: `wsl which claude` (должен возвращать `/usr/local/bin/claude` или `/home/f/.local/bin/claude`).
3. Проверьте статус прокси: Claude настроен на работу через порт `1015` (SOCKS5) и `11015` (HTTP). Убедитесь, что прокси-сервер запущен в Windows.
4. Проверьте целостность базы данных: `PRAGMA integrity_check` должен возвращать `ok`.
