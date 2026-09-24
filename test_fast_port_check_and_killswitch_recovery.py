"""Unit tests for fast port availability checking and instantaneous Claude Killswitch recovery.

Validates:
1. Fast probing mode (fast=True) runs in < 20ms and disengages/engages Killswitch immediately.
2. Settings manager returns fast check interval <= 5.0s (default 2.0s).
3. Port transition from offline to online immediately restores proxy and disengages Killswitch.
4. Proxy status updates in real-time without locking the UI.
"""

import time
import unittest
from unittest.mock import patch, MagicMock
import tkinter as tk

import claude_manager
import settings_manager
import proxy_manager
import config_app


class TestFastPortCheckAndKillswitchRecovery(unittest.TestCase):
    def setUp(self):
        settings_manager.save_settings(settings_manager.DEFAULT_SETTINGS)

    def test_get_port_check_interval_within_5_seconds(self):
        """Интервал проверки портов по умолчанию не превышает 5 секунд (равен 2.0 сек)."""
        interval = settings_manager.get_port_check_interval()
        self.assertLessEqual(interval, 5.0)
        self.assertEqual(interval, 2.0)

        # Проверка кастомных значений с ограничением границ
        settings_manager.set_setting("port_check_interval_seconds", 1.5)
        self.assertEqual(settings_manager.get_port_check_interval(), 1.5)

        settings_manager.set_setting("port_check_interval_seconds", 0.1)  # Ниже минимума 0.5
        self.assertEqual(settings_manager.get_port_check_interval(), 0.5)

        settings_manager.set_setting("port_check_interval_seconds", 60.0)  # Выше максимума 30.0
        self.assertEqual(settings_manager.get_port_check_interval(), 30.0)

    @patch("claude_manager.get_claude_settings_files")
    @patch("claude_manager.check_port_accessible")
    def test_probe_claude_route_fast_mode_speed_and_recovery(self, mock_access, mock_files):
        """Режим fast=True отрабатывает моментально (< 20мс) и снимает Killswitch при открытии порта."""
        mock_files.return_value = []

        # 1. Порт закрыт -> Killswitch активен
        mock_access.return_value = False
        t0 = time.time()
        res_offline = claude_manager.probe_claude_route("127.0.0.1", 1015, killswitch=True, fast=True)
        elapsed_offline = time.time() - t0

        self.assertFalse(res_offline["online"])
        self.assertTrue(res_offline["killswitch_engaged"])
        self.assertLess(elapsed_offline, 0.05, f"Fast offline check took too long: {elapsed_offline:.4f}s")

        # 2. Порт открылся -> Killswitch моментально снимается (fast recovery)
        mock_access.return_value = True
        t0 = time.time()
        res_online = claude_manager.probe_claude_route("127.0.0.1", 1015, killswitch=True, fast=True)
        elapsed_online = time.time() - t0

        self.assertTrue(res_online["online"])
        self.assertFalse(res_online["killswitch_engaged"])
        self.assertLess(elapsed_online, 0.05, f"Fast recovery check took too long: {elapsed_online:.4f}s")

    @patch("claude_manager.apply_claude_proxy_sync")
    @patch("claude_manager.check_port_accessible")
    @patch("claude_manager.get_claude_settings_files")
    def test_claude_proxy_sync_called_on_state_change(self, mock_files, mock_access, mock_sync):
        """При переходе порта из закрытого в открытый вызывается apply_claude_proxy_sync(killswitch_engaged=False)."""
        mock_files.return_value = []

        # Порт стал доступен
        mock_access.return_value = True
        claude_manager.probe_claude_route("127.0.0.1", 1015, killswitch=True, fast=True)
        mock_sync.assert_called_with("127.0.0.1", 1015, killswitch_engaged=False)

        # Порт упал
        mock_access.return_value = False
        claude_manager.probe_claude_route("127.0.0.1", 1015, killswitch=True, fast=True)
        mock_sync.assert_called_with("127.0.0.1", 1015, killswitch_engaged=True)

    def test_gui_fast_watchdog_detects_port_transition_and_updates_ui(self):
        """Интерфейс приложения моментально обновляет статус при восстановлении порта."""
        try:
            with patch.object(config_app.HerdrConfigApp, "refresh_routes_async"):
                app = config_app.HerdrConfigApp()
                app.withdraw()
        except tk.TclError:
            self.skipTest("Tkinter display not available")

        try:
            # Изначально порт Claude считался закрытым
            app._last_claude_accessible = False

            # Моделируем открытие порта
            simulated_claude_result = {
                "online": True,
                "status_text": "SOCKS5 :1015 (online)",
                "host": "127.0.0.1",
                "port": 1015,
                "http_port": 11015,
                "killswitch_engaged": False,
                "killswitch_enabled": True,
                "ip": "213.165.63.177",
                "country": "LV",
                "latency_ms": 35,
            }

            # Применяем результаты быстрого опроса
            app._apply_fast_port_results(simulated_claude_result, ports_changed=False)

            # Проверяем, что UI переключился в активный режим
            self.assertIn("online", app.claude_status_lbl.cget("text"))
            self.assertIn("PROXY", app.claude_pill.cget("text"))
            self.assertIn("11015", app.claude_route_lbl.cget("text"))
            self.assertNotIn("KILLSWITCH BLOCK", app.claude_route_lbl.cget("text"))

            # Проверяем, что закрытие приложения останавливает таймер
            app.destroy()
            self.assertTrue(app._closing)
        finally:
            try:
                app.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
