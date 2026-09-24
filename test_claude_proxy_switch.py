"""Тесты для исправления переключения прокси, живой проверки портов и авто-восстановления Claude.

Проверяет:
1. Автосинхронизацию proxies.json из instances.json проекта vless2socks (включая System Proxy :1015).
2. Немедленное включение прокси (online) при переключении на открытый порт (включая 1015).
3. Работу живой проверки порта в is_proxy_active_in_list (статус unknown не блокирует рабочий порт).
4. Авто-переключение (Auto-Failover) Claude при падении порта (с закрытого 1085 на открытый 1015 System Proxy).
5. Корректное отображение меток и авто-выбор активного порта в Combobox.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import proxy_manager
import settings_manager
import claude_manager


class TestClaudeProxySwitching(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_settings_file = settings_manager.SETTINGS_FILE
        self.orig_proxies_file = proxy_manager.PROXIES_FILE

        self.test_settings_file = Path(self.temp_dir) / "settings.json"
        self.test_proxies_file = Path(self.temp_dir) / "proxies.json"
        self.test_claude_file = Path(self.temp_dir) / "claude_settings.json"
        self.test_vless_instances = Path(self.temp_dir) / "instances.json"

        settings_manager.SETTINGS_FILE = self.test_settings_file
        proxy_manager.PROXIES_FILE = self.test_proxies_file
        proxy_manager.VLESS2SOCKS_INSTANCES_FILE = self.test_vless_instances

        settings_manager.save_settings({
            "language": "ru",
            "auto_proxy_failover": True,
            "claude_proxy_host": "127.0.0.1",
            "claude_proxy_port": 1085,
            "claude_killswitch": True,
        })

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig_settings_file
        proxy_manager.PROXIES_FILE = self.orig_proxies_file
        proxy_manager.VLESS2SOCKS_INSTANCES_FILE = None
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_sync_from_vless2socks_imports_system_proxy_1015(self):
        """Проверяет синхронизацию proxies.json из instances.json проекта vless2socks."""
        instances_data = [
            {
                "name": "System Proxy",
                "order": 0,
                "url": "",
                "listen": "127.0.0.1:1015",
                "killswitch": True,
            },
            {
                "name": "FI FINLAND 3 VLESS TCP",
                "order": 1,
                "url": "vless://uuid@host:443#FINLAND",
                "listen": "127.0.0.1:1081",
                "killswitch": False,
            },
            {
                "name": "",
                "order": 2,
                "url": "vless://uuid@host:443#%F0%9F%87%AB%F0%9F%87%B7%20FRANCE",
                "listen": "127.0.0.1:1083",
                "killswitch": False,
            }
        ]
        with open(self.test_vless_instances, "w", encoding="utf-8") as f:
            json.dump(instances_data, f)

        synced = proxy_manager.sync_from_vless2socks(self.test_vless_instances)
        self.assertGreaterEqual(len(synced), 3)

        ports = [p["port"] for p in synced]
        self.assertIn(1015, ports)
        self.assertIn(1081, ports)
        self.assertIn(1083, ports)

        # 1015 должен идти первым как System Proxy (#0)
        p1015 = next(p for p in synced if p["port"] == 1015)
        self.assertEqual(p1015["id"], 1)
        self.assertEqual(p1015["label"], "System Proxy")

        # Проверяем декодирование URL remark если name пустое
        p1083 = next(p for p in synced if p["port"] == 1083)
        self.assertIn("FRANCE", p1083["label"])

    @patch("claude_manager.get_claude_settings_files")
    @patch("claude_manager.check_port_accessible")
    @patch("requests.get")
    def test_switching_to_open_port_immediately_turns_online(
        self, mock_requests_get, mock_check_port, mock_get_files
    ):
        """Проверяет немедленный переход в online при переключении на открытый порт (порт 1015)."""
        mock_get_files.return_value = [self.test_claude_file]
        mock_check_port.return_value = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        proxy_manager.save_proxies([
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1015,
                "label": "System Proxy",
                "status": "unknown",
                "ip": "-",
                "country": "undefined",
            }
        ])

        claude_manager.save_claude_config("127.0.0.1", 1015, killswitch=True)
        res = claude_manager.probe_claude_route("127.0.0.1", 1015, killswitch=True)

        self.assertTrue(res["online"])
        self.assertFalse(res["killswitch_engaged"])
        self.assertEqual(res["port"], 1015)
        self.assertIn("активен", res["status_text"])

        # Проверяем, что в proxies.json статус обновился до online
        proxies = proxy_manager.load_proxies()
        p1015 = next(p for p in proxies if p["port"] == 1015)
        self.assertEqual(p1015["status"], "online")

        # Проверяем, что файл настроек Claude разблокирован (не blackhole)
        with open(self.test_claude_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["env"]["HTTPS_PROXY"], "http://127.0.0.1:11015")
        self.assertEqual(saved["env"]["ALL_PROXY"], "socks5h://127.0.0.1:1015")

    @patch("claude_manager.check_port_accessible")
    def test_unknown_status_does_not_block_accessible_port(self, mock_check_port):
        """Проверяет, что статус unknown в proxies.json не блокирует физически открытый порт."""
        mock_check_port.return_value = True

        proxy_manager.save_proxies([
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1084,
                "label": "SOCKS5 :1084",
                "status": "unknown",
                "ip": "-",
                "country": "undefined",
            }
        ])

        is_active, reason, p_entry = claude_manager.is_proxy_active_in_list("127.0.0.1", 1084)
        self.assertTrue(is_active)
        self.assertEqual(reason, "online")
        self.assertIsNotNone(p_entry)
        self.assertEqual(p_entry["status"], "online")

    @patch("claude_manager.get_claude_settings_files")
    @patch("claude_manager.check_port_accessible")
    def test_claude_strictly_killswitches_when_port_closed_no_failover(
        self, mock_check_port, mock_get_files
    ):
        """Проверяет, что при закрытом порте 1085 Claude НЕ переключается на другие порты,
        а строго активирует Killswitch (blackhole), оставаясь на порту 1085."""
        mock_get_files.return_value = [self.test_claude_file]

        # 1085 закрыт, 1015 открыт
        def port_check_side_effect(host, port, timeout=0.8):
            return int(port) == 1015

        mock_check_port.side_effect = port_check_side_effect

        proxy_manager.save_proxies([
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1015,
                "label": "System Proxy",
                "status": "online",
                "ip": "-",
                "country": "undefined",
            },
            {
                "id": 2,
                "host": "127.0.0.1",
                "port": 1085,
                "label": "SOCKS5 :1085",
                "status": "offline",
                "ip": "-",
                "country": "undefined",
            }
        ])

        settings_manager.set_claude_proxy_settings("127.0.0.1", 1085, killswitch=True)
        res = claude_manager.probe_claude_route()

        # Проверяем: Claude не переключился на 1015, а заблокирован в Killswitch!
        self.assertFalse(res["online"])
        self.assertTrue(res["killswitch_engaged"])
        self.assertFalse(res.get("failover_occurred", False))
        self.assertEqual(res["port"], 1085)
        self.assertIn("KILLSWITCH АКТИВЕН", res["status_text"])
        self.assertEqual(settings_manager.get_claude_proxy_port(), 1085)

        # Проверяем, что в конфиг Claude записан blackhole
        with open(self.test_claude_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["env"]["HTTPS_PROXY"], "http://127.0.0.1:1")
        self.assertEqual(data["env"]["ALL_PROXY"], "socks5h://127.0.0.1:1")

    def test_claude_combo_label_and_selection(self):
        """Проверяет корректное формирование меток и выбор значения в combobox."""
        import tkinter as tk
        from tkinter import ttk
        import config_app

        try:
            root = tk.Tk()
            root.withdraw()
        except Exception:
            self.skipTest("Tkinter GUI display not available")

        try:
            proxy_manager.save_proxies([
                {
                    "id": 1,
                    "host": "127.0.0.1",
                    "port": 1015,
                    "label": "System Proxy",
                    "status": "online",
                },
                {
                    "id": 2,
                    "host": "127.0.0.1",
                    "port": 1081,
                    "label": "Finland 3",
                    "status": "online",
                }
            ])

            # Создаем фейковый объект приложения
            class MockApp:
                pass

            app = MockApp()
            app.claude_port_var = tk.StringVar(value="1015")
            app.claude_host_var = tk.StringVar(value="127.0.0.1")
            app.claude_proxy_combo = ttk.Combobox(root)

            # Вызываем метод обновления комбобокса
            with patch("proxy_manager.check_socks5_handshake", return_value=True):
                config_app.HerdrConfigApp._update_claude_proxy_combo(app)

            values = list(app.claude_proxy_combo["values"])
            self.assertEqual(len(values), 2)
            self.assertIn("127.0.0.1:1015 - System Proxy (online)", values[0])
            self.assertIn("127.0.0.1:1081 - Finland 3 (online)", values[1])

            # Проверяем, что комбобокс автоматически выставил активный порт 1015
            self.assertEqual(app.claude_proxy_combo.get(), values[0])

            # Проверяем выбор другого элемента
            app.claude_proxy_combo.set(values[1])
            app._save_claude_settings_action = MagicMock()
            config_app.HerdrConfigApp._on_claude_combo_selected(app)

            self.assertEqual(app.claude_host_var.get(), "127.0.0.1")
            self.assertEqual(app.claude_port_var.get(), "1081")
            app._save_claude_settings_action.assert_called_once()
        finally:
            try:
                root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
