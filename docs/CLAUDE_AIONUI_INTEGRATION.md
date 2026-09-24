# Полное руководство: Интеграция Claude Code в AionUi

В данном документе подробно описаны архитектурные детали, механизм взаимодействия AionUi с Claude Code, структура таблиц SQLite, причины ранее возникавших проблем и инструкция по обслуживанию.

---

## 1. Архитектура взаимодействия AionUi и Claude Code

AionUi использует многоуровневую систему управления агентами и ассистентами:
1. **Frontend (React/Arco Design):** Загружает список доступных ассистентов с бэкенда через запрос `GET /api/assistants`. На главном экране (`/#/guid`) отображаются в виде «таблеток» (pill buttons) только те ассистенты, у которых флаг `enabled` равен `true`.
2. **Backend Core (`aioncore` на Rust):**
   - Управляет жизненным циклом агентов.
   - Поддерживает протокол ACP (Agent Client Protocol) для внешних CLI-агентов.
   - Реализует нативный коннектор `claude_conn.rs` (`aionui_session::backend::claude_conn`), взаимодействующий с CLI-утилитой `claude` (версия 2.1.280+).
   - Синхронизирует состояние ассистентов между старыми версиями схемы базы (`assistant_overrides`) и новой схемой (`assistant_overlays`).
3. **Сеть и безопасность (OmniRoute & VLESS/SOCKS5):**
   - Трафик к API Anthropic (`api.anthropic.com`) направляется через выделенный локальный SOCKS5 прокси-порт `1015` (или HTTP порт `11015`).
   - Механизм Killswitch в `proxy_manager.py` гарантирует, что запросы не утекут через прямой российский IP при обрыве соединения.

---

## 2. Структура сущностей в SQLite (`aionui-backend.db`)

Для того чтобы Claude Code корректно отображался и функционировал, в базе данных задействованы 4 таблицы:

### 1. `agent_metadata` (Карточка агента в реестре)
- **`id`**: `'2d23ff1c'` (фиксированный UUID встроенного агента).
- **`agent_type`**: `'acp'` (**КРИТИЧЕСКИ ВАЖНО!**). В ядре `aioncore` перечисление `AgentType` в Rust поддерживает строго фиксированные варианты: `acp`, `nanobot`, `remote`, `aionrs`, `antigravity`, `gemini`, `codex`. Если задать `agent_type = 'claude'`, Serde возвращает ошибку десериализации, и агент полностью исключается из каталога.
- **`backend`**: `'claude'`.
- **`agent_source`**: `'builtin'`.
- **`agent_source_info`**: `'{"binary_name":"claude"}'`.
- **`command`**: `'claude'`.
- **`args`**: `'[]'`.
- **`icon`**: `'/api/assets/logos/ai-major/claude.svg'`.
- **`yolo_id`**: `'bypassPermissions'`.
- **`behavior_policy`**: `'{"supports_side_question":true,"self_identity_sticky":true,"session_load_via_meta_field":true,"supports_team":true}'`.
- **`agent_capabilities`**: `'{"session_capabilities":{"fork":{}}}'`.
- **`sort_order`**: `3100`.

### 2. `assistant_definitions` (Определение ассистента)
- **`id`**: `'asstdef_01a0d0f4-9a89-7181-b141-34efd1806613'`.
- **`assistant_id`**: `'bare:2d23ff1c'` (стабильный внешний идентификатор).
- **`user_id`**: `'system_default_user'`.
- **`agent_id`**: `'2d23ff1c'`.
- **`source`**: `'generated'`.
- **`owner_type`**: `'system'`.
- **`name`**: `'Claude Code'`.
- **`avatar_type`**: `'emoji'`.
- **`avatar_value`**: `'/api/assets/logos/ai-major/claude.svg'`.

### 3. `assistant_overlays` (Пользовательские настройки отображения)
- **`user_id`**: `'system_default_user'`.
- **`assistant_definition_id`**: `'asstdef_01a0d0f4-9a89-7181-b141-34efd1806613'`.
- **`enabled`**: `1`.
- **`sort_order`**: `-3` (отрицательное значение выводит кнопку в самое начало списка, рядом с Aion CLI).

### 4. `assistant_overrides` (Таблица совместимости)
- При старте `aioncore` выполняет процедуру `sync_legacy_overrides_to_new_states`.
- Должна содержать:
  - `user_id`: `'system_default_user'`.
  - `assistant_id`: `'bare:2d23ff1c'`.
  - `enabled`: `1`.
  - `sort_order`: `-3`.

---

## 3. Решенные проблемы и Root Cause Analysis

### Проблема 1: Ошибка `database disk image is malformed` / исчезновение чатов
- **Причина:** Одновременный доступ к SQLite базе в режиме WAL (`journal_mode=WAL`) из Windows (например, чтение скриптами через 9P-путь `\\wsl.localhost\Ubuntu\...`) и Linux-процессов `aioncore`. Windows lock-механизмы конфликтуют с POSIX fcntl-блокировками Linux, что вызывает повреждение B-Tree страниц и смещение указателей в заголовках страниц.
- **Решение:** 
  1. Создана утилита `repair_aionui_db.py`, выполняющая глубокое построчное спасение данных без блокировок.
  2. Все операции изменения базы производятся исключительно внутри WSL2 после корректного завершения сессии AionUi.

### Проблема 2: Кнопка Claude Code не отображалась на главной
- **Симптомы:** В `GET /api/assistants` возвращались только Aion CLI, Gemini CLI и Antigravity. Запрос к `GET /api/assistants/bare:2d23ff1c` возвращал `404 Not Found`.
- **Причина:** В таблице `agent_metadata` поле `agent_type` было установлено в `'claude'`. Ядро `aioncore` не имеет такого варианта в перечислении `AgentType` (там только `acp`, `nanobot`, `remote`, `aionrs`, `antigravity`, `gemini`, `codex`). В результате строка агента отбрасывалась при десериализации, и связка `assistant_definitions` ➔ `agent_metadata` разрывалась.
- **Решение:** Поле `agent_type` изменено на `'acp'`, `backend` установлен в `'claude'`.

---

## 4. Быстрый запуск и автоматизация

### Запуск через Windows (1 клик):
Двойной клик по скрипту:
```
D:\My files\herdrControlGui\add_claude_to_aionui.bat
```

### Запуск через терминал WSL:
```bash
python3 /mnt/d/My\ files/herdrControlGui/scripts/setup_claude_aionui.py
```

### Проверка результата:
1. В консоли WSL:
```bash
curl -s --noproxy '*' http://127.0.0.1:25808/api/assistants/bare:2d23ff1c
```
Должен вернуть:
```json
{
  "success": true,
  "data": {
    "id": "bare:2d23ff1c",
    "agent_status": "online",
    "agent_status_message": "claude 2.1.280",
    "profile": { "name": "Claude Code" },
    "state": { "enabled": true, "sort_order": -3 }
  }
}
```
2. В браузере перейдите на `http://localhost:25808/#/guid` и нажмите **`Ctrl + F5`**.
