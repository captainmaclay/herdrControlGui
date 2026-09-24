"""Unit-тесты для модуля integrations_manager и GUI вкладки 'Интеграции'."""

from __future__ import annotations

import datetime
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import integrations_manager
from integrations_manager import IntegrationLogger, MAX_LOG_BYTES


class TestIntegrationLogger(unittest.TestCase):
    """Тестирование логгера интеграций, авто-ротации при >1МБ и уведомления слушателей."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "test_integrations.log"
        self.logger = IntegrationLogger(self.log_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_format_and_file_creation(self):
        """Проверяет формат записи лога: дата, время, уровень и сообщение."""
        line = self.logger.log("Тестовое сообщение", "INFO")
        self.assertTrue(self.log_file.exists())
        self.assertIn("INFO", line)
        self.assertIn("Тестовое сообщение", line)

        # Проверка формата даты: [YYYY-MM-DD HH:MM:SS]
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        self.assertIn(f"[{today_str}", line)

        # Чтение из файла
        content = self.logger.read_history()
        self.assertIn("Тестовое сообщение", content)

    def test_listener_callback_invoked(self):
        """Проверяет, что зарегистрированный слушатель (GUI) получает события лога."""
        received = []

        def callback(ts, level, msg):
            received.append((ts, level, msg))

        self.logger.add_listener(callback)
        self.logger.log("Событие 1", "SUCCESS")
        self.logger.log("Событие 2", "ERROR")

        self.assertEqual(len(received), 2)
        self.assertEqual(received[0][1], "SUCCESS")
        self.assertEqual(received[0][2], "Событие 1")
        self.assertEqual(received[1][1], "ERROR")
        self.assertEqual(received[1][2], "Событие 2")

        # Удаление слушателя
        self.logger.remove_listener(callback)
        self.logger.log("Событие 3", "INFO")
        self.assertEqual(len(received), 2)

    def test_auto_rotation_when_exceeding_1mb(self):
        """Проверяет, что при превышении размера 1 МБ старые строки удаляются и размер уменьшается."""
        # Создаем искусственный лог размером 1.2 МБ (1 258 291 байт)
        line_content = "X" * 100 + "\n"
        num_lines = 12000  # ~1.2 MB
        with open(self.log_file, "w", encoding="utf-8") as f:
            for i in range(num_lines):
                f.write(f"Line {i:06d}: {line_content}")

        initial_size = self.log_file.stat().st_size
        self.assertGreater(initial_size, MAX_LOG_BYTES)

        # Записываем новое сообщение, что должно запустить _rotate_if_needed
        self.logger.log("Новое сообщение после переполнения", "WARN")

        new_size = self.log_file.stat().st_size
        # Размер должен упасть ниже MAX_LOG_BYTES (примерно до половины ~500-600 КБ)
        self.assertLess(new_size, MAX_LOG_BYTES)

        content = self.logger.read_history()
        self.assertIn("Ротация логов: старые записи удалены (лимит 1 МБ)", content)
        self.assertIn("Новое сообщение после переполнения", content)
        # Самые старые строки должны быть удалены
        self.assertNotIn("Line 000001:", content)
        # Более свежие строки должны сохраниться
        self.assertIn("Line 011000:", content)

    def test_clear_history(self):
        """Проверяет очистку журнала логов."""
        self.logger.log("Сообщение перед очисткой", "INFO")
        self.assertTrue(self.logger.clear_history())
        content = self.logger.read_history()
        self.assertIn("Журнал логов очищен пользователем", content)
        self.assertNotIn("Сообщение перед очисткой", content)

    def test_get_file_size_str(self):
        """Проверяет форматирование размера файла."""
        self.logger.log("Короткая строка", "INFO")
        size_str = self.logger.get_file_size_str()
        self.assertTrue(size_str.endswith("Б") or size_str.endswith("КБ"))


class TestIntegrationsManagerOperations(unittest.TestCase):
    """Тестирование функций статуса и синхронизации."""

    @patch("integrations_manager.check_http_status")
    @patch("integrations_manager.check_port_accessible")
    @patch("aionui_claude_bridge.get_oauth_status")
    @patch("gemini_manager.list_profiles")
    def test_check_aionui_status(self, mock_profiles, mock_oauth, mock_port, mock_http):
        """Проверяет сбор комплексного статуса всех компонентов."""
        mock_http.side_effect = [
            (True, 200, "OK"),   # AionUi
            (True, 200, "OK"),   # OmniRoute
        ]
        mock_port.side_effect = [
            True,  # Claude 1015
            True,  # Claude 11015
            True,  # Gemini port 1082
        ]
        mock_oauth.return_value = {"authorized": True, "subscription_type": "pro", "days_left": 15}
        mock_profiles.return_value = [{"name": "Account 1", "port": 1082}]

        res = integrations_manager.check_aionui_status()

        self.assertTrue(res["aionui"]["online"])
        self.assertEqual(res["aionui"]["code"], 200)
        self.assertTrue(res["omniroute"]["online"])
        self.assertTrue(res["claude"]["socks5_1015"])
        self.assertTrue(res["claude"]["oauth"]["authorized"])
        self.assertEqual(res["gemini"]["total_profiles"], 1)
        self.assertEqual(res["gemini"]["active_ports"], [1082])

    @patch("aionui_claude_bridge.get_oauth_status")
    @patch("aionui_claude_bridge.ensure_claude_proxy_settings")
    @patch("aionui_claude_bridge.patch_aionui_managed_resources")
    @patch("aionui_claude_bridge.patch_aionui_database")
    def test_sync_claude_flow(self, mock_db, mock_res, mock_proxy, mock_auth):
        """Проверяет выполнение этапов синхронизации Claude."""
        mock_auth.return_value = {"authorized": True, "subscription_type": "pro", "days_left": 10}
        mock_proxy.return_value = {"success": True}
        mock_res.return_value = {"success": True}
        mock_db.return_value = {"success": True, "rows_affected": 1}

        res = integrations_manager.sync_claude()
        self.assertTrue(res["success"])
        self.assertTrue(res["proxy"]["success"])
        self.assertTrue(res["resources"]["success"])
        self.assertTrue(res["db"]["success"])

    @patch("gemini_manager.list_profiles")
    @patch("integrations_manager.check_port_accessible")
    @patch("urllib.request.urlopen")
    def test_sync_gemini_flow(self, mock_urlopen, mock_port, mock_profiles):
        """Проверяет выполнение этапов синхронизации Gemini."""
        mock_profiles.return_value = [
            {"name": "Prof1", "email": "p1@gmail.com", "port": 1082},
        ]
        mock_port.return_value = True

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"choices": [{"message": {"content": "Pong"}}]}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = integrations_manager.sync_gemini()
        self.assertTrue(res["success"])
        self.assertEqual(len(res["profiles"]), 1)
        self.assertTrue(res["profiles"][0]["alive"])
        self.assertTrue(res["ping_ok"])


class TestGUIIntegrationsTab(unittest.TestCase):
    """Тестирование наличия и функционирования вкладки Интеграции в HerdrConfigApp."""

    @patch("config_app.HerdrConfigApp.refresh_routes_async")
    @patch("config_app.HerdrConfigApp._schedule_fast_port_check")
    @patch("config_app.HerdrConfigApp._schedule_auto_refresh")
    @patch("integrations_manager.check_aionui_status")
    def test_gui_elements_initialization(self, mock_status, mock_auto, mock_fast, mock_refresh):
        """Проверяет создание вкладки, кнопок и текстовой консоли без зависания."""
        mock_status.return_value = {
            "aionui": {"online": True, "code": 200},
            "omniroute": {"online": True, "code": 200},
            "claude": {"socks5_1015": True, "oauth": {"authorized": True, "subscription_type": "pro"}},
            "gemini": {"total_profiles": 2, "active_ports": [1082, 1083]}
        }
        import config_app

        root = config_app.HerdrConfigApp()
        root.withdraw()
        try:
            # Проверяем наличие кнопки вкладки
            self.assertIn("integrations", root.tab_buttons)

            # Проверяем переключение на страницу
            root.switch_page("integrations")
            self.assertEqual(root.active_tab, "integrations")

            # Проверяем наличие ключевых виджетов на странице
            self.assertTrue(hasattr(root, "btn_integ_status"))
            self.assertTrue(hasattr(root, "btn_integ_sync_claude"))
            self.assertTrue(hasattr(root, "btn_integ_sync_gemini"))
            self.assertTrue(hasattr(root, "btn_integ_history"))
            self.assertTrue(hasattr(root, "btn_integ_clear_console"))
            self.assertTrue(hasattr(root, "integ_console"))

            # Проверяем очистку экранной консоли
            root.clear_integrations_console()

            # Проверяем добавление лога в консоль через слушатель
            root._on_integration_log("2026-09-24 12:00:00", "SUCCESS", "Тестовый лог в GUI")
            root.update()

            content = root.integ_console.get("1.0", "end")
            self.assertIn("Тестовый лог в GUI", content)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
