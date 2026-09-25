---
name: repair-aionui-db
description: >-
  Диагностика и автоматическое восстановление базы данных SQLite и истории чатов AionUi в WSL2.
  Используйте этот скилл при ошибках 'database disk image is malformed', 'btreeInitPage error 11',
  исчезновении чатов ('Conversation not found', 'No chat history'), 500/404 ошибках на /api/conversations,
  зависших lock-файлах или повреждении WAL-журнала AionUi.
---

# Скилл: Восстановление базы данных и чатов AionUi (WSL2)

Этот скилл предназначен для безопасного и полного восстановления базы данных `~/.aionui-web/aionui-backend.db` без потери пользовательских диалогов и сообщений.

## Быстрый запуск

В любой момент, когда пропали чаты или база выдает ошибки, запустите:
```powershell
.\repair_aionui.bat
```
Или из WSL:
```bash
python3 "/mnt/d/My files/aionUi_helper/scripts/repair_aionui_db.py"
```

Перед запуском убедитесь, что aiWatcher уважает флаг обслуживания:
```bash
grep -c maintenance "/mnt/d/My files/aiWatcher/watcher_config.json"   # 1 = ок, 0 = нажать Turn OFF Watcher
```
Иначе сторож поднимет AionUi посреди ремонта и база снова испортится. Скрипт это обнаружит
и выведет «Ремонт ОТМЕНЁН», не трогая базу.

Скрипт автономно остановит процесс, создаст резервную копию, перенесет все таблицы и сообщения в чистый файл без повреждений, пересоздаст индексы, очистит блокировки и перезапустит AionUi.

## Проверка результата

- В выводе: `✓ AionUi остановлен, повторных запусков за 8 сек не было`, `[('ok',)]`, `УСПЕХ`.
- `ls ~/.aionui-web/aionui-backend.db*`: при работающем AionUi есть и `-wal`, и `-shm`.
- Бэкап до ремонта: `~/.aionui-web/db_backups/aionui-backend.before_repair_*.db` (+ `-wal`, `-shm`).
- Если после ремонта открывается экран входа с `Connection failed`, смотрите скилл `aionui-login-connection-failed`.
- Путь к скрипту: `/mnt/d/My files/aionUi_helper/scripts/repair_aionui_db.py` (использует `aionui_maint.py` рядом).
