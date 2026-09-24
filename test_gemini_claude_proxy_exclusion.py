"""Тесты для изоляции прокси: прокси с флагом Claude строго не должны использоваться в аккаунтах Gemini.

Проверяет:
1. is_port_available_for_gemini():
   - Порт 1015 (по умолчанию Claude) недоступен для Gemini (False).
   - Любой порт с флагом claude: True недоступен (False).
   - Порты с claude: False доступны (True).
2. _calculate_default_sequential_port():
   - Пропуск портов Claude (если 1081 Claude, то account-1 получает 1082).
   - Никакой аккаунт Gemini никогда не получает порт 1015 или любой другой с claude=True.
3. set_profile_manual_port():
   - Попытка привязать порт с флагом Claude отклоняется (False) с сообщением об ошибке.
   - Привязка валидного порта без флага Claude проходит успешно.
4. get_profile_binding_info():
   - Если в конфиге остался порт, который получил флаг Claude, он аннулируется, и возвращается альтернативный non-Claude порт.
5. reset_all_profiles_to_sequential():
   - Расставляет порты строго по порядку, перешагивая порты с флагом Claude.
6. reassign_profiles_using_claude_proxies():
   - Автоматически выявляет профили на портах Claude и мигрирует их на свободные non-Claude порты.
7. find_best_fallback_proxy():
   - Auto-Failover для Gemini никогда не выбирает прокси с флагом Claude.
8. get_proxy_choices():
   - Список выбора прокси для аккаунтов Gemini исключает прокси с флагом Claude.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gemini_manager
import proxy_manager
import settings_manager


class TestGeminiClaudeProxyExclusion(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="herdr_gemini_claude_")
        self.base_dir = Path(self.test_dir)
        self.cli_dir = self.base_dir / "antigravity-cli"
        self.profiles_dir = self.base_dir / "profiles"

        self.cli_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

        self.orig_wsl_base = gemini_manager.WSL_BASE_DIR
        self.orig_cli_dir = gemini_manager.CLI_DIR
        self.orig_profiles_dir = gemini_manager.PROFILES_DIR
        self.orig_active_token = gemini_manager.ACTIVE_TOKEN_FILE
        self.orig_active_proxy_env = gemini_manager.ACTIVE_PROXY_ENV_FILE
        self.orig_active_profile_json = gemini_manager.ACTIVE_PROFILE_JSON_FILE

        gemini_manager.WSL_BASE_DIR = self.base_dir
        gemini_manager.CLI_DIR = self.cli_dir
        gemini_manager.PROFILES_DIR = self.profiles_dir
        gemini_manager.ACTIVE_TOKEN_FILE = self.cli_dir / "antigravity-oauth-token"
        gemini_manager.ACTIVE_PROXY_ENV_FILE = self.cli_dir / "active_proxy.env"
        gemini_manager.ACTIVE_PROFILE_JSON_FILE = self.cli_dir / "active_profile.json"

        self.orig_settings_file = settings_manager.SETTINGS_FILE
        self.orig_proxies_file = proxy_manager.PROXIES_FILE

        self.test_settings_file = self.base_dir / "settings.json"
        self.test_proxies_file = self.base_dir / "proxies.json"

        settings_manager.SETTINGS_FILE = self.test_settings_file
        proxy_manager.PROXIES_FILE = self.test_proxies_file

        # Инициализируем тестовый proxies.json
        # 1015: System Proxy (claude=True)
        # 1081: Finland 1 (claude=False)
        # 1082: Finland 2 (claude=True - симулируем выбор юзером)
        # 1083: France (claude=False)
        # 1084: Finland 3 (claude=False)
        # 1085: Claude (claude=True)
        self.initial_proxies = [
            {"id": 1, "host": "127.0.0.1", "port": 1015, "label": "System Proxy", "status": "online", "ip": "1.1.1.1", "country": "Latvia", "claude": True},
            {"id": 2, "host": "127.0.0.1", "port": 1081, "label": "Finland 1", "status": "online", "ip": "2.2.2.2", "country": "Finland", "claude": False},
            {"id": 3, "host": "127.0.0.1", "port": 1082, "label": "Finland 2", "status": "online", "ip": "3.3.3.3", "country": "Finland", "claude": True},
            {"id": 4, "host": "127.0.0.1", "port": 1083, "label": "France", "status": "online", "ip": "4.4.4.4", "country": "France", "claude": False},
            {"id": 5, "host": "127.0.0.1", "port": 1084, "label": "Finland 3", "status": "online", "ip": "5.5.5.5", "country": "Finland", "claude": False},
            {"id": 6, "host": "127.0.0.1", "port": 1085, "label": "Claude Port", "status": "online", "ip": "6.6.6.6", "country": "undefined", "claude": True},
        ]
        proxy_manager.save_proxies(self.initial_proxies)

        settings_manager.save_settings({
            "language": "ru",
            "auto_proxy_failover": True,
            "account_proxy_bindings": {},
        })

    def tearDown(self):
        gemini_manager.WSL_BASE_DIR = self.orig_wsl_base
        gemini_manager.CLI_DIR = self.orig_cli_dir
        gemini_manager.PROFILES_DIR = self.orig_profiles_dir
        gemini_manager.ACTIVE_TOKEN_FILE = self.orig_active_token
        gemini_manager.ACTIVE_PROXY_ENV_FILE = self.orig_active_proxy_env
        gemini_manager.ACTIVE_PROFILE_JSON_FILE = self.orig_active_profile_json

        settings_manager.SETTINGS_FILE = self.orig_settings_file
        proxy_manager.PROXIES_FILE = self.orig_proxies_file

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_is_port_available_for_gemini(self):
        """Проверяет фильтрацию портов: порты Claude недоступны для Gemini."""
        self.assertFalse(gemini_manager.is_port_available_for_gemini(1015), "1015 (System Proxy Claude) должен быть недоступен")
        self.assertTrue(gemini_manager.is_port_available_for_gemini(1081), "1081 (claude=False) должен быть доступен")
        self.assertFalse(gemini_manager.is_port_available_for_gemini(1082), "1082 (claude=True) должен быть недоступен")
        self.assertTrue(gemini_manager.is_port_available_for_gemini(1083), "1083 (claude=False) должен быть доступен")
        self.assertFalse(gemini_manager.is_port_available_for_gemini(1085), "1085 (claude=True) должен быть недоступен")

    def test_calculate_default_sequential_port_skips_claude_proxies(self):
        """Проверяет, что автоматический расчет портов Gemini перешагивает порты Claude."""
        for i in range(1, 5):
            (self.profiles_dir / f"account-{i}").mkdir()

        # 1081 доступен -> account-1 получает 1081
        p1 = gemini_manager._calculate_default_sequential_port("account-1")
        self.assertEqual(p1, 1081)

        # 1082 занят Claude -> account-2 должен перешагнуть на 1083!
        p2 = gemini_manager._calculate_default_sequential_port("account-2")
        self.assertEqual(p2, 1083)

        # account-3 должен получить 1084 (следующий non-Claude)
        p3 = gemini_manager._calculate_default_sequential_port("account-3")
        self.assertEqual(p3, 1084)

        # 1085 занят Claude -> account-4 должен перешагнуть на 1086!
        p4 = gemini_manager._calculate_default_sequential_port("account-4")
        self.assertEqual(p4, 1086)

    def test_set_profile_manual_port_blocks_claude_proxies(self):
        """Попытка назначить порт Claude для профиля Gemini должна отклоняться."""
        (self.profiles_dir / "account-1").mkdir(parents=True, exist_ok=True)

        # 1. Попытка назначить 1015 (System Proxy Claude)
        ok1, msg1 = gemini_manager.set_profile_manual_port("account-1", 1015)
        self.assertFalse(ok1)
        self.assertIn("отмечен флагом Claude", msg1)

        # 2. Попытка назначить 1082 (который помечен claude=True)
        ok2, msg2 = gemini_manager.set_profile_manual_port("account-1", 1082)
        self.assertFalse(ok2)
        self.assertIn("отмечен флагом Claude", msg2)

        # 3. Назначение свободного порта 1083 (claude=False)
        ok3, msg3 = gemini_manager.set_profile_manual_port("account-1", 1083)
        self.assertTrue(ok3)
        self.assertIn("вручную привязан", msg3)

    def test_get_profile_binding_info_discards_stale_claude_binding(self):
        """Если в конфигурации был вручную забинден порт, который стал Claude, он аннулируется."""
        prof_name = "test-account"
        prof_dir = self.profiles_dir / prof_name
        prof_dir.mkdir(parents=True, exist_ok=True)

        # Изначально привязали 1082 (который у нас claude=True)
        settings_manager.set_account_proxy_binding(prof_name, 1082, manual=True)

        port, is_manual = gemini_manager.get_profile_binding_info(prof_name)
        # Биндинг к 1082 должен быть сброшен, и выдан доступный non-Claude порт
        self.assertNotEqual(port, 1082)
        self.assertFalse(is_manual)
        self.assertTrue(gemini_manager.is_port_available_for_gemini(port))

    def test_reset_all_profiles_to_sequential_skips_claude_ports(self):
        """reset_all_profiles_to_sequential расставляет профили, не затрагивая порты Claude."""
        for name in ["account-1", "account-2", "account-3"]:
            (self.profiles_dir / name).mkdir(parents=True, exist_ok=True)

        ok, msg = gemini_manager.reset_all_profiles_to_sequential()
        self.assertTrue(ok)

        port1 = gemini_manager.get_profile_port("account-1")
        port2 = gemini_manager.get_profile_port("account-2")
        port3 = gemini_manager.get_profile_port("account-3")

        self.assertEqual(port1, 1081)
        self.assertEqual(port2, 1083)  # 1082 пропущен
        self.assertEqual(port3, 1084)

    def test_reassign_profiles_using_claude_proxies(self):
        """Проверяет автоматическую миграцию профилей при включении флага Claude."""
        p_dir = self.profiles_dir / "account-1"
        p_dir.mkdir(parents=True, exist_ok=True)
        with open(p_dir / "profile_config.json", "w", encoding="utf-8") as f:
            json.dump({"profile_name": "account-1", "port": 1082, "manual": True}, f)
        settings_manager.set_account_proxy_binding("account-1", 1082, manual=True)

        # Вызываем проверку и переназначение
        migrated = gemini_manager.reassign_profiles_using_claude_proxies()
        self.assertEqual(len(migrated), 1)
        self.assertIn("account-1", migrated[0])

        new_port = gemini_manager.get_profile_port("account-1")
        self.assertNotEqual(new_port, 1082)
        self.assertTrue(gemini_manager.is_port_available_for_gemini(new_port))

    def test_find_best_fallback_proxy_never_selects_claude_proxy(self):
        """Auto-Failover Gemini никогда не выбирает прокси с флагом Claude, даже из той же страны."""
        # Предположим сбой на 1081 (Finland).
        # В списке есть:
        # - 1082 (Finland, claude=True) -> НЕ ДОЛЖЕН ВЫБРАТЬСЯ
        # - 1084 (Finland, claude=False) -> ДОЛЖЕН ВЫБРАТЬСЯ
        with patch("proxy_manager.check_port_accessible", return_value=True), \
             patch("proxy_manager.probe_single_proxy") as mock_probe, \
             patch("requests.get") as mock_get:
            
            mock_probe.side_effect = lambda cand, timeout=3.0: {**cand, "status": "online"}
            mock_get.return_value.status_code = 200

            best = proxy_manager.find_best_fallback_proxy(failed_port=1081, preferred_country="Finland")
            self.assertIsNotNone(best)
            self.assertEqual(best.get("port"), 1084, "Должен быть выбран 1084, а не 1082 с флагом Claude")
            self.assertFalse(best.get("claude", False))

    def test_get_proxy_choices_excludes_claude_proxies(self):
        """Меню выбора прокси для Gemini исключает порты с флагом Claude."""
        choices = proxy_manager.get_proxy_choices(exclude_claude=True)
        choice_ports = [c["port"] for c in choices]

        self.assertNotIn(1015, choice_ports, "1015 не должен присутствовать в выборе Gemini")
        self.assertNotIn(1082, choice_ports, "1082 (Claude) не должен присутствовать в выборе Gemini")
        self.assertNotIn(1085, choice_ports, "1085 (Claude) не должен присутствовать в выборе Gemini")

        self.assertIn(1081, choice_ports)
        self.assertIn(1083, choice_ports)
        self.assertIn(1084, choice_ports)


if __name__ == "__main__":
    unittest.main()
