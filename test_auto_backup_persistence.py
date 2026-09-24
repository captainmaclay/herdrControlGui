#!/usr/bin/env python3
"""Тестирование флага auto_backup_enabled:
1. По умолчанию выключен (False).
2. Сохранение положения при изменении и перезапуске.
3. Экспорт флага в зашифрованный бэкап (.hbak).
4. Восстановление флага при импорте из бэкапа.
5. check_and_run_auto_backup() не запускается при выключенном флаге.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import backup_manager
import settings_manager


class TestAutoBackupPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="herdr_test_autobackup_"))
        self.orig_settings_file = backup_manager.SETTINGS_FILE
        self.orig_backup_dir = backup_manager.DEFAULT_BACKUP_DIR

        # Перенаправляем settings.json во временную папку
        self.test_settings_file = self.tmp_dir / "settings.json"
        backup_manager.SETTINGS_FILE = self.test_settings_file
        settings_manager.SETTINGS_FILE = self.test_settings_file
        self.password = "TestPass123!Secure"

    def tearDown(self):
        backup_manager.SETTINGS_FILE = self.orig_settings_file
        settings_manager.SETTINGS_FILE = self.orig_settings_file
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_is_disabled(self):
        """Проверка: если settings.json не существует или пуст, автобэкап по умолчанию ВЫКЛЮЧЕН."""
        if self.test_settings_file.exists():
            self.test_settings_file.unlink()

        cfg = backup_manager.get_backup_config()
        self.assertFalse(cfg.get("auto_backup_enabled"), "По умолчанию auto_backup_enabled должен быть False")

        # В settings_manager.DEFAULT_SETTINGS также должен быть False
        self.assertFalse(settings_manager.DEFAULT_SETTINGS.get("auto_backup_enabled"), "DEFAULT_SETTINGS auto_backup_enabled должен быть False")

    def test_persistence_across_restarts(self):
        """Проверка: сохранение состояния при включении/выключении и имитации перезапуска."""
        # 1. Включаем
        backup_manager.update_backup_config("auto_backup_enabled", True)
        cfg1 = backup_manager.get_backup_config()
        self.assertTrue(cfg1["auto_backup_enabled"], "Флаг должен стать True после включения")

        # Проверяем прямое чтение файла
        with open(self.test_settings_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        self.assertTrue(raw_data.get("auto_backup_enabled"), "В settings.json должно быть записано True")

        # 2. Выключаем
        backup_manager.update_backup_config("auto_backup_enabled", False)
        cfg2 = backup_manager.get_backup_config()
        self.assertFalse(cfg2["auto_backup_enabled"], "Флаг должен стать False после выключения")

        # Проверяем прямое чтение файла
        with open(self.test_settings_file, "r", encoding="utf-8") as f:
            raw_data2 = json.load(f)
        self.assertFalse(raw_data2.get("auto_backup_enabled"), "В settings.json должно быть записано False")

    def test_check_and_run_auto_backup_respects_flag(self):
        """Проверка: check_and_run_auto_backup() отказывается работать при auto_backup_enabled=False."""
        backup_manager.update_backup_config("auto_backup_enabled", False)
        ok, msg = backup_manager.check_and_run_auto_backup()
        self.assertFalse(ok)
        self.assertEqual(msg, "Автобэкап выключен")

    def test_backup_export_and_import_preserves_enabled_flag(self):
        """Проверка: экспорт с True сохраняет флаг, импорт восстанавливает True."""
        # 1. Включаем автобэкап
        backup_manager.update_backup_config("auto_backup_enabled", True)
        backup_manager.update_backup_config("backup_interval_hours", 6)

        # 2. Создаем бэкап
        ok, msg, backup_path = backup_manager.create_encrypted_backup(self.password, self.tmp_dir)
        self.assertTrue(ok, f"Создание бэкапа не удалось: {msg}")
        self.assertIsNotNone(backup_path)

        # 3. Меняем текущую настройку на False (имитируем выключенный бэкап в системе)
        backup_manager.update_backup_config("auto_backup_enabled", False)
        backup_manager.update_backup_config("backup_interval_hours", 24)
        self.assertFalse(backup_manager.get_backup_config()["auto_backup_enabled"])

        # 4. Восстанавливаем бэкап
        ok_res, msg_res, manifest = backup_manager.restore_encrypted_backup(backup_path, self.password)
        self.assertTrue(ok_res, f"Восстановление бэкапа не удалось: {msg_res}")
        self.assertIsNotNone(manifest)
        self.assertTrue(manifest.get("auto_backup_enabled"), "Манифест должен содержать auto_backup_enabled=True")

        # 5. Проверяем, что в восстановленных настройках флаг стал True
        restored_cfg = backup_manager.get_backup_config()
        self.assertTrue(restored_cfg["auto_backup_enabled"], "После восстановления бэкапа флаг должен стать True!")
        self.assertEqual(restored_cfg["backup_interval_hours"], 6, "Интервал также должен восстановиться (6)")

    def test_backup_export_and_import_preserves_disabled_flag(self):
        """Проверка: экспорт с False сохраняет флаг, импорт восстанавливает False."""
        # 1. Выключаем автобэкап
        backup_manager.update_backup_config("auto_backup_enabled", False)
        backup_manager.update_backup_config("backup_interval_hours", 8)

        # 2. Создаем бэкап
        ok, msg, backup_path = backup_manager.create_encrypted_backup(self.password, self.tmp_dir)
        self.assertTrue(ok, f"Создание бэкапа не удалось: {msg}")

        # 3. Меняем текущую настройку на True
        backup_manager.update_backup_config("auto_backup_enabled", True)
        self.assertTrue(backup_manager.get_backup_config()["auto_backup_enabled"])

        # 4. Восстанавливаем бэкап
        ok_res, msg_res, manifest = backup_manager.restore_encrypted_backup(backup_path, self.password)
        self.assertTrue(ok_res)
        self.assertFalse(manifest.get("auto_backup_enabled"), "Манифест должен содержать auto_backup_enabled=False")

        # 5. Проверяем, что в восстановленных настройках флаг стал False
        restored_cfg = backup_manager.get_backup_config()
        self.assertFalse(restored_cfg["auto_backup_enabled"], "После восстановления бэкапа флаг должен стать False!")
        self.assertEqual(restored_cfg["backup_interval_hours"], 8)


if __name__ == "__main__":
    unittest.main()
