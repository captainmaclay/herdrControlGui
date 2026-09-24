"""Тесты для флага Claude в SOCKS5 прокси (по умолчанию включен для 1015, выключен для остальных).

Проверяет:
1. Значения флага Claude по умолчанию:
   - 1015 (System Proxy) -> True
   - Любые другие порты (1081, 1082, 1085 и т.д.) -> False
2. Инициализацию при отсутствии флага в существующих proxies.json.
3. Добавление нового прокси через add_proxy.
4. Синхронизацию из instances.json (vless2socks).
5. Переключение флага через set_proxy_claude_flag и сохранение после перезапуска.
6. Возможность выключить флаг для 1015 с сохранением после перезапуска (не перетирается).
7. Полный цикл резервного копирования (Backup) и восстановления (Restore):
   - Экспорт в зашифрованный .hbak
   - Восстановление и проверка сохранности флагов claude.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import backup_manager
import proxy_manager
import settings_manager


class TestClaudeProxyFlag(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_settings_file = settings_manager.SETTINGS_FILE
        self.orig_proxies_file = proxy_manager.PROXIES_FILE
        self.orig_base_dir = backup_manager.BASE_DIR
        self.orig_env_file = backup_manager.ENV_FILE

        self.test_settings_file = Path(self.temp_dir) / "settings.json"
        self.test_proxies_file = Path(self.temp_dir) / "proxies.json"
        self.test_backup_dir = Path(self.temp_dir) / "backups"
        self.test_backup_dir.mkdir(parents=True, exist_ok=True)
        self.test_vless_instances = Path(self.temp_dir) / "instances.json"

        settings_manager.SETTINGS_FILE = self.test_settings_file
        proxy_manager.PROXIES_FILE = self.test_proxies_file
        backup_manager.BASE_DIR = Path(self.temp_dir)
        backup_manager.SETTINGS_FILE = self.test_settings_file
        backup_manager.ENV_FILE = Path(self.temp_dir) / ".env"
        proxy_manager.VLESS2SOCKS_INSTANCES_FILE = self.test_vless_instances

        # Начальные настройки
        settings_manager.save_settings({
            "language": "ru",
            "claude_proxy_host": "127.0.0.1",
            "claude_proxy_port": 1015,
            "claude_killswitch": True,
        })

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig_settings_file
        proxy_manager.PROXIES_FILE = self.orig_proxies_file
        backup_manager.BASE_DIR = self.orig_base_dir
        backup_manager.SETTINGS_FILE = self.orig_settings_file
        backup_manager.ENV_FILE = self.orig_env_file
        proxy_manager.VLESS2SOCKS_INSTANCES_FILE = None
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_default_fallback_proxies_have_claude_flag(self):
        """Проверяет fallback прокси при отсутствии proxies.json: 1015 True, остальные False."""
        if self.test_proxies_file.exists():
            self.test_proxies_file.unlink()

        proxies = proxy_manager.load_proxies(refresh_status=False)
        self.assertGreaterEqual(len(proxies), 2)

        p1015 = next((p for p in proxies if p.get("port") == 1015), None)
        self.assertIsNotNone(p1015)
        self.assertTrue(p1015.get("claude"))

        for p in proxies:
            if p.get("port") != 1015:
                self.assertFalse(p.get("claude", False))

    def test_load_proxies_backfills_missing_claude_flag(self):
        """Если в proxies.json у записей отсутствует поле 'claude', оно должно автоматически заполниться."""
        raw_data = [
            {"id": 1, "host": "127.0.0.1", "port": 1015, "label": "System Proxy"},
            {"id": 2, "host": "127.0.0.1", "port": 1081, "label": "Finland"},
            {"id": 3, "host": "127.0.0.1", "port": 1085, "label": "Claude"},
        ]
        with open(self.test_proxies_file, "w", encoding="utf-8") as f:
            json.dump(raw_data, f)

        proxies = proxy_manager.load_proxies(refresh_status=False)
        p1015 = next(p for p in proxies if p["port"] == 1015)
        p1081 = next(p for p in proxies if p["port"] == 1081)
        p1085 = next(p for p in proxies if p["port"] == 1085)

        self.assertTrue(p1015.get("claude"))
        self.assertFalse(p1081.get("claude"))
        self.assertFalse(p1085.get("claude"))

    def test_add_proxy_default_claude_flag(self):
        """Проверяет, что add_proxy устанавливает claude=True только для 1015, а для других — False."""
        if self.test_proxies_file.exists():
            self.test_proxies_file.unlink()

        p_new_1 = proxy_manager.add_proxy(host="127.0.0.1", port=1089, label="New Proxy")
        self.assertFalse(p_new_1.get("claude"))

        p_new_1015 = proxy_manager.add_proxy(host="127.0.0.1", port=1015, label="System Proxy 1015")
        self.assertTrue(p_new_1015.get("claude"))

    def test_sync_from_vless2socks_claude_flags(self):
        """Проверяет установку claude флагов при синхронизации из instances.json."""
        instances_data = [
            {"name": "System Proxy", "order": 0, "url": "", "listen": "127.0.0.1:1015"},
            {"name": "France", "order": 1, "url": "vless://test", "listen": "127.0.0.1:1083"},
        ]
        with open(self.test_vless_instances, "w", encoding="utf-8") as f:
            json.dump(instances_data, f)

        synced = proxy_manager.sync_from_vless2socks(self.test_vless_instances)
        p1015 = next(p for p in synced if p["port"] == 1015)
        p1083 = next(p for p in synced if p["port"] == 1083)

        self.assertTrue(p1015.get("claude"))
        self.assertFalse(p1083.get("claude"))

    def test_toggle_proxy_claude_flag_and_persistence(self):
        """Проверяет переключение флага и его сохранение после перезапуска (load_proxies)."""
        # Инициализируем прокси
        proxy_manager.save_proxies([
            {"id": 1, "host": "127.0.0.1", "port": 1015, "claude": True},
            {"id": 2, "host": "127.0.0.1", "port": 1082, "claude": False},
        ])

        # 1. Включаем claude для 1082
        success = proxy_manager.set_proxy_claude_flag(1082, True)
        self.assertTrue(success)
        self.assertTrue(proxy_manager.get_proxy_claude_flag(1082))

        # Перезапуск (заново считываем из файла)
        reloaded = proxy_manager.load_proxies(refresh_status=False)
        p1082 = next(p for p in reloaded if p["port"] == 1082)
        self.assertTrue(p1082.get("claude"))

        # 2. Выключаем claude для 1015 (пользователь снял флаг)
        success_1015 = proxy_manager.set_proxy_claude_flag(1015, False)
        self.assertTrue(success_1015)
        self.assertFalse(proxy_manager.get_proxy_claude_flag(1015))

        # Перезапуск — значение False для 1015 должно сохраниться и НЕ перетираться дефолтным True
        reloaded_2 = proxy_manager.load_proxies(refresh_status=False)
        p1015 = next(p for p in reloaded_2 if p["port"] == 1015)
        self.assertFalse(p1015.get("claude"))

    def test_backup_export_and_restore_preserves_claude_flags(self):
        """Проверяет экспорт и восстановление флагов Claude через бэкап .hbak."""
        test_proxies = [
            {"id": 1, "host": "127.0.0.1", "port": 1015, "label": "System Proxy", "claude": False}, # выключен юзером
            {"id": 2, "host": "127.0.0.1", "port": 1081, "label": "Proxy 1081", "claude": False},
            {"id": 3, "host": "127.0.0.1", "port": 1082, "label": "Proxy 1082", "claude": True},  # включен юзером
        ]
        proxy_manager.save_proxies(test_proxies)

        password = "SecureTestPassword123!"
        ok, msg, backup_path = backup_manager.create_encrypted_backup(password, target_dir=self.test_backup_dir)
        self.assertTrue(ok, f"Бэкап не создался: {msg}")
        self.assertIsNotNone(backup_path)
        self.assertTrue(Path(backup_path).exists())

        # Искажаем файл proxies.json или удаляем
        proxy_manager.save_proxies([
            {"id": 1, "host": "127.0.0.1", "port": 1015, "claude": True},
            {"id": 2, "host": "127.0.0.1", "port": 1081, "claude": True},
            {"id": 3, "host": "127.0.0.1", "port": 1082, "claude": False},
        ])

        # Восстанавливаем из бэкапа
        rest_ok, rest_msg, _manifest = backup_manager.restore_encrypted_backup(backup_path, password)
        self.assertTrue(rest_ok, f"Восстановление не удалось: {rest_msg}")

        # Проверяем, что восстановились исходные флаги
        restored_proxies = proxy_manager.load_proxies(refresh_status=False)
        r1015 = next(p for p in restored_proxies if p["port"] == 1015)
        r1081 = next(p for p in restored_proxies if p["port"] == 1081)
        r1082 = next(p for p in restored_proxies if p["port"] == 1082)

        self.assertFalse(r1015.get("claude"), "1015 должен остаться False как в бэкапе")
        self.assertFalse(r1081.get("claude"), "1081 должен остаться False как в бэкапе")
        self.assertTrue(r1082.get("claude"), "1082 должен остаться True как в бэкапе")


if __name__ == "__main__":
    unittest.main()
