"""Автоматические тесты для конфигурации подключения Claude Code и Killswitch.

Проверяет:
1. Значения по умолчанию: host=127.0.0.1, port=1085, killswitch=True.
2. Сохранение и загрузку настроек в settings.json.
3. Включение Killswitch и запись blackhole (127.0.0.1:1) при неактивном прокси.
4. Автоматическое снятие Killswitch и восстановление туннеля при активном прокси.
5. Экспорт и импорт настроек Claude и Killswitch в зашифрованных бэкапах (.hbak).
6. Изоляцию Claude от переключения Gemini OAuth.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import settings_manager
import claude_manager
import proxy_manager
import backup_manager
import gemini_manager


class TestClaudeConnectionAndKillswitch(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_settings_file = settings_manager.SETTINGS_FILE
        self.orig_proxies_file = proxy_manager.PROXIES_FILE
        self.orig_base_dir = backup_manager.BASE_DIR

        self.test_settings_file = Path(self.temp_dir) / "settings.json"
        self.test_proxies_file = Path(self.temp_dir) / "proxies.json"
        self.test_env_file = Path(self.temp_dir) / ".env"
        self.test_claude_file = Path(self.temp_dir) / "claude_settings.json"

        settings_manager.SETTINGS_FILE = self.test_settings_file
        proxy_manager.PROXIES_FILE = self.test_proxies_file
        backup_manager.SETTINGS_FILE = self.test_settings_file
        backup_manager.BASE_DIR = Path(self.temp_dir)
        backup_manager.ENV_FILE = self.test_env_file

        # Инициализируем пустые / дефолтные настройки
        settings_manager.save_settings(settings_manager.DEFAULT_SETTINGS)

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig_settings_file
        proxy_manager.PROXIES_FILE = self.orig_proxies_file
        backup_manager.SETTINGS_FILE = self.orig_settings_file
        backup_manager.BASE_DIR = self.orig_base_dir
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_default_settings(self):
        """Проверяет значения по умолчанию для подключения Claude."""
        host = settings_manager.get_claude_proxy_host()
        port = settings_manager.get_claude_proxy_port()
        ks = settings_manager.get_claude_killswitch()

        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 1015)
        self.assertTrue(ks)

    def test_modify_and_persist_settings(self):
        """Проверяет изменение и сохранение настроек Claude в settings.json."""
        settings_manager.set_claude_proxy_settings("192.168.1.100", 9050, False)

        self.assertEqual(settings_manager.get_claude_proxy_host(), "192.168.1.100")
        self.assertEqual(settings_manager.get_claude_proxy_port(), 9050)
        self.assertFalse(settings_manager.get_claude_killswitch())

        # Читаем напрямую из файла
        with open(self.test_settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["claude_proxy_host"], "192.168.1.100")
        self.assertEqual(data["claude_proxy_port"], 9050)
        self.assertFalse(data["claude_killswitch"])

    def test_apply_claude_settings_active(self):
        """Проверяет корректность записи рабочего прокси в claude settings.json."""
        initial_data = {
            "model": "opus",
            "theme": "dark",
            "custom_key": 123
        }
        with open(self.test_claude_file, "w", encoding="utf-8") as f:
            json.dump(initial_data, f)

        ok = claude_manager.apply_claude_settings_to_file(
            self.test_claude_file,
            host="127.0.0.1",
            port=1085,
            killswitch_engaged=False,
        )
        self.assertTrue(ok)

        with open(self.test_claude_file, "r", encoding="utf-8") as f:
            saved = json.load(f)

        # Проверяем сохранение пользовательских настроек
        self.assertEqual(saved.get("model"), "opus")
        self.assertEqual(saved.get("theme"), "dark")
        self.assertEqual(saved.get("custom_key"), 123)

        # Проверяем параметры туннеля
        env = saved.get("env", {})
        self.assertEqual(env.get("HTTPS_PROXY"), "http://127.0.0.1:11085")
        self.assertEqual(env.get("HTTP_PROXY"), "http://127.0.0.1:11085")
        self.assertEqual(env.get("ALL_PROXY"), "socks5h://127.0.0.1:1085")

    def test_apply_claude_settings_killswitch_engaged(self):
        """Проверяет срабатывание Killswitch (запись blackhole 127.0.0.1:1)."""
        initial_data = {"model": "sonnet"}
        with open(self.test_claude_file, "w", encoding="utf-8") as f:
            json.dump(initial_data, f)

        ok = claude_manager.apply_claude_settings_to_file(
            self.test_claude_file,
            host="127.0.0.1",
            port=1085,
            killswitch_engaged=True,
        )
        self.assertTrue(ok)

        with open(self.test_claude_file, "r", encoding="utf-8") as f:
            saved = json.load(f)

        env = saved.get("env", {})
        # Все запросы заворачиваются на blackhole для предотвращения утечки оригинального IP
        self.assertEqual(env.get("HTTPS_PROXY"), "http://127.0.0.1:1")
        self.assertEqual(env.get("HTTP_PROXY"), "http://127.0.0.1:1")
        self.assertEqual(env.get("ALL_PROXY"), "socks5h://127.0.0.1:1")

    @patch("claude_manager.get_claude_settings_files")
    @patch("claude_manager.check_port_accessible", return_value=False)
    def test_killswitch_triggers_when_proxy_offline_in_list(self, mock_check_port, mock_get_files):
        """Если выбранный прокси в списке имеет статус 'offline', Killswitch срабатывает."""
        mock_get_files.return_value = [self.test_claude_file]

        # Создаем список прокси со статусом offline для 1085
        proxy_manager.save_proxies([
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1085,
                "status": "offline",
                "ip": "-",
                "country": "undefined",
            }
        ])

        settings_manager.set_claude_proxy_settings("127.0.0.1", 1085, killswitch=True)

        res = claude_manager.probe_claude_route()

        self.assertFalse(res["online"])
        self.assertTrue(res["killswitch_engaged"])
        self.assertTrue(res["killswitch_enabled"])
        self.assertIn("KILLSWITCH", res["status_text"])

        # Проверяем, что в файл Claude записан blackhole
        with open(self.test_claude_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["env"]["HTTPS_PROXY"], "http://127.0.0.1:1")

    @patch("claude_manager.get_claude_settings_files")
    @patch("claude_manager.check_port_accessible")
    @patch("requests.get")
    def test_killswitch_disengages_when_proxy_becomes_active(
        self, mock_requests_get, mock_check_port, mock_get_files
    ):
        """Когда прокси становится активным в списке, Killswitch отключается."""
        mock_get_files.return_value = [self.test_claude_file]
        mock_check_port.return_value = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        # Прокси в списке online
        proxy_manager.save_proxies([
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1085,
                "status": "online",
                "ip": "85.200.10.5",
                "country": "Netherlands",
                "latency_ms": 120,
            }
        ])

        settings_manager.set_claude_proxy_settings("127.0.0.1", 1085, killswitch=True)

        res = claude_manager.probe_claude_route()

        self.assertTrue(res["online"])
        self.assertFalse(res["killswitch_engaged"])
        self.assertEqual(res["ip"], "85.200.10.5")
        self.assertEqual(res["country"], "Netherlands")
        self.assertIn("активен", res["status_text"])

        # Проверяем, что файл Claude обновлен на рабочий прокси
        with open(self.test_claude_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["env"]["HTTPS_PROXY"], "http://127.0.0.1:11085")
        self.assertEqual(saved["env"]["ALL_PROXY"], "socks5h://127.0.0.1:1085")

    def test_backup_export_and_import_preserves_claude_settings(self):
        """Проверяет экспорт и импорт настроек Claude и флага Killswitch в бэкапах."""
        backup_dir = Path(self.temp_dir) / "backups"
        backup_dir.mkdir()

        # 1. Сохраняем пользовательские настройки
        settings_manager.set_claude_proxy_settings("10.0.0.5", 8888, killswitch=True)

        # 2. Создаем зашифрованный бэкап
        password = "SecureClaudePassword123!"
        ok, msg, bp = backup_manager.create_encrypted_backup(password, target_dir=backup_dir)
        self.assertTrue(ok)
        self.assertIsNotNone(bp)

        # 3. Меняем настройки на другие (сбиваем)
        settings_manager.set_claude_proxy_settings("127.0.0.1", 1081, killswitch=False)
        self.assertEqual(settings_manager.get_claude_proxy_host(), "127.0.0.1")
        self.assertEqual(settings_manager.get_claude_proxy_port(), 1081)
        self.assertFalse(settings_manager.get_claude_killswitch())

        # 4. Восстанавливаем бэкап
        ok_res, res_msg, manifest = backup_manager.restore_encrypted_backup(bp, password)
        self.assertTrue(ok_res)

        # 5. Проверяем, что настройки Claude и Killswitch восстановились из бэкапа
        self.assertEqual(settings_manager.get_claude_proxy_host(), "10.0.0.5")
        self.assertEqual(settings_manager.get_claude_proxy_port(), 8888)
        self.assertTrue(settings_manager.get_claude_killswitch())

    def test_gemini_switching_does_not_override_claude(self):
        """Проверяет, что переключение портов Gemini не перезаписывает настройки Claude."""
        # Задаем настройки Claude
        settings_manager.set_claude_proxy_settings("127.0.0.1", 1085, killswitch=True)

        # Эмулируем переключение аккаунта Gemini на порт 1082
        with patch("gemini_manager.ACTIVE_PROXY_ENV_FILE", Path(self.temp_dir) / "active_proxy.env"), \
             patch("gemini_manager.ACTIVE_PROFILE_JSON_FILE", Path(self.temp_dir) / "active_profile.json"), \
             patch("gemini_manager.CLI_DIR", Path(self.temp_dir)):
            gemini_manager.write_active_proxy_env(1082, "test@example.com", "profile-2")

        # Настройки Claude в settings_manager остаются нетронутыми
        self.assertEqual(settings_manager.get_claude_proxy_port(), 1085)
        self.assertEqual(settings_manager.get_claude_proxy_host(), "127.0.0.1")
        self.assertTrue(settings_manager.get_claude_killswitch())

    def test_custom_edited_settings_persist_across_restarts_and_backup(self):
        """Проверяет редактирование хоста и порта Claude, сохранение при перезапуске и перенос в бэкапах."""
        backup_dir = Path(self.temp_dir) / "backups_custom"
        backup_dir.mkdir()

        # 1. Пользователь отредактировал хост и порт (например, 192.168.0.50:9055)
        settings_manager.set_claude_proxy_settings("192.168.0.50", 9055, killswitch=True)

        # 2. Имитируем перезапуск приложения (считываем напрямую через load_settings)
        loaded_settings = settings_manager.load_settings()
        self.assertEqual(loaded_settings["claude_proxy_host"], "192.168.0.50")
        self.assertEqual(loaded_settings["claude_proxy_port"], 9055)
        self.assertTrue(loaded_settings["claude_killswitch"])

        # 3. Экспортируем в бэкап
        password = "TestCustomBackup123!"
        ok, msg, bp = backup_manager.create_encrypted_backup(password, target_dir=backup_dir)
        self.assertTrue(ok)

        # 4. Сбиваем настройки обратно на дефолтные 127.0.0.1:1015
        settings_manager.set_claude_proxy_settings("127.0.0.1", 1015, killswitch=False)
        self.assertEqual(settings_manager.get_claude_proxy_port(), 1015)

        # 5. Восстанавливаем из бэкапа
        ok_res, res_msg, manifest = backup_manager.restore_encrypted_backup(bp, password)
        self.assertTrue(ok_res)

        # 6. Проверяем, что отредактированные пользователем host и port успешно восстановились
        self.assertEqual(settings_manager.get_claude_proxy_host(), "192.168.0.50")
        self.assertEqual(settings_manager.get_claude_proxy_port(), 9055)
        self.assertTrue(settings_manager.get_claude_killswitch())


if __name__ == "__main__":
    unittest.main()
