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
python3 /mnt/d/My\ files/herdrControlGui/scripts/repair_aionui_db.py
```

Скрипт автономно остановит процесс, создаст резервную копию, перенесет все таблицы и сообщения в чистый файл без повреждений, пересоздаст индексы, очистит блокировки и перезапустит AionUi.
