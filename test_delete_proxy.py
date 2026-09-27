"""Тесты для функции delete_proxy и предотвращения воскрешения удаленных портов."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import proxy_manager


class TestDeleteProxy(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_proxies_file = proxy_manager.PROXIES_FILE
        self.orig_vless_file = proxy_manager.VLESS2SOCKS_INSTANCES_FILE

        self.test_proxies_file = Path(self.temp_dir) / "proxies.json"
        self.test_vless_file = Path(self.temp_dir) / "instances.json"

        proxy_manager.PROXIES_FILE = self.test_proxies_file
        proxy_manager.VLESS2SOCKS_INSTANCES_FILE = self.test_vless_file

        # Исходные данные
        initial_proxies = [
            {"id": 1, "host": "127.0.0.1", "port": 1015, "label": "System Proxy", "status": "online", "country": "Finland", "claude": True},
            {"id": 2, "host": "127.0.0.1", "port": 1081, "label": "Proxy 1", "status": "offline", "country": "undefined", "claude": False},
            {"id": 3, "host": "127.0.0.1", "port": 1085, "label": "Proxy 5", "status": "offline", "country": "undefined", "claude": False},
        ]
        with open(self.test_proxies_file, "w", encoding="utf-8") as f:
            json.dump(initial_proxies, f)

        initial_instances = [
            {"name": "System Proxy", "listen": "127.0.0.1:1015"},
            {"name": "Proxy 1", "listen": "127.0.0.1:1081"},
            {"name": "Proxy 5", "listen": "127.0.0.1:1085"},
        ]
        with open(self.test_vless_file, "w", encoding="utf-8") as f:
            json.dump(initial_instances, f)

    def tearDown(self):
        proxy_manager.PROXIES_FILE = self.orig_proxies_file
        proxy_manager.VLESS2SOCKS_INSTANCES_FILE = self.orig_vless_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_delete_proxy_removes_from_proxies_and_instances(self):
        """Удаление прокси удаляет его из proxies.json и instances.json, не давая ему воскреснуть."""
        proxies = proxy_manager.load_proxies()
        p1085 = next(p for p in proxies if p["port"] == 1085)

        ok = proxy_manager.delete_proxy(p1085["id"])
        self.assertTrue(ok)

        # Проверяем, что в proxies.json порт 1085 удалён
        after = proxy_manager.load_proxies()
        ports_after = [p["port"] for p in after]
        self.assertNotIn(1085, ports_after)
        self.assertEqual(ports_after, [1015, 1081])

        # Проверяем перенумерацию ID
        self.assertEqual([p["id"] for p in after], [1, 2])

        # Проверяем, что в instances.json порт 1085 также удалён
        with open(self.test_vless_file, "r", encoding="utf-8") as f:
            v_data = json.load(f)
        v_listens = [i.get("listen") for i in v_data]
        self.assertNotIn("127.0.0.1:1085", v_listens)
        self.assertEqual(v_listens, ["127.0.0.1:1015", "127.0.0.1:1081"])


if __name__ == "__main__":
    unittest.main()
