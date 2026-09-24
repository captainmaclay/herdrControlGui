"""Тесты для проверки живого статуса локальных SOCKS5-портов и исключения фантомного статуса 'online'.

Проверяет:
1. Быстрое SOCKS5-рукопожатие (check_socks5_handshake) корректно определяет рабочий SOCKS5 и закрытый порт.
2. refresh_local_ports_status немедленно переводит закрытый локальный порт (например, 1085) из 'online' в 'offline'.
3. refresh_local_ports_status переводит рабочий локальный порт из 'offline'/'unknown' в 'online'.
4. load_proxies(refresh_status=True) сохраняет подтвержденное состояние в proxies.json.
5. probe_claude_route строго включает Killswitch при закрытом порте (1085) и успешно соединяется при открытом порте (1015).
"""

from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import proxy_manager
import settings_manager
import claude_manager


class TestLocalProxyStatusRefresh(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_settings_file = settings_manager.SETTINGS_FILE
        self.orig_proxies_file = proxy_manager.PROXIES_FILE

        self.test_settings_file = Path(self.temp_dir) / "settings.json"
        self.test_proxies_file = Path(self.temp_dir) / "proxies.json"
        self.test_claude_file = Path(self.temp_dir) / "claude_settings.json"

        settings_manager.SETTINGS_FILE = self.test_settings_file
        proxy_manager.PROXIES_FILE = self.test_proxies_file

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
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_check_socks5_handshake_detects_open_and_closed_ports(self):
        """Проверяет реакцию check_socks5_handshake на живой SOCKS5-сокет и на закрытый порт."""
        # 1. Закрытый порт должен возвращать False
        self.assertFalse(proxy_manager.check_socks5_handshake("127.0.0.1", 64532, timeout=0.1))

        # 2. Эмулируем сервер SOCKS5 на случайном свободном порту
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def srv_worker():
            try:
                conn, _ = srv.accept()
                req = conn.recv(3)
                if req == b"\x05\x01\x00":
                    conn.sendall(b"\x05\x00")
                conn.close()
            except Exception:
                pass
            finally:
                srv.close()

        th = threading.Thread(target=srv_worker, daemon=True)
        th.start()

        # Проверяем рукопожатие
        is_alive = proxy_manager.check_socks5_handshake("127.0.0.1", port, timeout=0.5)
        self.assertTrue(is_alive)
        th.join(timeout=1.0)

    def test_refresh_local_ports_status_turns_ghost_online_to_offline(self):
        """Проверяет, что фантомный статус 'online' на закрытом локальном порту (1085) сбрасывается в 'offline'."""
        proxies = [
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1015,
                "label": "System Proxy",
                "status": "online",
                "ip": "213.165.63.177",
                "country": "Latvia",
            },
            {
                "id": 6,
                "host": "127.0.0.1",
                "port": 1085,
                "label": "Claude: SOCKS5 :1085",
                "status": "online",  # Фантомный статус из старого кэша!
                "ip": "1.2.3.4",
                "country": "Germany",
                "latency_ms": 150,
            },
        ]

        # Эмулируем: порт 1015 открыт (True), порт 1085 закрыт (False)
        def mock_handshake(host, port, timeout=0.15):
            return port == 1015

        with patch("proxy_manager.check_socks5_handshake", side_effect=mock_handshake):
            changed = proxy_manager.refresh_local_ports_status(proxies)

        self.assertTrue(changed)

        # 1015 остался online
        p1015 = next(p for p in proxies if p["port"] == 1015)
        self.assertEqual(p1015["status"], "online")

        # 1085 сбросился в offline, данные очищены
        p1085 = next(p for p in proxies if p["port"] == 1085)
        self.assertEqual(p1085["status"], "offline")
        self.assertEqual(p1085["ip"], "-")
        self.assertEqual(p1085["country"], "undefined")
        self.assertIsNone(p1085["latency_ms"])

    def test_refresh_local_ports_status_turns_offline_to_online_when_alive(self):
        """Проверяет, что когда закрытый порт поднимается, статус обновляется в 'online'."""
        proxies = [
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1015,
                "label": "System Proxy",
                "status": "offline",  # Был offline
                "ip": "-",
                "country": "undefined",
            }
        ]

        with patch("proxy_manager.check_socks5_handshake", return_value=True):
            changed = proxy_manager.refresh_local_ports_status(proxies)

        self.assertTrue(changed)
        self.assertEqual(proxies[0]["status"], "online")

    def test_load_proxies_with_refresh_status_persists_changes(self):
        """Проверяет, что load_proxies(refresh_status=True) сохраняет актуализированное состояние."""
        test_data = [
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1085,
                "label": "SOCKS5 :1085",
                "status": "online",  # Фантомный статус
                "ip": "8.8.8.8",
                "country": "USA",
            }
        ]
        with open(self.test_proxies_file, "w", encoding="utf-8") as f:
            json.dump(test_data, f)

        # Порт 1085 закрыт
        with patch("proxy_manager.check_socks5_handshake", return_value=False):
            loaded = proxy_manager.load_proxies(refresh_status=True)

        self.assertEqual(loaded[0]["status"], "offline")

        # Проверяем, что в файле proxies.json тоже обновилось
        with open(self.test_proxies_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[0]["status"], "offline")

    @patch("claude_manager.get_claude_settings_files")
    def test_probe_claude_route_blocks_on_closed_1085_and_connects_on_open_1015(self, mock_files):
        """Проверяет, что Claude блокируется Killswitch на закрытом порте 1085 и подключается на открытом порте 1015."""
        mock_files.return_value = [self.test_claude_file]

        proxy_manager.save_proxies([
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1015,
                "label": "System Proxy",
                "status": "online",
                "ip": "213.165.63.177",
                "country": "Latvia",
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

        # Эмулируем: 1015 доступен, 1085 недоступен
        def mock_access(host, port, timeout=0.8):
            return port == 1015

        with patch("claude_manager.check_port_accessible", side_effect=mock_access), \
             patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            # 1. Проверяем 1085 (закрыт) -> Killswitch срабатывает
            res_1085 = claude_manager.probe_claude_route("127.0.0.1", 1085, killswitch=True)
            self.assertFalse(res_1085["online"])
            self.assertTrue(res_1085["killswitch_engaged"])
            self.assertIn("KILLSWITCH", res_1085["status_text"])

            # 2. Проверяем 1015 (открыт) -> Killswitch выключен, прокси онлайн
            res_1015 = claude_manager.probe_claude_route("127.0.0.1", 1015, killswitch=True)
            self.assertTrue(res_1015["online"])
            self.assertFalse(res_1015["killswitch_engaged"])
            self.assertEqual(res_1015["ip"], "213.165.63.177")
            self.assertEqual(res_1015["country"], "Latvia")
            self.assertIn("активен", res_1015["status_text"])


if __name__ == "__main__":
    unittest.main()
