"""Тесты для проверки инициализации графического интерфейса Herdr Control Center и работы карточки Claude."""

import unittest
from unittest.mock import patch, MagicMock
import tkinter as tk
import config_app
import settings_manager
import claude_manager
import proxy_manager


class TestAppUI(unittest.TestCase):
    def setUp(self):
        # Предотвращаем запуск фоновых потоков refresh_routes_async во время теста
        self.patcher = patch.object(config_app.HerdrConfigApp, "refresh_routes_async")
        self.mock_refresh = self.patcher.start()

        try:
            self.app = config_app.HerdrConfigApp()
            self.app.withdraw()  # Скрываем окно во время тестов
        except tk.TclError:
            self.patcher.stop()
            self.skipTest("Tkinter display/Tcl interpreter not available")

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        self.patcher.stop()

    def test_app_initialization(self):
        """Проверяет, что главное окно успешно инициализируется со всеми элементами управления Claude."""
        self.assertIsNotNone(self.app)
        self.assertTrue(hasattr(self.app, "claude_card"))
        self.assertTrue(hasattr(self.app, "claude_host_entry"))
        self.assertTrue(hasattr(self.app, "claude_port_entry"))
        self.assertTrue(hasattr(self.app, "claude_ks_cb"))
        self.assertTrue(hasattr(self.app, "claude_proxy_combo"))
        self.assertTrue(hasattr(self.app, "claude_save_btn"))

    def test_claude_save_action(self):
        """Проверяет сохранение настроек через кнопку карточки Claude."""
        self.app.claude_host_var.set("127.0.0.1")
        self.app.claude_port_var.set("1099")
        self.app.claude_killswitch_var.set(True)

        with patch("config_app.claude_manager.save_claude_config") as mock_save:
            self.app._save_claude_settings_action()
            mock_save.assert_called_once_with("127.0.0.1", 1099, True)

    def test_claude_combo_select(self):
        """Проверяет выбор прокси из выпадающего списка."""
        self.app.claude_proxy_combo["values"] = ["127.0.0.1:1085 (online)", "127.0.0.1:1082 (offline)"]
        self.app.claude_proxy_combo.set("127.0.0.1:1085 (online)")

        with patch.object(self.app, "_save_claude_settings_action") as mock_save:
            self.app._on_claude_combo_selected()
            self.assertEqual(self.app.claude_host_var.get(), "127.0.0.1")
            self.assertEqual(self.app.claude_port_var.get(), "1085")
            mock_save.assert_called_once()

    def test_apply_route_results_claude_online(self):
        """Проверяет корректное отображение в UI, когда Claude онлайн."""
        mock_res = {
            "timestamp": "12:00:00",
            "gemini": {"online": True, "port": 1081},
            "claude": {
                "online": True,
                "host": "127.0.0.1",
                "port": 1085,
                "http_port": 11085,
                "ip": "1.2.3.4",
                "country": "Germany",
                "latency_ms": 95,
                "status_text": "● Прокси активен (127.0.0.1:1085)",
                "killswitch_engaged": False,
                "killswitch_enabled": True,
            },
            "herdr_wsl": {"server_running": False}
        }
        self.app._apply_route_results(mock_res)
        self.assertIn("1.2.3.4", self.app.claude_ip_lbl.cget("text"))
        self.assertIn("95", self.app.claude_ping_lbl.cget("text"))

    def test_apply_route_results_claude_killswitch_engaged(self):
        """Проверяет корректное отображение в UI, когда сработал Killswitch."""
        mock_res = {
            "timestamp": "12:00:00",
            "gemini": {"online": True, "port": 1081},
            "claude": {
                "online": False,
                "host": "127.0.0.1",
                "port": 1085,
                "http_port": 11085,
                "ip": "-",
                "status_text": "🛡️ KILLSWITCH АКТИВЕН: Прокси не в сети",
                "killswitch_engaged": True,
                "killswitch_enabled": True,
            },
            "herdr_wsl": {"server_running": False}
        }
        self.app._apply_route_results(mock_res)
        self.assertIn("KILLSWITCH", self.app.claude_status_lbl.cget("text"))
        self.assertIn("заблокирован", self.app.claude_ip_lbl.cget("text").lower())

    def test_switch_all_tabs(self):
        """Проверяет переключение по всем вкладкам интерфейса без ошибок."""
        tabs = ["routes", "proxy", "gemini", "strategy", "backup", "localization"]
        for tab in tabs:
            self.app.switch_page(tab)
            self.assertEqual(self.app.active_tab, tab)
            self.app.update_idletasks()


if __name__ == "__main__":
    unittest.main()
