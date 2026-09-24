#!/usr/bin/env python3
r"""Утилита автоматического восстановления базы данных и чатов AionUi (WSL2).

Решает проблему:
- Повреждения B-Tree страниц SQLite ("database disk image is malformed", "btreeInitPage error 11").
- Исчезновения чатов ("Conversation not found", 404/500 ошибки на /api/conversations и /api/messages).
- Зависших lock-файлов (instance.lock, migrate.lock).
- Несогласованного состояния WAL-журнала.

Использование:
  В WSL:     python3 repair_aionui_db.py
  В Windows: wsl -d Ubuntu -- python3 /mnt/d/My\ files/herdrControlGui/scripts/repair_aionui_db.py
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def log(msg: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


def run_cmd(cmd: str) -> tuple[int, str]:
    res = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return res.returncode, (res.stdout + res.stderr).strip()


def repair_database() -> bool:
    user_home = Path.home()
    aionui_dir = user_home / ".aionui-web"
    db_file = aionui_dir / "aionui-backend.db"
    backup_dir = aionui_dir / "db_backups"

    if not db_file.exists():
        log(f"ОШИБКА: База данных не найдена по пути: {db_file}")
        return False

    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    corrupt_backup = backup_dir / f"aionui-backend.before_repair_{ts}.db"

    log("=== ШАГ 1: Остановка процессов AionUi ===")
    run_cmd("tmux kill-session -t aionui 2>/dev/null")
    time.sleep(1)
    run_cmd("pkill -f 'aioncore' 2>/dev/null")
    run_cmd("pkill -f 'aionui-web' 2>/dev/null")
    time.sleep(1)

    log(f"=== ШАГ 2: Создание резервной копии перед починкой в {corrupt_backup.name} ===")
    try:
        shutil.copy2(db_file, corrupt_backup)
        log("Резервная копия успешно сохранена.")
    except Exception as e:
        log(f"Предупреждение при бэкапе: {e}")

    log("=== ШАГ 3: Сброс WAL-журнала в базу данных ===")
    try:
        con = sqlite3.connect(str(db_file), timeout=5)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        con.close()
        log("WAL-журнал успешно синхронизирован с диском.")
    except Exception as e:
        log(f"Сброс WAL пропущен или завершился с предупреждением: {e}")

    log("=== ШАГ 4: Построение чистой базы данных (глубокий перенос всех таблиц) ===")
    fixed_db = aionui_dir / "aionui-backend.db.fixed_clean"
    if fixed_db.exists():
        fixed_db.unlink()

    src = sqlite3.connect(str(db_file))
    src_cur = src.cursor()

    dst = sqlite3.connect(str(fixed_db))
    dst_cur = dst.cursor()

    # Оптимизация и отключение проверок внешних ключей на время переноса
    dst_cur.execute("PRAGMA foreign_keys = OFF;")
    dst_cur.execute("PRAGMA page_size = 4096;")
    dst_cur.execute("PRAGMA auto_vacuum = FULL;")

    # Извлечение схемы
    tables = src_cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
    ).fetchall()
    indices = src_cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL;"
    ).fetchall()

    for name, sql in tables:
        dst_cur.execute(sql)
    dst.commit()
    log(f"Создано {len(tables)} таблиц в чистой базе.")

    total_recovered = 0
    total_skipped = 0

    for name, _ in tables:
        cols_info = src_cur.execute(f'PRAGMA table_info("{name}")').fetchall()
        cols_cnt = len(cols_info)
        ph = ",".join(["?"] * cols_cnt)

        # Для обычных небольших таблиц пробуем перенести пачкой
        copied_bulk = False
        if name not in ("messages", "agent_metadata"):
            try:
                rows = src_cur.execute(f'SELECT * FROM "{name}"').fetchall()
                if rows:
                    dst_cur.executemany(f'INSERT INTO "{name}" VALUES ({ph})', rows)
                    dst.commit()
                log(f"✓ Таблица '{name}': {len(rows)} строк перенесено успешно.")
                copied_bulk = True
            except Exception:
                copied_bulk = False

        # Если таблица повреждена или это messages/agent_metadata — переносим построчно по rowid
        if not copied_bulk:
            table_good = 0
            table_bad = 0
            try:
                max_rowid = src_cur.execute(f'SELECT max(rowid) FROM "{name}"').fetchone()[0] or 0
            except Exception:
                max_rowid = 10000

            for r in range(1, max_rowid + 1):
                try:
                    row = src_cur.execute(f'SELECT * FROM "{name}" WHERE rowid = {r}').fetchone()
                    if row:
                        dst_cur.execute(f'INSERT INTO "{name}" VALUES ({ph})', row)
                        table_good += 1
                        if table_good % 250 == 0:
                            dst.commit()
                except Exception:
                    table_bad += 1

            dst.commit()
            log(f"✓ Таблица '{name}' (построчное спасение): {table_good} сохранено, {table_bad} поврежденных блоков.")
            total_recovered += table_good
            total_skipped += table_bad

    # Пересоздаем индексы
    log("=== ШАГ 5: Пересоздание B-Tree индексов ===")
    for idx_name, idx_sql in indices:
        try:
            dst_cur.execute(idx_sql)
        except Exception as e:
            log(f"Предупреждение при создании индекса '{idx_name}': {e}")
    dst.commit()

    # Финальная проверка целостности
    check = dst_cur.execute("PRAGMA integrity_check;").fetchall()
    log(f"Результат проверки отремонтированной базы: {check}")

    src.close()
    dst.close()

    if check != [("ok",)]:
        log("ОШИБКА: Ремонт не прошёл проверку целостности!")
        return False

    log("=== ШАГ 6: Атомарная замена базы и очистка lock-файлов ===")
    shutil.move(str(fixed_db), str(db_file))

    # Удаляем временные журналы и зависшие файлы блокировок
    for lock_name in (
        "aionui-backend.db-wal",
        "aionui-backend.db-shm",
        "aionui-backend.db.instance.lock",
        "aionui-backend.db.migrate.lock",
    ):
        f = aionui_dir / lock_name
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass

    log("База данных успешно обновлена и очищена.")

    log("=== ШАГ 7: Перезапуск AionUi в сессии tmux ===")
    start_cmd = (
        "tmux new -d -s aionui 'env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY "
        "NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 /home/f/.local/bin/aionui-web start --no-open --port 25808'"
    )
    run_cmd(start_cmd)
    time.sleep(3)

    log("=== ШАГ 8: Валидация доступности API (до 15 секунд) ===")
    for attempt in range(1, 6):
        time.sleep(2)
        code, out = run_cmd("curl -s --noproxy '127.0.0.1' http://127.0.0.1:25808/api/conversations")
        if '"success":true' in out:
            log(f"УСПЕХ (попытка {attempt}): AionUi полностью работоспособен, чаты и история доступны!")
            return True
        log(f"Попытка {attempt}/5: сервис еще поднимается...")

    log(f"Предупреждение: Сервис не успел ответить за 15 сек. Последний ответ: {out[:200]}")
    return False


if __name__ == "__main__":
    success = repair_database()
    sys.exit(0 if success else 1)
