"""Unit tests for Claude profiles token check, batch verification, and account reauth (overwrite) functionality.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import claude_oauth_manager as com
import config_app
import i18n
import settings_manager


def write_test_credentials(path: Path, access: str | None, refresh: str | None, expires_in_s: int = 3600,
                           refresh_expires_in_s: int = 30 * 86400, sub: str = "pro") -> None:
    now_ms = int(time.time() * 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    oauth_data: dict = {
        "scopes": ["user:inference"],
        "subscriptionType": sub,
    }
    if access:
        oauth_data["accessToken"] = access
        oauth_data["expiresAt"] = now_ms + expires_in_s * 1000
    if refresh:
        oauth_data["refreshToken"] = refresh
        oauth_data["refreshTokenExpiresAt"] = now_ms + refresh_expires_in_s * 1000

    path.write_text(json.dumps({"claudeAiOauth": oauth_data}), encoding="utf-8")


def write_test_claude_json(path: Path, uuid: str, email: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "oauthAccount": {
            "accountUuid": uuid,
            "emailAddress": email,
            "displayName": email.split("@")[0],
        }
    }), encoding="utf-8")


class TestClaudeScrollAndReauth(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herdr_test_claude_reauth_"))
        self.home = self.tmp / "home"
        self.claude_dir = self.home / ".claude"
        self.claude_dir.mkdir(parents=True)

        self._orig = {k: getattr(com, k) for k in (
            "WSL_HOME", "CLAUDE_DIR", "ACTIVE_CREDENTIALS_FILE", "ACTIVE_CLAUDE_JSON", "PROFILES_DIR")}
        com.WSL_HOME = self.home
        com.CLAUDE_DIR = self.claude_dir
        com.ACTIVE_CREDENTIALS_FILE = self.claude_dir / ".credentials.json"
        com.ACTIVE_CLAUDE_JSON = self.home / ".claude.json"
        com.PROFILES_DIR = self.claude_dir / "oauth-profiles"
        com.PROFILES_DIR.mkdir(parents=True, exist_ok=True)

        self.proxy_host = "127.0.0.1"
        self.proxy_port = 1015
        self._patches = [
            patch.object(settings_manager, "get_claude_proxy_host", side_effect=lambda: self.proxy_host),
            patch.object(settings_manager, "get_claude_proxy_port", side_effect=lambda: self.proxy_port),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for k, v in self._orig.items():
            setattr(com, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_i18n_keys_present(self):
        """Check all required Claude translation keys exist in both RU and EN."""
        required_keys = (
            "btn_check_all_claude",
            "btn_reauth_claude",
            "dlg_claude_reauth_title",
            "dlg_claude_reauth_confirm",
            "msg_claude_reauth_started",
            "dlg_claude_check_results_title",
        )
        for lang in ("ru", "en"):
            for k in required_keys:
                self.assertIn(k, i18n.TRANSLATIONS[lang], f"Missing key '{k}' in lang '{lang}'")

    def test_launch_login_terminal_overwrite(self):
        """Check that overwrite flag allows re-authenticating existing profile."""
        profile_dir = com.PROFILES_DIR / "work-account"
        profile_dir.mkdir(parents=True)
        (profile_dir / com.CREDENTIALS_NAME).write_text("{}", encoding="utf-8")

        # Without overwrite: should reject
        with patch("claude_oauth_manager.is_claude_proxy_online", return_value=True):
            ok, msg = com.launch_login_terminal("work-account", overwrite=False)
            self.assertFalse(ok)
            self.assertIn("уже существует", msg)

        # With overwrite: should succeed and launch terminal
        with patch("claude_oauth_manager.is_claude_proxy_online", return_value=True), \
             patch("subprocess.Popen") as mock_popen:
            ok, msg = com.launch_login_terminal("work-account", overwrite=True)
            self.assertTrue(ok)
            self.assertIn("перезаписи", msg)
            mock_popen.assert_called_once()
            cmd_args = mock_popen.call_args[0][0]
            bash_script = cmd_args[-1]
            self.assertIn("claude auth login", bash_script)
            self.assertIn("work-account", bash_script)

            meta = json.loads((profile_dir / com.PROFILE_META_NAME).read_text(encoding="utf-8"))
            self.assertEqual(meta.get("source"), "reauth")

    def test_check_token_live_missing_and_expired(self):
        """Verify check_token_live handles missing files and expired tokens."""
        # Non-existent profile
        res_none = com.check_token_live("ghost")
        self.assertFalse(res_none["valid"])
        self.assertEqual(res_none["status_text"], "Токен отсутствует")

        # Profile with expired token
        p_dir = com.PROFILES_DIR / "expired-acc"
        write_test_credentials(p_dir / com.CREDENTIALS_NAME, "expired_access", "expired_refresh",
                               expires_in_s=-100, refresh_expires_in_s=-50)
        res_exp = com.check_token_live("expired-acc")
        self.assertFalse(res_exp["valid"])
        self.assertEqual(res_exp["status_text"], "Токен не работает")

    def test_check_token_live_online_api_success_and_fail(self):
        """Verify check_token_live calls Anthropic API and parses 200 vs 401."""
        p_dir = com.PROFILES_DIR / "valid-acc"
        write_test_credentials(p_dir / com.CREDENTIALS_NAME, "good_access", "good_refresh")
        write_test_claude_json(p_dir / com.PROFILE_CLAUDE_JSON, "uuid-123", "user@anthropic.com")

        # Mock API 200 response
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = {
            "account": {
                "email": "verified@anthropic.com",
                "email_address": "verified@anthropic.com"
            }
        }

        with patch("claude_oauth_manager.is_claude_proxy_online", return_value=True), \
             patch("requests.get", return_value=mock_resp_200) as mock_get:
            res = com.check_token_live("valid-acc", timeout=2.0)
            self.assertTrue(res["valid"])
            self.assertTrue(res["online_verified"])
            self.assertEqual(res["email"], "verified@anthropic.com")
            self.assertEqual(res["status_text"], "Токен работает")
            mock_get.assert_called_once()

        # Mock API 401 response
        mock_resp_401 = MagicMock()
        mock_resp_401.status_code = 401

        with patch("claude_oauth_manager.is_claude_proxy_online", return_value=True), \
             patch("requests.get", return_value=mock_resp_401):
            res_401 = com.check_token_live("valid-acc", timeout=2.0)
            self.assertFalse(res_401["valid"])
            self.assertEqual(res_401["status_text"], "Токен не работает")
            self.assertIn("401", res_401["error"])

    def test_check_all_tokens_batch_and_cancel(self):
        """Verify check_all_tokens runs batch checks and respects cancellation."""
        import threading

        p1 = com.PROFILES_DIR / "c1"
        write_test_credentials(p1 / com.CREDENTIALS_NAME, "token1", "refresh1")
        p2 = com.PROFILES_DIR / "c2"
        write_test_credentials(p2 / com.CREDENTIALS_NAME, "token2", "refresh2")

        # Pre-cancelled event
        cancel_evt = threading.Event()
        cancel_evt.set()
        res_cancelled = com.check_all_tokens(cancel_event=cancel_evt)
        self.assertEqual(len(res_cancelled), 0)

        # Normal run with progress callback
        progress_calls = []
        def on_prog(done, total, r):
            progress_calls.append((done, total, r["profile_name"]))

        with patch("claude_oauth_manager.is_claude_proxy_online", return_value=False):
            res = com.check_all_tokens(on_progress=on_prog)
            self.assertEqual(len(res), 2)
            self.assertEqual(len(progress_calls), 2)
            self.assertEqual(progress_calls[0][0], 1)
            self.assertEqual(progress_calls[1][0], 2)

    def test_config_app_claude_ui_elements(self):
        """Verify config_app has all necessary buttons, frames, and methods for Claude auth."""
        app = config_app.HerdrConfigApp()
        app.withdraw()
        try:
            self.assertTrue(hasattr(app, "btn_check_all_claude"))
            self.assertTrue(hasattr(app, "claude_check_status_frame"))
            self.assertTrue(hasattr(app, "claude_check_timer_lbl"))
            self.assertTrue(hasattr(app, "btn_cancel_claude_check"))
            self.assertTrue(hasattr(app, "on_reauth_claude_profile"))
            self.assertTrue(hasattr(app, "_start_claude_token_update_watcher"))
            self.assertTrue(hasattr(app, "on_test_claude_token"))
            self.assertTrue(hasattr(app, "on_check_all_claude_tokens"))
            self.assertTrue(hasattr(app, "_show_claude_batch_results_dialog"))
        finally:
            try:
                app.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
