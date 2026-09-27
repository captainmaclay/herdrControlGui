"""Тесты выборочного восстановления (Selective Soft Restore) резервной копии .hbak.

Проверяет:
1. restore_aionui_db=False:
   - AionUi НЕ останавливается (aionui_stop_for_maintenance не вызывается)
   - Живые файлы .db, -wal и -shm не затрагиваются
   - Файлы AionUi в архиве пропускаются
   - Настройки Herdr Center и токены OAuth восстанавливаются штатно
2. restore_herdr_config=False:
   - Файлы win/* не восстанавливаются
   - Автобэкап в настройках не перезаписывается
3. restore_oauth_profiles=False:
   - Файлы wsl/*, claude/win/*, claude/wsl/* не восстанавливаются
4. Все флаги False:
   - Возвращает ошибку валидации без распаковки
5. Интеграция с GUI config_app:
   - Проверка метода _prompt_restore_options
   - Проверка вызова restore_encrypted_backup с выбранными флагами
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import backup_manager as bm

PW = "secret-test-password"


def create_test_db_bytes(tag: str = "RESTORED") -> bytes:
    import sqlite3 as _sq
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_name = f.name
    try:
        conn = _sq.connect(tmp_name)
        conn.execute("CREATE TABLE chats(id INTEGER PRIMARY KEY, msg TEXT)")
        conn.execute("INSERT INTO chats(msg) VALUES (?)", (tag,))
        conn.commit()
        conn.close()
        return Path(tmp_name).read_bytes()
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass


TEST_DB_BYTES = create_test_db_bytes()


def create_hbak_file(path: Path, members: dict[str, bytes], manifest_extra: dict | None = None) -> None:
    buf = io.BytesIO()
    manifest = {"created_at": "2026-09-27T10:00:00Z"}
    if manifest_extra:
        manifest.update(manifest_extra)
    with zipfile_module(buf) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in members.items():
            zf.writestr(name, data)
    salt = os.urandom(bm.SALT_SIZE)
    nonce = os.urandom(bm.NONCE_SIZE)
    ct = AESGCM(bm._derive_key(PW, salt)).encrypt(nonce, buf.getvalue(), associated_data=bm.MAGIC_HEADER)
    path.write_bytes(bm.MAGIC_HEADER + salt + nonce + ct)


def zipfile_module(buf):
    import zipfile
    return zipfile.ZipFile(buf, "w")


class TestSelectiveRestore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

        self.win_dir = self.tmp / "win"
        self.wsl_gemini_dir = self.tmp / "wsl_gemini"
        self.win_claude_dir = self.tmp / "win_claude"
        self.wsl_claude_dir = self.tmp / "wsl_claude"
        self.wsl_aionui_dir = self.tmp / "wsl_aionui"

        for d in (self.win_dir, self.wsl_gemini_dir, self.win_claude_dir, self.wsl_claude_dir, self.wsl_aionui_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Текущие живые файлы
        (self.win_dir / "settings.json").write_bytes(b'{"live": true}')
        (self.wsl_gemini_dir / "gemini_profiles.json").write_bytes(b'{"live_gemini": true}')
        (self.win_claude_dir / "claude_profiles.json").write_bytes(b'{"live_claude": true}')
        (self.wsl_aionui_dir / "aionui-backend.db").write_bytes(b'LIVE-DB-CONTENT')
        (self.wsl_aionui_dir / "aionui-backend.db-wal").write_bytes(b'LIVE-WAL')
        (self.wsl_aionui_dir / "aionui-backend.db-shm").write_bytes(b'LIVE-SHM')

        self.hbak_path = self.tmp / "test_backup.hbak"
        create_hbak_file(self.hbak_path, {
            "win/settings.json": b'{"restored_settings": true}',
            "wsl/gemini_profiles.json": b'{"restored_gemini": true}',
            "claude/win/claude_profiles.json": b'{"restored_claude": true}',
            "aionui/wsl/aionui-backend.db": TEST_DB_BYTES,
            "aionui/wsl/extension-states.json": b'{"ext": true}',
        }, manifest_extra={"auto_backup_enabled": True, "backup_interval_hours": 6})

        self.patches = [
            patch.object(bm, "BASE_DIR", self.win_dir),
            patch.object(bm, "WSL_GEMINI_DIR", self.wsl_gemini_dir),
            patch.object(bm, "WIN_CLAUDE_DIR", self.win_claude_dir),
            patch.object(bm, "WSL_CLAUDE_DIR", self.wsl_claude_dir),
            patch.object(bm, "WSL_AIONUI_DIR", self.wsl_aionui_dir),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def test_soft_restore_skips_aionui_db_completely(self):
        """Мягкий рестор (restore_aionui_db=False): AionUi не останавливается, база и WAL не тронуты."""
        stop_mock = MagicMock()
        end_mock = MagicMock()

        with patch.object(bm, "aionui_stop_for_maintenance", stop_mock), \
             patch.object(bm, "aionui_end_maintenance", end_mock), \
             patch.object(bm, "update_backup_config") as upd_mock:

            ok, msg, manifest = bm.restore_encrypted_backup(
                self.hbak_path, PW,
                restore_herdr_config=True,
                restore_oauth_profiles=True,
                restore_aionui_db=False
            )

        self.assertTrue(ok, msg)
        stop_mock.assert_not_called()
        end_mock.assert_not_called()

        # База AionUi и её журналы остались нетронутыми!
        self.assertEqual((self.wsl_aionui_dir / "aionui-backend.db").read_bytes(), b'LIVE-DB-CONTENT')
        self.assertEqual((self.wsl_aionui_dir / "aionui-backend.db-wal").read_bytes(), b'LIVE-WAL')
        self.assertEqual((self.wsl_aionui_dir / "aionui-backend.db-shm").read_bytes(), b'LIVE-SHM')
        self.assertFalse((self.wsl_aionui_dir / "extension-states.json").exists())

        # Настройки и аккаунты успешно восстановились!
        self.assertEqual((self.win_dir / "settings.json").read_bytes(), b'{"restored_settings": true}')
        self.assertEqual((self.wsl_gemini_dir / "gemini_profiles.json").read_bytes(), b'{"restored_gemini": true}')
        self.assertEqual((self.win_claude_dir / "claude_profiles.json").read_bytes(), b'{"restored_claude": true}')
        self.assertIn("AionUi", msg)

    def test_full_restore_includes_aionui_db(self):
        """Полный рестор (restore_aionui_db=True): безопасная остановка и перезапись базы AionUi."""
        stop_mock = MagicMock(return_value=(True, "stopped"))
        end_mock = MagicMock()

        with patch.object(bm, "aionui_stop_for_maintenance", stop_mock), \
             patch.object(bm, "aionui_end_maintenance", end_mock), \
             patch.object(bm, "update_backup_config"):

            ok, msg, _ = bm.restore_encrypted_backup(
                self.hbak_path, PW,
                restore_herdr_config=True,
                restore_oauth_profiles=True,
                restore_aionui_db=True
            )

        self.assertTrue(ok, msg)
        stop_mock.assert_called_once()
        end_mock.assert_called_once()

        # База перезаписана, старые WAL/SHM удалены
        self.assertEqual((self.wsl_aionui_dir / "aionui-backend.db").read_bytes(), TEST_DB_BYTES)
        self.assertFalse((self.wsl_aionui_dir / "aionui-backend.db-wal").exists())
        self.assertFalse((self.wsl_aionui_dir / "aionui-backend.db-shm").exists())

    def test_restore_only_oauth_profiles(self):
        """Восстановление ТОЛЬКО профилей OAuth (без настроек Herdr и без AionUi)."""
        stop_mock = MagicMock()

        with patch.object(bm, "aionui_stop_for_maintenance", stop_mock), \
             patch.object(bm, "update_backup_config") as upd_mock:

            ok, msg, _ = bm.restore_encrypted_backup(
                self.hbak_path, PW,
                restore_herdr_config=False,
                restore_oauth_profiles=True,
                restore_aionui_db=False
            )

        self.assertTrue(ok, msg)
        stop_mock.assert_not_called()
        upd_mock.assert_not_called()

        # Настройки Herdr НЕ изменились
        self.assertEqual((self.win_dir / "settings.json").read_bytes(), b'{"live": true}')
        # База AionUi НЕ изменилась
        self.assertEqual((self.wsl_aionui_dir / "aionui-backend.db").read_bytes(), b'LIVE-DB-CONTENT')
        # Профили восстановились
        self.assertEqual((self.wsl_gemini_dir / "gemini_profiles.json").read_bytes(), b'{"restored_gemini": true}')
        self.assertEqual((self.win_claude_dir / "claude_profiles.json").read_bytes(), b'{"restored_claude": true}')

    def test_restore_only_herdr_config(self):
        """Восстановление ТОЛЬКО настроек Herdr."""
        with patch.object(bm, "aionui_stop_for_maintenance") as stop_mock, \
             patch.object(bm, "update_backup_config") as upd_mock:

            ok, msg, _ = bm.restore_encrypted_backup(
                self.hbak_path, PW,
                restore_herdr_config=True,
                restore_oauth_profiles=False,
                restore_aionui_db=False
            )

        self.assertTrue(ok, msg)
        stop_mock.assert_not_called()
        self.assertEqual((self.win_dir / "settings.json").read_bytes(), b'{"restored_settings": true}')
        self.assertEqual((self.wsl_gemini_dir / "gemini_profiles.json").read_bytes(), b'{"live_gemini": true}')

    def test_restore_nothing_selected_errors_immediately(self):
        """Если ни один компонент не выбран, возвращается ошибка без распаковки."""
        ok, msg, manifest = bm.restore_encrypted_backup(
            self.hbak_path, PW,
            restore_herdr_config=False,
            restore_oauth_profiles=False,
            restore_aionui_db=False
        )
        self.assertFalse(ok)
        self.assertIn("Не выбран ни один компонент", msg)
        self.assertIsNone(manifest)


class TestRestoreGuiIntegration(unittest.TestCase):
    def setUp(self):
        import config_app
        self.app = config_app.HerdrConfigApp()
        self.app.withdraw()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def test_app_has_restore_options_dialog_method(self):
        """Проверяем наличие метода _prompt_restore_options у приложения."""
        self.assertTrue(hasattr(self.app, "_prompt_restore_options"))
        self.assertTrue(callable(getattr(self.app, "_prompt_restore_options")))

    def test_restore_options_dialog_defaults_and_confirm(self):
        """Проверяем, что по умолчанию Herdr=True, OAuth=True, AionUi=False."""
        def find_buttons(widget):
            buttons = []
            for child in widget.winfo_children():
                if child.winfo_class() == "Button":
                    buttons.append(child)
                buttons.extend(find_buttons(child))
            return buttons

        def mock_wait_window(win):
            buttons = find_buttons(win)
            for btn in buttons:
                txt = btn.cget("text")
                if "Восстановить" in txt or "Restore" in txt:
                    btn.invoke()
                    return
            win.destroy()

        with patch.object(self.app, "wait_window", side_effect=mock_wait_window):
            confirmed, r_herdr, r_oauth, r_aionui = self.app._prompt_restore_options("backup_2026.hbak")

        self.assertTrue(confirmed)
        self.assertTrue(r_herdr)
        self.assertTrue(r_oauth)
        self.assertFalse(r_aionui)  # По умолчанию AionUi DB выключена (Soft Restore!)

    def test_on_restore_specific_backup_passes_user_selection(self):
        """on_restore_specific_backup передает выбранные флаги в restore_encrypted_backup."""
        self.app.backup_password_var.set("testpw")

        # Пользователь выбрал: Herdr=True, OAuth=False, AionUi=False
        with patch.object(self.app, "_prompt_restore_options", return_value=(True, True, False, False)), \
             patch("backup_manager.restore_encrypted_backup", return_value=(True, "OK", {})) as mock_restore:

            self.app.on_restore_specific_backup("path/to/my_backup.hbak")

            # Даем фоновому потоку завершиться
            import time
            time.sleep(0.1)

            mock_restore.assert_called_once_with(
                "path/to/my_backup.hbak", "testpw",
                restore_herdr_config=True,
                restore_oauth_profiles=False,
                restore_aionui_db=False
            )


if __name__ == "__main__":
    unittest.main()

