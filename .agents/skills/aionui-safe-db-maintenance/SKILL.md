---
name: aionui-safe-db-maintenance
description: >-
  Правила безопасной работы с базой AionUi (~/.aionui-web/aionui-backend.db, SQLite WAL) в WSL2.
  Используйте этот скилл ПЕРЕД тем, как писать, менять или запускать любой скрипт, который
  останавливает AionUi, копирует, правит, подменяет или восстанавливает файл базы; при повторяющейся
  ошибке 'database disk image is malformed'; при 'A team member runtime failed to start' /
  'Agent runtime failed to start' в командах (team) AionUi; при пропаже -wal/-shm у работающего AionUi;
  при вопросах «что ломает базу AionUi» и «как сделать базу отказоустойчивой».
---

# Скилл: безопасное обслуживание базы AionUi

Полная инструкция: `/mnt/d/My files/aionUi_helper/docs/SAFE_DB_MAINTENANCE.md`.

## Главное правило

База = три файла: `aionui-backend.db` + `-wal` + `-shm`. Любая запись в `.db`, подмена
или удаление `-wal`/`-shm` допустимы **только** когда AionUi остановлен и aiWatcher
на паузе по флагу `~/.aionui-web/.maintenance`.

Причина порчи (подтверждена логами 24–25.09.2026): скрипт убивал AionUi, aiWatcher через
≤ 5 с поднимал новый экземпляр, а скрипт подменял базу или удалял `-wal`/`-shm` под живым
процессом → `malformed`. Файловая система (ext4 в WSL) не виновата.

## Диагностика

1. «Team member runtime failed to start» → смотреть в логе причину, а не менять модель:
   ```bash
   grep -h "runtime attach failed\|malformed" ~/.aionui-web/logs/$(date +%Y/%m/%d)/*.aioncore.log | tail -5
   ```
   Если там `database disk image is malformed` — это база, лечится кнопкой
   «🛠 Починить базу AionUi» (Herdr → Маршруты → aiWatcher) или скиллом `repair-aionui-db`.
2. Целостность (можно при работающем AionUi):
   ```bash
   python3 -c "import sqlite3; c=sqlite3.connect('file:$HOME/.aionui-web/aionui-backend.db?mode=ro', uri=True); print(c.execute('PRAGMA quick_check').fetchall())"
   ```
3. Кто сломал: найти первую `malformed` в `~/.aionui-web/logs/YYYY/MM/DD/*.aioncore.log`
   и посмотреть события за несколько минут до неё:
   ```bash
   grep -h "instance guard\|SIGTERM\|SIGKILL\|parent exit" ~/.aionui-web/logs/2026/09/*/*.aioncore.log
   ```
   Частые `acquired data-dir instance guard` без `Received SIGTERM` перед ними или
   `Grace period expired, sending SIGKILL` = AionUi убивали посреди работы.

## Как писать скрипт, который трогает базу

Всегда через `/mnt/d/My files/aionUi_helper/scripts/aionui_maint.py`:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aionui_maint as maint

def do_work() -> bool:
    if not maint.stop_aionui():            # остановка + 8 с проверки, что никто не поднял
        return False                        # база не тронута
    maint.backup_db_files(Path.home() / ".aionui-web" / "db_backups", "before_x")  # .db + -wal + -shm
    ...                                     # работа с базой, integrity_check до и после
    subprocess.run(maint.clean_start_cmd(), check=False)   # запуск без прокси, --no-open
    return True

if __name__ == "__main__":
    with maint.maintenance("x"):            # флаг .maintenance, снимается даже при ошибке
        ok = do_work()
    sys.exit(0 if ok else 1)
```

Откат из бэкапа: сначала удалить текущие `-wal`/`-shm`, затем вернуть `.db` **и** его
`-wal`/`-shm` из того же набора.

## Запрещено

- `pkill` / `tmux kill-session` + `sleep` без `maint.stop_aionui()`;
- `shutil.copy2` только `.db` (бэкап без журнала неполный);
- удалять `-wal`/`-shm`, пока AionUi запущен;
- открывать базу на запись при работающем AionUi (чтение — только `?mode=ro`);
- SIGKILL AionUi, если можно дождаться штатного завершения;
- открывать базу из Windows по `\\wsl$\...`.

Горячий бэкап без остановки: `sqlite3 ~/.aionui-web/aionui-backend.db ".backup /путь/копия.db"`.

## Статус скриптов (25.09.2026)

| Скрипт | Состояние |
| :--- | :--- |
| `repair_aionui_db.py`, `restore_aionui.py`, `fix_aionui_login.py` | используют `aionui_maint` |
| `setup_claude_aionui.py` (`add_claude_to_aionui.bat`) | исправлен 25.09: флаг, `stop_aionui()`, бэкап трёх файлов, правильный откат |
| `backup_aionui.py` | только чтение, безопасен |

Новый скрипт, меняющий базу, без этого шаблона — считать ошибкой и исправлять до запуска.
