"""Unit tests for Gemini profiles scrolling, activation fix, and token overwrite (reauth) functionality.
"""
import unittest
import tkinter as tk
from unittest.mock import patch, MagicMock
from pathlib import Path
import json

import gemini_manager
import token_vault_manager
import i18n
import config_app


class TestGeminiScrollAndReauth(unittest.TestCase):
    def test_i18n_keys_present(self):
        """Check all required translation keys exist in both RU and EN."""
        for lang in ("ru", "en"):
            self.assertIn("btn_reauth_token", i18n.TRANSLATIONS[lang])
            self.assertIn("dlg_reauth_title", i18n.TRANSLATIONS[lang])
            self.assertIn("dlg_reauth_confirm", i18n.TRANSLATIONS[lang])
            self.assertIn("msg_reauth_started", i18n.TRANSLATIONS[lang])

    def test_launch_add_account_terminal_overwrite(self):
        """Check that overwrite flag invokes gemini-oauth reauth in terminal."""
        with patch("subprocess.Popen") as mock_popen, patch("gemini_manager.reserve_new_profile_port"):
            ok = gemini_manager.launch_add_account_terminal("test-account", overwrite=True)
            self.assertTrue(ok)
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            cmd_str = args[-1]
            self.assertIn("gemini-oauth reauth test-account", cmd_str)

        with patch("subprocess.Popen") as mock_popen, patch("gemini_manager.reserve_new_profile_port"):
            ok = gemini_manager.launch_add_account_terminal("new-account", overwrite=False)
            self.assertTrue(ok)
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            cmd_str = args[-1]
            self.assertIn("gemini-oauth add new-account", cmd_str)

    def test_switch_profile_and_active_state(self):
        """Verify profile switching marks profile as active and updates active_profile.json."""
        profiles = gemini_manager.list_profiles()
        valid = [p for p in profiles if p.get("token_exists")]
        if len(valid) >= 2:
            target = valid[1]["profile_name"]
            with token_vault_manager.auto_unlock_context():
                ok, msg = gemini_manager.switch_profile(target)
            self.assertTrue(ok, msg)

            reloaded = gemini_manager.list_profiles()
            active_names = [p["profile_name"] for p in reloaded if p.get("is_active")]
            self.assertIn(target, active_names)

    def test_scrollable_canvas_structure(self):
        """Verify config_app contains scrollable canvas and mouse wheel handlers for gemini."""
        app = config_app.HerdrConfigApp()
        app.withdraw()
        try:
            self.assertTrue(hasattr(app, "gemini_canvas"))
            self.assertTrue(hasattr(app, "gemini_scrollbar"))
            self.assertTrue(hasattr(app, "gemini_cards_container"))
            self.assertTrue(hasattr(app, "on_reauth_gemini_profile"))
            self.assertTrue(hasattr(app, "_start_token_update_watcher"))
        finally:
            try:
                app.destroy()
            except Exception:
                pass

    def test_token_check_i18n_keys(self):
        """Check all required token verification keys exist in RU and EN."""
        for lang in ("ru", "en"):
            self.assertIn("btn_check_all_gemini", i18n.TRANSLATIONS[lang])
            self.assertIn("btn_check_gemini", i18n.TRANSLATIONS[lang])
            self.assertIn("dlg_check_results_title", i18n.TRANSLATIONS[lang])
            self.assertIn("dlg_token_check_title", i18n.TRANSLATIONS[lang])
            self.assertIn("status_token_works", i18n.TRANSLATIONS[lang])
            self.assertIn("status_token_broken", i18n.TRANSLATIONS[lang])

    def test_check_token_live(self):
        """Verify check_token_live works on existing profiles."""
        profiles = gemini_manager.list_profiles()
        if profiles:
            first_p = profiles[0]["profile_name"]
            res = gemini_manager.check_token_live(first_p)
            self.assertIsInstance(res, dict)
            self.assertIn("valid", res)
            self.assertIsInstance(res["valid"], bool)
            self.assertIn("status_text", res)
            self.assertIn("profile_name", res)

    def test_check_all_tokens_and_cancel(self):
        """Verify batch checking checks profiles and can be aborted with cancel_event."""
        import threading
        cancel_evt = threading.Event()
        cancel_evt.set() # pre-cancelled
        res = gemini_manager.check_all_tokens(cancel_event=cancel_evt)
        self.assertEqual(len(res), 0)

        # Run uncancelled check
        normal_cancel = threading.Event()
        res_normal = gemini_manager.check_all_tokens(cancel_event=normal_cancel)
        self.assertGreater(len(res_normal), 0)
        for r in res_normal:
            self.assertIn("valid", r)
            self.assertIsInstance(r["valid"], bool)
            self.assertIn("status_text", r)

    def test_gemini_check_ui_widgets(self):
        """Verify batch check widgets and methods exist on HerdrConfigApp."""
        app = config_app.HerdrConfigApp()
        app.withdraw()
        try:
            self.assertTrue(hasattr(app, "btn_check_all_gemini"))
            self.assertTrue(hasattr(app, "gemini_check_status_frame"))
            self.assertTrue(hasattr(app, "gemini_check_timer_lbl"))
            self.assertTrue(hasattr(app, "btn_cancel_gemini_check"))
            self.assertTrue(hasattr(app, "on_check_all_gemini_tokens"))
            self.assertTrue(hasattr(app, "on_cancel_gemini_check"))
            self.assertTrue(hasattr(app, "on_test_gemini_token"))
            self.assertTrue(hasattr(app, "_show_gemini_batch_results_dialog"))
        finally:
            try:
                app.destroy()
            except Exception:
                pass

    def test_batch_check_pops_up_when_page_left(self):
        """Verify modal results dialog is rendered properly even when user has navigated away from gemini tab."""
        app = config_app.HerdrConfigApp()
        app.withdraw()
        try:
            # 1. Switch to gemini tab
            app.switch_page("gemini")
            self.assertEqual(app.active_tab, "gemini")

            # 2. Simulate leaving gemini tab to routes
            app.switch_page("routes")
            self.assertEqual(app.active_tab, "routes")

            # 3. Simulate completion while on 'routes' tab via _on_gemini_batch_check_done
            mock_results = [
                {"position": 1, "email": "test1@gmail.com", "valid": True, "status_text": "✓ Токен работает", "port": 1081},
                {"position": 2, "email": "test2@gmail.com", "valid": False, "status_text": "✕ Токен не работает", "port": 1082, "error": "Expired"},
            ]
            app._on_gemini_batch_check_done(mock_results, was_cancelled=False)
            app.update()

            # 4. Verify a Toplevel modal exists and is viewable
            toplevels = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
            self.assertGreaterEqual(len(toplevels), 1)
            dlg = toplevels[-1]
            self.assertTrue(dlg.winfo_exists())

            # Destroy dialog
            dlg.destroy()
        finally:
            try:
                app.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()


