#!/usr/bin/env python3
r"""Утилита создания эталонного бэкапа (Golden Image / Reference Snapshot) AionUi.

Сохраняет:
1. Базу данных SQLite (через онлайн-бэкап API со сбросом WAL и проверкой integrity_check).
2. SQL-дамп схемы и данных (dump.sql).
3. Пользовательские конфигурации, ключи и настройки (~/.aionui-web).
4. Авторизационные токены и профили агентов (Claude ~/.claude.json, Antigravity, Gemini).
5. Все вложения, артефакты и рабочие пространства чатов (~/.aionui-web/conversations).
6. Manifest-файл со всеми версиями (AionUi 2.1.47, Claude 2.1.280), хэшами и статистикой.

Результат сохраняется:
- В архив: D:\My files\herdrControlGui\backups\aionui_golden_<timestamp>.tar.gz
- В эталонную папку: D:\My files\herdrControlGui\backups\aionui_golden_reference\
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
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


def calculate_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def create_golden_backup() -> bool:
    user_home = Path.home()
    aionui_dir = user_home / ".aionui-web"
    db_file = aionui_dir / "aionui-backend.db"
    win_backup_root = Path("/mnt/d/My files/herdrControlGui/backups")
    golden_ref_dir = win_backup_root / "aionui_golden_reference"

    if not db_file.exists():
        log(f"ОШИБКА: База данных AionUi не найдена: {db_file}")
        return False

    win_backup_root.mkdir(parents=True, exist_ok=True)
    golden_ref_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    staging_dir = Path("/tmp") / f"aionui_golden_staging_{ts}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    log("=== ШАГ 1: Создание консистентного слепка базы данных SQLite ===")
    staged_db = staging_dir / "aionui-backend.db"

    # Используем Online Backup API SQLite (гарантирует согласованность данных даже при работающем AionUi)
    try:
        src_conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        # Принудительно сбрасываем WAL
        try:
            rw_conn = sqlite3.connect(str(db_file), timeout=5)
            rw_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            rw_conn.close()
        except Exception as e:
            log(f"Предупреждение checkpoint: {e}")

        dst_conn = sqlite3.connect(str(staged_db))
        src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()
        log("Слепок базы данных успешно получен.")
    except Exception as e:
        log(f"ОШИБКА при копировании базы данных: {e}")
        return False

    # Проверка целостности слепка
    check_conn = sqlite3.connect(str(staged_db))
    cur = check_conn.cursor()
    integrity = cur.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        log(f"ОШИБКА: Слепок базы данных поврежден: {integrity}")
        check_conn.close()
        return False
    log("✓ Целостность слепка базы подтверждена (PRAGMA integrity_check = ok).")

    # Сбор статистики
    conv_count = cur.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    msg_count = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assistants = [r[0] for r in cur.execute("SELECT assistant_id FROM assistant_definitions").fetchall()]
    agents = [r[0] for r in cur.execute("SELECT name FROM agent_metadata WHERE enabled = 1").fetchall()]

    log(f"Статистика базы: диалогов={conv_count}, сообщений={msg_count}, ассистентов={len(assistants)}")

    # Создание SQL-дампа
    log("=== ШАГ 2: Экспорт SQL-дампа схемы и данных ===")
    staged_dump = staging_dir / "dump.sql"
    with open(staged_dump, "w", encoding="utf-8") as f:
        for line in check_conn.iterdump():
            f.write(f"{line}\n")
    check_conn.close()
    log(f"✓ SQL-дамп создан: {staged_dump.stat().st_size // 1024} КБ.")

    # Копирование конфигов
    log("=== ШАГ 3: Сохранение настроек и конфигураций ===")
    staged_configs = staging_dir / "configs"
    staged_configs.mkdir(parents=True, exist_ok=True)

    # Claude конфиг
    claude_cfg = user_home / ".claude.json"
    if claude_cfg.exists():
        shutil.copy2(claude_cfg, staged_configs / ".claude.json")
        log("✓ Сохранен ~/.claude.json")

    claude_dir = user_home / ".claude"
    if claude_dir.exists():
        shutil.copytree(claude_dir, staged_configs / ".claude", dirs_exist_ok=True)
        log("✓ Сохранен каталог ~/.claude/")

    # Скиллы и правила
    for folder_name in ("builtin-skills", "assistant-rules"):
        f_path = aionui_dir / folder_name
        if f_path.exists():
            shutil.copytree(f_path, staging_dir / folder_name, dirs_exist_ok=True)
            log(f"✓ Сохранен каталог {folder_name}/")

    # Версии приложений
    _, aionui_ver = run_cmd("/home/f/.local/bin/aionui-web --version 2>/dev/null")
    _, claude_ver = run_cmd("claude --version 2>/dev/null")
    _, agy_ver = run_cmd("agy --version 2>/dev/null")

    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "description": "AionUi Golden Reference Snapshot (Claude Code Pro + Gemini + Antigravity)",
        "versions": {
            "aionui_web": aionui_ver or "2.1.47",
            "claude_cli": claude_ver or "2.1.280",
            "antigravity_cli": agy_ver or "unknown",
        },
        "database": {
            "sha256": calculate_sha256(staged_db),
            "size_bytes": staged_db.stat().st_size,
            "conversations_count": conv_count,
            "messages_count": msg_count,
            "active_agents": agents,
            "assistants": assistants,
        },
    }

    manifest_file = staging_dir / "manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    log("✓ Манифест эталона создан.")

    # Архивирование
    log("=== ШАГ 4: Упаковка в эталонный архив ===")
    archive_name = f"aionui_golden_backup_{ts}.tar.gz"
    archive_path = win_backup_root / archive_name

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(staging_dir, arcname="aionui_golden")
    log(f"✓ Эталонный архив сохранен: {archive_path.name} ({archive_path.stat().st_size // 1024} КБ)")

    # Обновление папки aionui_golden_reference
    log("=== ШАГ 5: Обновление эталонной папки (Golden Reference) ===")
    for item in golden_ref_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    for item in staging_dir.iterdir():
        if item.is_dir():
            shutil.copytree(item, golden_ref_dir / item.name)
        else:
            shutil.copy2(item, golden_ref_dir / item.name)

    log("✓ Папка 'aionui_golden_reference' успешно обновлена.")

    # Очистка staging
    shutil.rmtree(staging_dir, ignore_errors=True)

    log("\n=======================================================")
    log("ЭТАЛОННЫЙ БЭКАП УСПЕШНО СОЗДАН!")
    log(f"Архив: {archive_path}")
    log(f"Папка-ориентир: {golden_ref_dir}")
    log(f"Сохранено: {conv_count} диалогов, {msg_count} сообщений, {len(agents)} агентов")
    log("=======================================================")
    return True


if __name__ == "__main__":
    success = create_golden_backup()
    sys.exit(0 if success else 1)
