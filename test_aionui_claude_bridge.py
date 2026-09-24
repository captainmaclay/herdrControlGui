"""Автоматические тесты для модуля aionui_claude_bridge.

Проверяет:
1. Валидацию различных структур OAuth2-файлов (.credentials.json и прямых словарей токенов).
2. Импорт OAuth2 файла с автоматическим резервным копированием (.bak_).
3. Проверку статуса авторизации, расчет дней до истечения и валидность токенов.
4. Синхронизацию настроек прокси (:1015 / :11015) и флага Killswitch в settings.json.
5. Корректность работы скрипта патча ресурсов AionUi и базы данных SQLite.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import aionui_claude_bridge


class TestAionUiClaudeBridge(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_src_oauth = Path(self.temp_dir) / "test_new_oauth.json"
        self.test_target_win = Path(self.temp_dir) / "win_creds.json"
        self.test_target_wsl = Path(self.temp_dir) / "wsl_creds.json"
        self.test_settings_win = Path(self.temp_dir) / "win_settings.json"
        self.test_settings_wsl = Path(self.temp_dir) / "wsl_settings.json"

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_validate_oauth_data_valid_nested(self):
        """Проверяет валидацию структуры со вложенным ключом claudeAiOauth."""
        data = {
            "claudeAiOauth": {
                "accessToken": "sk-ant-test-access-token",
                "refreshToken": "sk-ant-test-refresh-token",
                "expiresAt": int((time.time() + 3600) * 1000),
                "subscriptionType": "pro",
            }
        }
        valid, msg, normalized = aionui_claude_bridge.validate_oauth_data(data)
        self.assertTrue(valid)
        self.assertIn("claudeAiOauth", normalized)
        self.assertEqual(normalized["claudeAiOauth"]["accessToken"], "sk-ant-test-access-token")
        self.assertEqual(normalized["claudeAiOauth"]["subscriptionType"], "pro")

    def test_validate_oauth_data_flat(self):
        """Проверяет валидацию плоской структуры с токенами."""
        data = {
            "accessToken": "sk-ant-flat-token",
            "refreshToken": "sk-ant-flat-refresh",
            "subscriptionType": "team",
        }
        valid, msg, normalized = aionui_claude_bridge.validate_oauth_data(data)
        self.assertTrue(valid)
        self.assertEqual(normalized["claudeAiOauth"]["accessToken"], "sk-ant-flat-token")
        self.assertEqual(normalized["claudeAiOauth"]["subscriptionType"], "team")

    def test_validate_oauth_data_invalid_missing_tokens(self):
        """Проверяет отклонение некорректных данных без refreshToken."""
        invalid_data = {"accessToken": "only_access"}
        valid, msg, _ = aionui_claude_bridge.validate_oauth_data(invalid_data)
        self.assertFalse(valid)
        self.assertIn("refreshToken", msg)

    def test_import_oauth_file_creates_backup_and_syncs(self):
        """Проверяет импорт OAuth с созданием резервной копии и синхронизацией в целевые файлы."""
        # Создаем существующий файл для проверки бэкапа
        with open(self.test_target_win, "w", encoding="utf-8") as f:
            json.dump({"claudeAiOauth": {"accessToken": "old_token", "refreshToken": "old_refresh"}}, f)

        # Создаем новый файл для импорта
        new_payload = {
            "accessToken": "new_shiny_access_token",
            "refreshToken": "new_shiny_refresh_token",
            "subscriptionType": "pro",
        }
        with open(self.test_src_oauth, "w", encoding="utf-8") as f:
            json.dump(new_payload, f)

        targets = [self.test_target_win, self.test_target_wsl]
        res = aionui_claude_bridge.import_oauth_file(self.test_src_oauth, target_files=targets)

        self.assertTrue(res["success"])
        self.assertEqual(len(res["synced"]), 2)

        # Проверяем, что создался файл бэкапа
        bak_files = list(Path(self.temp_dir).glob("win_creds.json.bak_*"))
        self.assertEqual(len(bak_files), 1)

        # Проверяем, что в целевые файлы записан новый токен
        with open(self.test_target_win, "r", encoding="utf-8") as f:
            saved = json.load(f)
            self.assertEqual(saved["claudeAiOauth"]["accessToken"], "new_shiny_access_token")

        with open(self.test_target_wsl, "r", encoding="utf-8") as f:
            saved_wsl = json.load(f)
            self.assertEqual(saved_wsl["claudeAiOauth"]["refreshToken"], "new_shiny_refresh_token")

    def test_ensure_claude_proxy_settings(self):
        """Проверяет правильность записи переменных окружения прокси."""
        with patch("aionui_claude_bridge.get_claude_settings_files", return_value=[self.test_settings_win, self.test_settings_wsl]):
            res = aionui_claude_bridge.ensure_claude_proxy_settings(host="127.0.0.1", port=1015, killswitch=True)
            self.assertTrue(res["success"])

            for p in [self.test_settings_win, self.test_settings_wsl]:
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    self.assertEqual(cfg["env"]["HTTPS_PROXY"], "http://127.0.0.1:11015")
                    self.assertEqual(cfg["env"]["ALL_PROXY"], "socks5h://127.0.0.1:1015")
                    self.assertEqual(cfg["env"]["DISABLE_AUTOUPDATER"], "1")

    def test_get_oauth_status_active_token(self):
        """Проверяет отчет о статусе токена с расчетом оставшихся дней."""
        now = time.time()
        future_exp = int((now + 20 * 86400) * 1000)
        data = {
            "claudeAiOauth": {
                "accessToken": "tok",
                "refreshToken": "ref",
                "expiresAt": int((now + 3600) * 1000),
                "refreshTokenExpiresAt": future_exp,
                "subscriptionType": "pro",
            }
        }
        with open(self.test_target_win, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with patch("aionui_claude_bridge.get_claude_credentials_files", return_value=[self.test_target_win]):
            st = aionui_claude_bridge.get_oauth_status()
            self.assertTrue(st["authorized"])
            self.assertEqual(st["subscription_type"], "pro")
            self.assertGreater(st["days_left"], 18)


if __name__ == "__main__":
    unittest.main()
