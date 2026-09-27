"""Восстановление .hbak не должно перезаписывать базу AionUi под работающим процессом.

Инцидент 27.09.2026: база AionUi была заменена под живым aioncore (старые -wal/-shm остались),
после перезапуска aioncore отказывался её открывать
(BOOTSTRAP_DATABASE_CORRUPTION_REQUIRES_USER_CONFIRMATION) и 8 часов уходил в цикл перезапусков.

Запуск: python -m pytest test_backup_restore_aionui.py -q   или   python -m unittest test_backup_restore_aionui -v
Тесты работают только во временной папке и не вызывают WSL.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import backup_manager as bm

PW = "test-password"


def valid_db_bytes(tag: str = "RESTORED") -> bytes:
    import sqlite3 as _sq
    d = tempfile.mkdtemp()
    p = Path(d) / "x.db"
    c = _sq.connect(str(p))
    c.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT)")
    c.execute("INSERT INTO messages(content) VALUES (?)", (tag,))
    c.commit()
    c.close()
    return p.read_bytes()


RESTORED_DB = valid_db_bytes()


def make_hbak(path: Path, members: dict[str, bytes]) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"created_at": "test"}))
        for name, data in members.items():
            zf.writestr(name, data)
    salt = os.urandom(bm.SALT_SIZE)
    nonce = os.urandom(bm.NONCE_SIZE)
    ct = AESGCM(bm._derive_key(PW, salt)).encrypt(nonce, buf.getvalue(), associated_data=bm.MAGIC_HEADER)
    path.write_bytes(bm.MAGIC_HEADER + salt + nonce + ct)


def fake_write(p: Path, c: bytes) -> bool:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(c)
    return True


class TestRestoreAionuiDb(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.aion = self.tmp / "aionui"
        self.aion.mkdir()
        (self.aion / "aionui-backend.db").write_bytes(b"LIVE-DB")
        (self.aion / "aionui-backend.db-wal").write_bytes(b"OLD-WAL")
        (self.aion / "aionui-backend.db-shm").write_bytes(b"OLD-SHM")
        self.hbak = self.tmp / "b.hbak"
        make_hbak(self.hbak, {
            "aionui/wsl/aionui-backend.db": RESTORED_DB,
            "aionui/wsl/extension-states.json": b"{}",
        })
        self.patches = [
            patch.object(bm, "WSL_AIONUI_DIR", self.aion),
            patch.object(bm, "update_backup_config", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def test_stops_aionui_writes_db_drops_old_wal_and_ends_maintenance(self):
        calls = []

        def write(p, c):
            calls.append(("write", p.name))
            return fake_write(p, c)

        with patch.object(bm, "aionui_stop_for_maintenance", lambda *a, **k: (calls.append("stop"), (True, ""))[1]), \
             patch.object(bm, "aionui_end_maintenance", lambda: (calls.append("end"), (True, ""))[1]), \
             patch.object(bm, "_safe_write_file", side_effect=write):
            ok, msg, _ = bm.restore_encrypted_backup(self.hbak, PW)
        self.assertTrue(ok, msg)
        self.assertEqual(calls[0], "stop")                        # остановка ДО записи базы
        self.assertIn(("write", "aionui-backend.db"), calls)
        self.assertEqual(calls[-1], "end")                        # флаг снят после записи
        self.assertEqual((self.aion / "aionui-backend.db").read_bytes(), RESTORED_DB)
        self.assertFalse((self.aion / "aionui-backend.db-wal").exists())
        self.assertFalse((self.aion / "aionui-backend.db-shm").exists())
        self.assertIn("aiWatcher", msg)

    def test_db_not_restored_if_aionui_cannot_be_stopped(self):
        ended = []
        with patch.object(bm, "aionui_stop_for_maintenance", lambda *a, **k: (False, "watcher restarted it")), \
             patch.object(bm, "aionui_end_maintenance", lambda: (ended.append(1), (True, ""))[1]), \
             patch.object(bm, "_safe_write_file", side_effect=fake_write):
            ok, msg, _ = bm.restore_encrypted_backup(self.hbak, PW)
        self.assertTrue(ok)
        self.assertEqual((self.aion / "aionui-backend.db").read_bytes(), b"LIVE-DB")      # живая база не тронута
        self.assertEqual((self.aion / "aionui-backend.db-wal").read_bytes(), b"OLD-WAL")
        self.assertEqual((self.aion / "extension-states.json").read_bytes(), b"{}")      # остальное восстановлено
        self.assertIn("НЕ восстановлена", msg)
        self.assertEqual(ended, [1])                                                     # флаг не оставлен висеть

    def test_backup_without_aionui_db_does_not_touch_aionui(self):
        make_hbak(self.hbak, {"aionui/wsl/extension-states.json": b"{}"})
        with patch.object(bm, "aionui_stop_for_maintenance") as stop, \
             patch.object(bm, "aionui_end_maintenance") as end, \
             patch.object(bm, "_safe_write_file", side_effect=fake_write):
            ok, _, _ = bm.restore_encrypted_backup(self.hbak, PW)
        self.assertTrue(ok)
        stop.assert_not_called()
        end.assert_not_called()
        self.assertTrue((self.aion / "aionui-backend.db-wal").exists())

    def test_stop_command_uses_maintenance_helpers(self):
        with patch.object(bm.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "ok"
            run.return_value.stderr = ""
            ok, _ = bm.aionui_stop_for_maintenance("x")
        args = run.call_args.args[0]
        self.assertTrue(ok)
        self.assertIn("python3", args)
        code = args[-1]
        self.assertIn("aionui_maint", code)
        self.assertIn("maint_flag", code)
        self.assertIn("stop_aionui", code)
        if os.name == "nt":
            self.assertEqual(args[0], "wsl.exe")

    def test_stop_failure_on_exception(self):
        with patch.object(bm.subprocess, "run", side_effect=FileNotFoundError("wsl.exe")):
            ok, out = bm.aionui_stop_for_maintenance()
        self.assertFalse(ok)
        self.assertIn("wsl.exe", out)


if __name__ == "__main__":
    unittest.main()


# ─────────────────────────── Согласованный снимок при создании бэкапа ───────────────────────────

import sqlite3
import threading
import time as _time


def make_live_wal_db(path: Path, rows: int) -> sqlite3.Connection:
    """База в WAL-режиме с незафиксированными в основном файле изменениями (как у работающего AionUi)."""
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")          # всё остаётся в -wal
    c.execute("CREATE TABLE conversations(id TEXT PRIMARY KEY)")
    c.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT, content TEXT)")
    c.execute("INSERT INTO conversations VALUES ('c1')")
    c.executemany("INSERT INTO messages(conversation_id, content) VALUES ('c1', ?)", [(f"m{i}" * 50,) for i in range(rows)])
    c.commit()
    return c


def count_messages(db_bytes: bytes) -> int:
    ok, info = bm.validate_sqlite_bytes(db_bytes)
    return info.get("counts", {}).get("messages", -1) if ok else -1


class SnapshotCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.aion = Path(self._tmp.name) / ".aionui-web"
        self.aion.mkdir()
        self.db = self.aion / "aionui-backend.db"
        self.patches = [
            patch.object(bm, "WSL_AIONUI_DIR", self.aion),
            patch.object(bm, "AIONUI_LINUX_DIR", str(self.aion)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()


@unittest.skipIf(os.name == "nt", "снимок через python3 в WSL; на Windows тест вызвал бы wsl.exe")
class TestSnapshot(SnapshotCase):
    def test_raw_copy_misses_wal_but_snapshot_has_everything(self):
        live = make_live_wal_db(self.db, 500)
        try:
            self.assertTrue((self.aion / "aionui-backend.db-wal").exists())
            raw = self.db.read_bytes()                       # так делал старый бэкап
            self.assertNotEqual(count_messages(raw), 500)     # данные из -wal потеряны
            snap, info = bm.snapshot_aionui_db()
            self.assertIsNotNone(snap, info)
            self.assertEqual(info["integrity"], "ok")
            self.assertEqual(info["counts"], {"conversations": 1, "messages": 500})
            self.assertEqual(info["method"], "sqlite_backup_api")
            self.assertEqual(count_messages(snap), 500)
            self.assertFalse((self.aion / "db_backups" / bm.AIONUI_SNAPSHOT_NAME).exists())   # временный файл убран
        finally:
            live.close()

    def test_snapshot_while_writer_is_active(self):
        live = make_live_wal_db(self.db, 10)
        stop = threading.Event()

        def writer():
            i = 0
            while not stop.is_set():
                live.execute("INSERT INTO messages(conversation_id, content) VALUES ('c1', ?)", (f"w{i}",))
                live.commit()
                i += 1
                _time.sleep(0.001)
        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            for _ in range(3):
                snap, info = bm.snapshot_aionui_db()
                self.assertIsNotNone(snap, info)
                self.assertEqual(info["integrity"], "ok")
                self.assertGreaterEqual(count_messages(snap), 10)
        finally:
            stop.set()
            t.join(2)
            live.close()

    def test_corrupt_db_gives_no_snapshot(self):
        live = make_live_wal_db(self.db, 200)
        live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        live.close()
        data = bytearray(self.db.read_bytes())
        data[4096:4096 * 3] = b"\x00\xff" * 4096          # портим страницы 2–3 (схема/таблицы)
        self.db.write_bytes(bytes(data))
        snap, info = bm.snapshot_aionui_db()
        self.assertIsNone(snap)
        self.assertNotEqual(info["integrity"], "ok")

    def test_missing_db(self):
        snap, info = bm.snapshot_aionui_db()
        self.assertIsNone(snap)
        self.assertEqual(info["integrity"], "error")
        self.assertIn("нет файла", info["error"])


class TestValidateAndLocalSnapshot(SnapshotCase):
    def test_validate_good_and_bad_bytes(self):
        live = make_live_wal_db(self.db, 5)
        live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        live.close()
        ok, info = bm.validate_sqlite_bytes(self.db.read_bytes())
        self.assertTrue(ok)
        self.assertEqual(info["counts"]["messages"], 5)
        ok, info = bm.validate_sqlite_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
        self.assertFalse(ok)
        ok, _ = bm.validate_sqlite_bytes(b"not a database at all")
        self.assertFalse(ok)

    def test_local_snapshot_includes_wal(self):
        live = make_live_wal_db(self.db, 300)
        try:
            snap, info = bm.snapshot_local_sqlite(self.db)
            self.assertEqual(info["integrity"], "ok")
            self.assertEqual(count_messages(snap), 300)
        finally:
            live.close()


class TestCreateBackupUsesSnapshot(unittest.TestCase):
    """create_encrypted_backup: в архиве снимок, а не живой файл; результат проверки — в манифесте."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.out = root / "out"
        self.aion = root / "wsl_aionui"
        self.aion.mkdir()
        (self.aion / "extension-states.json").write_text("{}")
        empty = root / "empty"
        self.patches = [
            patch.object(bm, "BASE_DIR", empty),
            patch.object(bm, "WIN_CLAUDE_DIR", empty),
            patch.object(bm, "WSL_CLAUDE_DIR", empty),
            patch.object(bm, "WSL_GEMINI_DIR", empty),
            patch.object(bm, "WIN_AIONUI_DIR", empty),
            patch.object(bm, "WSL_AIONUI_DIR", self.aion),
            patch.object(bm, "update_backup_config", lambda *a, **k: None),
            patch.object(bm, "get_backup_config", lambda: {}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def _open(self, path: str) -> zipfile.ZipFile:
        raw = Path(path).read_bytes()
        h = len(bm.MAGIC_HEADER)
        salt, nonce, ct = raw[h:h + bm.SALT_SIZE], raw[h + bm.SALT_SIZE:h + bm.SALT_SIZE + bm.NONCE_SIZE], raw[h + bm.SALT_SIZE + bm.NONCE_SIZE:]
        data = AESGCM(bm._derive_key(PW, salt)).decrypt(nonce, ct, associated_data=bm.MAGIC_HEADER)
        return zipfile.ZipFile(io.BytesIO(data))

    def test_good_snapshot_goes_into_archive(self):
        snap = b"SNAPSHOT-BYTES"
        info = {"method": "sqlite_backup_api", "integrity": "ok", "counts": {"conversations": 2, "messages": 7}}
        with patch.object(bm, "snapshot_aionui_db", return_value=(snap, info)):
            ok, msg, path = bm.create_encrypted_backup(PW, self.out)
        self.assertTrue(ok, msg)
        zf = self._open(path)
        self.assertEqual(zf.read("aionui/wsl/aionui-backend.db"), snap)
        self.assertIn("aionui/wsl/extension-states.json", zf.namelist())
        man = json.loads(zf.read("manifest.json"))
        self.assertEqual(man["aionui_db"]["integrity"], "ok")
        self.assertEqual(man["aionui_db"]["counts"]["messages"], 7)
        self.assertIn("integrity_check = ok", msg)

    def test_bad_snapshot_is_not_packed_and_user_is_warned(self):
        info = {"method": "sqlite_backup_api", "integrity": "error", "error": "DatabaseError: malformed"}
        with patch.object(bm, "snapshot_aionui_db", return_value=(None, info)):
            ok, msg, path = bm.create_encrypted_backup(PW, self.out)
        self.assertTrue(ok)
        zf = self._open(path)
        self.assertNotIn("aionui/wsl/aionui-backend.db", zf.namelist())
        self.assertEqual(json.loads(zf.read("manifest.json"))["aionui_db"]["integrity"], "error")
        self.assertIn("НЕ включена", msg)

    def test_live_file_is_never_read_directly(self):
        (self.aion / "aionui-backend.db").write_bytes(b"LIVE-FILE-MUST-NOT-BE-PACKED")
        with patch.object(bm, "snapshot_aionui_db", return_value=(None, {"integrity": "error", "error": "x"})):
            ok, _, path = bm.create_encrypted_backup(PW, self.out)
        zf = self._open(path)
        for n in zf.namelist():
            self.assertNotIn(b"LIVE-FILE-MUST-NOT-BE-PACKED", zf.read(n))


class TestRestoreRejectsCorruptCopy(unittest.TestCase):
    def test_corrupt_db_in_archive_is_not_restored_and_aionui_not_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            aion = Path(d) / "aionui"
            aion.mkdir()
            (aion / "aionui-backend.db").write_bytes(b"LIVE-DB")
            hbak = Path(d) / "b.hbak"
            make_hbak(hbak, {"aionui/wsl/aionui-backend.db": b"SQLite format 3\x00" + b"\x00" * 100})
            with patch.object(bm, "WSL_AIONUI_DIR", aion), \
                 patch.object(bm, "update_backup_config", lambda *a, **k: None), \
                 patch.object(bm, "aionui_stop_for_maintenance") as stop, \
                 patch.object(bm, "_safe_write_file", side_effect=fake_write):
                ok, msg, _ = bm.restore_encrypted_backup(hbak, PW)
            self.assertTrue(ok)
            stop.assert_not_called()
            self.assertEqual((aion / "aionui-backend.db").read_bytes(), b"LIVE-DB")
            self.assertIn("копия повреждена", msg)
