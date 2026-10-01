"""Автоматические тесты пакета herdrCenter.actions.

Покрывает:
1. Регистрацию действий и фабрику get_action / list_actions
2. Базовый фреймворк BaseAction и ActionResult
3. Действие install_claude_wsl (dry-run и реальное выполнение)
4. Действие killswitch_heal (проверка владельца, очистка конфигов, замер стабильности)
5. Действие wsl_isolation (проверка Zero-Leak, статус nftables/iptables)
6. Действие verify_connectivity (проверка локальных сокетов и роутинга Anthropic)
7. CLI интерфейс python -m actions
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from actions import (
    ACTIONS_REGISTRY,
    ActionResult,
    BaseAction,
    InstallClaudeWslAction,
    KillswitchHealAction,
    VerifyConnectivityAction,
    WslIsolationAction,
    get_action,
    list_actions,
)
from actions.cli import main as cli_main


class TestActionsRegistry(unittest.TestCase):
    def test_registry_contains_all_actions(self):
        expected_keys = {
            "install_claude_wsl",
            "killswitch_heal",
            "wsl_isolation",
            "verify_connectivity",
        }
        self.assertTrue(expected_keys.issubset(set(ACTIONS_REGISTRY.keys())))

    def test_list_actions(self):
        items = list_actions()
        self.assertGreaterEqual(len(items), 4)
        names = [item["name"] for item in items]
        self.assertIn("install_claude_wsl", names)
        self.assertIn("killswitch_heal", names)

    def test_get_action_factory(self):
        act = get_action("install_claude_wsl", dry_run=True)
        self.assertIsInstance(act, InstallClaudeWslAction)
        self.assertTrue(act.dry_run)

    def test_get_action_invalid_name(self):
        with self.assertRaises(KeyError):
            get_action("non_existent_action")


class TestBaseActionAndResult(unittest.TestCase):
    def test_action_result_lifecycle(self):
        res = ActionResult(action_name="test_act")
        self.assertTrue(res.success)
        self.assertEqual(len(res.steps), 0)

        res.add_step("step1", "ok", "Step 1 OK", {"param": 1})
        self.assertTrue(res.success)
        self.assertEqual(len(res.steps), 1)

        res.add_step("step2", "failed", "Step 2 Failed")
        self.assertFalse(res.success)
        self.assertIn("Step 2 Failed", res.errors)

        res.finish()
        self.assertGreaterEqual(res.duration_seconds, 0.0)

        d = res.to_dict()
        self.assertEqual(d["action"], "test_act")
        self.assertFalse(d["success"])
        self.assertEqual(len(d["steps"]), 2)

        summary = res.summary_str()
        self.assertIn("[FAILED]", summary)
        self.assertIn("step1", summary)
        self.assertIn("step2", summary)

    def test_base_action_execute_step(self):
        class DummyAction(BaseAction):
            name = "dummy"

        act = DummyAction(dry_run=True)
        res = ActionResult(action_name=act.name)

        # Successful step
        def ok_fn():
            return "ok", "All good", {"val": 42}

        act.execute_step(res, "step_ok", ok_fn)
        self.assertTrue(res.success)
        self.assertEqual(res.steps[0]["status"], "ok")

        # Failing fatal step
        def fail_fn():
            raise ValueError("Test failure")

        with self.assertRaises(ValueError):
            act.execute_step(res, "step_fail", fail_fn, fatal=True)
        self.assertFalse(res.success)


class TestInstallClaudeWslAction(unittest.TestCase):
    def test_dry_run_execution(self):
        action = InstallClaudeWslAction(dry_run=True)
        res = action.run()
        self.assertTrue(res.success)
        self.assertTrue(res.dry_run)
        step_names = [s["step"] for s in res.steps]
        self.assertIn("configure_wslconfig", step_names)
        self.assertIn("configure_wsl_proxy_env", step_names)
        self.assertIn("setup_xdg_open_bridge", step_names)
        self.assertIn("setup_claude_gui_wrapper", step_names)
        self.assertIn("create_desktop_shortcuts", step_names)

    def test_real_idempotent_execution(self):
        # Реальный прогон должен завершаться успешно и идемпотентно
        action = InstallClaudeWslAction(
            socks_port=1015,
            http_port=11015,
            dry_run=False,
        )
        res = action.run(install_node=False, install_npm_cli=False)
        self.assertTrue(res.success)
        self.assertGreaterEqual(len(res.steps), 4)


class TestKillswitchHealAction(unittest.TestCase):
    def test_dry_run_execution(self):
        action = KillswitchHealAction(target_port=1015, dry_run=True)
        res = action.run()
        self.assertTrue(res.success)
        step_names = [s["step"] for s in res.steps]
        self.assertIn("inspect_port_owner", step_names)
        self.assertIn("clean_legacy_configs", step_names)
        self.assertIn("reset_isolation_cache", step_names)
        self.assertIn("verify_flapping_stability", step_names)

    def test_clean_legacy_configs(self):
        test_runtime = Path(r"C:\MyFiles\vless2socks\runtime")
        action = KillswitchHealAction(vless_dir=Path(r"C:\MyFiles\vless2socks"), dry_run=True)
        status, msg, details = action._clean_legacy_configs()
        self.assertEqual(status, "ok")

    def test_stability_check_on_stable_port(self):
        action = KillswitchHealAction(target_port=1015, dry_run=False)
        status, msg, details = action._verify_flapping_stability(samples=3, interval=0.1)
        self.assertEqual(status, "ok")
        self.assertTrue(details.get("stable"))


class TestWslIsolationAction(unittest.TestCase):
    def test_dry_run_execution(self):
        action = WslIsolationAction(dry_run=True)
        res = action.run(action_mode="verify")
        self.assertTrue(res.success)
        step_names = [s["step"] for s in res.steps]
        self.assertIn("verify_isolation_status", step_names)
        self.assertIn("verify_zero_leak", step_names)

    def test_real_verify_zero_leak(self):
        # Проверяем, что сетевая изоляция реально активна в WSL2
        action = WslIsolationAction(dry_run=False)
        res = action.run(action_mode="verify")
        self.assertTrue(res.success)
        status_step = next(s for s in res.steps if s["step"] == "verify_isolation_status")
        self.assertTrue(status_step["details"]["active"])


class TestVerifyConnectivityAction(unittest.TestCase):
    def test_dry_run_execution(self):
        action = VerifyConnectivityAction(dry_run=True)
        res = action.run()
        self.assertTrue(res.success)

    def test_real_connectivity(self):
        action = VerifyConnectivityAction(socks_port=1015, http_port=11015, dry_run=False)
        res = action.run()
        self.assertTrue(res.success)
        sockets_step = next(s for s in res.steps if s["step"] == "check_local_sockets")
        self.assertTrue(sockets_step["details"]["socks_open"])
        self.assertTrue(sockets_step["details"]["http_open"])


class TestActionsCLI(unittest.TestCase):
    def test_cli_list_json(self):
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            rc = cli_main(["list", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(buffer.getvalue())
            self.assertIsInstance(data, list)
            self.assertGreaterEqual(len(data), 4)
        finally:
            sys.stdout = old_stdout

    def test_cli_dry_run_action(self):
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            rc = cli_main(["install_claude_wsl", "--dry-run", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(buffer.getvalue())
            self.assertTrue(data["success"])
            self.assertTrue(data["dry_run"])
        finally:
            sys.stdout = old_stdout


if __name__ == "__main__":
    unittest.main()
