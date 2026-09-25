---
name: aionui-session-resume
description: >-
  Диагностика и починка ошибки AionUi "UNKNOWN_UPSTREAM_ERROR / No conversation found with session ID"
  у агента Claude Code в WSL2. Используйте, когда чат claude-temp-* после простоя выдаёт карточку
  Upstream/Retryable, когда модель «забыла» контекст диалога, или когда в aioncore.log есть
  "cleared dead resume anchor".
---

# Скилл: Починка «No conversation found with session ID» в AionUi

Подробный разбор: `D:\My files\aionUi_helper\docs\SESSION_RESUME_NOT_FOUND.md`.

## Шаг 1. Подтвердить диагноз

```bash
LOG=~/.aionui-web/logs/$(date +%Y/%m/%d)/$(date +%F).aioncore.log
grep -n "No conversation found\|cleared dead resume anchor" "$LOG" | tail
```

Диагноз подтверждён, если есть пара строк: `No conversation found with session ID: <uuid>`
и `cleared dead resume anchor ... conversation_id: <id>`.
Если строк нет, это другая ошибка: смотрите `AUTOCOMPACT_EMPTY_RESPONSE.md` или `REPAIR_AIONUI_DB.md`.

## Шаг 2. Разблокировать чат пользователя

Ничего чинить в базе не нужно: AionUi уже сбросил якорь. Скажите пользователю:
1. отправить сообщение в чат ещё раз;
2. первым сообщением дать сводку контекста, потому что память модели о диалоге потеряна.

Не запускайте `repair_aionui.bat` и `restore_aionui.bat` из-за этой ошибки: база цела.

## Шаг 3. Найти, куда пропали файлы сессий

```bash
ls ~/.claude/projects/ | grep claude-temp
ls -la ~/.claude/projects/*claude-temp-<id>/
grep -i cleanupPeriodDays ~/.claude/settings.json ~/.claude.json
find ~/.claude/oauth-profiles -name "*.jsonl" -path "*claude-temp*" | head
tmux show-environment -t aionui 2>/dev/null | grep -i CLAUDE_CONFIG_DIR
cat ~/.claude/.last-cleanup
```

Кодировка папки: `/home/f/.aionui-web/conversations/users/system_default_user/2026/09/24/claude-temp-d8175bf0`
→ `~/.claude/projects/-home-f--aionui-web-conversations-users-system-default-user-2026-09-24-claude-temp-d8175bf0`
(каждый `/`, `.` и `_` заменяется на `-`).

## Шаг 4. Устранить причину

- `.jsonl` лежат в `oauth-profiles/<имя>/projects`: в окружении AionUi остался `CLAUDE_CONFIG_DIR`.
  Выполнить `tmux set-environment -t aionui -u CLAUDE_CONFIG_DIR` и перезапустить AionUi через aiWatcher.
- `cleanupPeriodDays` равен 0 или маленькому числу: поставить `"cleanupPeriodDays": 30` в `~/.claude/settings.json`.
- Папок `claude-temp-*` нет совсем: их удалили вручную или скриптом. Найти, что чистит `~/.claude/projects`,
  и исключить эту папку.
- Ошибка сразу после смены аккаунта Claude в herdrControlGui: после переключения начинать новый чат.

## Шаг 5. Проверка

Оставить любой чат Claude в покое на 15+ минут (сработает Idle kill), затем написать в него.
В логе не должно появиться новых `No conversation found`.

## Правила безопасности

- Не удалять и не перемещать `~/.claude/projects/`, `~/.claude/.credentials.json`, `~/.aionui-web/aionui-backend.db*`.
- Не править базу AionUi, пока процесс запущен.
- Не обращаться к `~/.claude` из Windows через `\\wsl$` в цикле: это приводит к `WinError 995` и зависаниям 9P.
