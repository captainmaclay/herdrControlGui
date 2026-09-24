"""Unit and integration tests for Gemini OAuth profile management in Herdr Control Center."""

import base64
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import gemini_manager
import proxy_manager
import settings_manager


def create_dummy_jwt(email: str, name: str, exp_offset: int = 3600) -> str:
    """Creates a mock unsigned JWT with the given email, name, and expiration."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload_data = {
        "iss": "https://accounts.google.com",
        "email": email,
        "email_verified": True,
        "name": name,
        "exp": int(time.time()) + exp_offset,
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"dummy_signature").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def create_dummy_token_file(filepath: Path, email: str, name: str, exp_offset: int = 3600) -> None:
    """Writes a dummy antigravity-oauth-token file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    token_data = {
        "token": {
            "access_token": f"mock_access_{email}",
            "token_type": "Bearer",
            "refresh_token": f"mock_refresh_{email}",
        },
        "auth_method": "consumer",
        "id_token": create_dummy_jwt(email, name, exp_offset),
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)


class TestGeminiManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="herdr_test_")
        self.base_dir = Path(self.test_dir)
        self.cli_dir = self.base_dir / "antigravity-cli"
        self.profiles_dir = self.base_dir / "profiles"

        self.cli_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

        # Patch paths in gemini_manager
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

    def tearDown(self):
        gemini_manager.WSL_BASE_DIR = self.orig_wsl_base
        gemini_manager.CLI_DIR = self.orig_cli_dir
        gemini_manager.PROFILES_DIR = self.orig_profiles_dir
        gemini_manager.ACTIVE_TOKEN_FILE = self.orig_active_token
        gemini_manager.ACTIVE_PROXY_ENV_FILE = self.orig_active_proxy_env
        gemini_manager.ACTIVE_PROFILE_JSON_FILE = self.orig_active_profile_json

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_jwt_claims(self):
        jwt_str = create_dummy_jwt("test@example.com", "Test User")
        claims = gemini_manager.parse_jwt_claims(jwt_str)
        self.assertEqual(claims.get("email"), "test@example.com")
        self.assertEqual(claims.get("name"), "Test User")

        # Corrupted / empty claims
        self.assertEqual(gemini_manager.parse_jwt_claims(""), {})
        self.assertEqual(gemini_manager.parse_jwt_claims("not.a.valid.jwt"), {})

    def test_get_token_info(self):
        non_existent = self.profiles_dir / "nonexistent" / "antigravity-oauth-token"
        info = gemini_manager.get_token_info(non_existent)
        self.assertFalse(info["exists"])
        self.assertEqual(info["email"], "Нет файла")

        valid_token = self.profiles_dir / "test_prof" / "antigravity-oauth-token"
        create_dummy_token_file(valid_token, "alice@gmail.com", "Alice Tester", exp_offset=1800)
        info_valid = gemini_manager.get_token_info(valid_token)
        self.assertTrue(info_valid["exists"])
        self.assertEqual(info_valid["email"], "alice@gmail.com")
        self.assertEqual(info_valid["name"], "Alice Tester")
        self.assertFalse(info_valid["is_expired"])

    def test_sequential_port_uniqueness(self):
        p1 = self.profiles_dir / "account-1"
        p2 = self.profiles_dir / "account-2"
        p_custom = self.profiles_dir / "user@example.com"
        p1.mkdir()
        p2.mkdir()
        p_custom.mkdir()

        port1 = gemini_manager._calculate_default_sequential_port("account-1")
        port2 = gemini_manager._calculate_default_sequential_port("account-2")
        port_custom = gemini_manager._calculate_default_sequential_port("user@example.com")

        self.assertEqual(port1, 1081)
        self.assertEqual(port2, 1082)
        # Custom email profile must NOT collide with 1081 or 1082
        self.assertNotIn(port_custom, [port1, port2])
        self.assertGreaterEqual(port_custom, 1083)

    def test_auto_heal_recovers_missing_token_into_named_profile(self):
        # Scenario: User created profile folder 'bob@gmail.com' and 'account-2',
        # but active token was in ACTIVE_TOKEN_FILE.
        create_dummy_token_file(gemini_manager.ACTIVE_TOKEN_FILE, "bob@gmail.com", "Bob Builder")
        (self.profiles_dir / "bob@gmail.com").mkdir()
        (self.profiles_dir / "account-2").mkdir()

        # Before healing, bob@gmail.com folder has no token
        self.assertFalse((self.profiles_dir / "bob@gmail.com" / "antigravity-oauth-token").exists())

        # Calling list_profiles should auto-heal
        profiles = gemini_manager.list_profiles()

        # Check that bob@gmail.com now has the token and is active
        bob_token = self.profiles_dir / "bob@gmail.com" / "antigravity-oauth-token"
        self.assertTrue(bob_token.exists())
        bob_info = gemini_manager.get_token_info(bob_token)
        self.assertEqual(bob_info["email"], "bob@gmail.com")

        # Check list_profiles result
        bob_prof = next((p for p in profiles if p["profile_name"] == "bob@gmail.com"), None)
        self.assertIsNotNone(bob_prof)
        self.assertTrue(bob_prof["is_active"])
        self.assertTrue(bob_prof["token_exists"])

        # account-2 should be cleanly reported as not authorized, without breaking
        acc2_prof = next((p for p in profiles if p["profile_name"] == "account-2"), None)
        self.assertIsNotNone(acc2_prof)
        self.assertFalse(acc2_prof["is_active"])
        self.assertFalse(acc2_prof["token_exists"])
        self.assertEqual(acc2_prof["email"], "Не авторизован")

    def test_auto_heal_creates_account_1_if_empty(self):
        create_dummy_token_file(gemini_manager.ACTIVE_TOKEN_FILE, "initial@gmail.com", "Initial User")
        profiles = gemini_manager.list_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["profile_name"], "account-1")
        self.assertEqual(profiles[0]["email"], "initial@gmail.com")
        self.assertTrue(profiles[0]["is_active"])

    def test_switch_profile_and_preserve_active_token(self):
        # Setup Profile 1: Alice
        p1_dir = self.profiles_dir / "account-1"
        create_dummy_token_file(p1_dir / "antigravity-oauth-token", "alice@gmail.com", "Alice")

        # Setup Profile 2: Bob
        p2_dir = self.profiles_dir / "account-2"
        create_dummy_token_file(p2_dir / "antigravity-oauth-token", "bob@gmail.com", "Bob")

        # Set Alice as active initially
        shutil.copy2(p1_dir / "antigravity-oauth-token", gemini_manager.ACTIVE_TOKEN_FILE)
        gemini_manager.write_active_proxy_env(1081, "alice@gmail.com", profile_name="account-1")

        # Simulate agy refreshing Alice's token in ACTIVE_TOKEN_FILE
        create_dummy_token_file(gemini_manager.ACTIVE_TOKEN_FILE, "alice@gmail.com", "Alice Refreshed", exp_offset=7200)

        # Switch to Bob (account-2)
        ok, msg = gemini_manager.switch_profile("account-2")
        self.assertTrue(ok)
        self.assertIn("bob@gmail.com", msg)

        # 1. Alice's refreshed token must have been saved back into account-1 before switch!
        alice_saved = gemini_manager.get_token_info(p1_dir / "antigravity-oauth-token")
        self.assertEqual(alice_saved["name"], "Alice Refreshed")

        # 2. Active token must now be Bob
        active_now = gemini_manager.get_token_info(gemini_manager.ACTIVE_TOKEN_FILE)
        self.assertEqual(active_now["email"], "bob@gmail.com")

        # 3. active_proxy.env and active_profile.json must point to Bob
        self.assertEqual(gemini_manager.get_active_profile_email(), "bob@gmail.com")
        self.assertEqual(gemini_manager.get_active_profile_name(), "account-2")

        # Switch back to Alice (account-1)
        ok2, msg2 = gemini_manager.switch_profile("account-1")
        self.assertTrue(ok2)
        active_final = gemini_manager.get_token_info(gemini_manager.ACTIVE_TOKEN_FILE)
        self.assertEqual(active_final["email"], "alice@gmail.com")
        self.assertEqual(active_final["name"], "Alice Refreshed")

    def test_switch_profile_fails_on_empty_profile(self):
        (self.profiles_dir / "empty-profile").mkdir()
        ok, msg = gemini_manager.switch_profile("empty-profile")
        self.assertFalse(ok)
        self.assertIn("не найден", msg)

    def test_switch_next_profile_cycles_only_valid_tokens(self):
        p1 = self.profiles_dir / "account-1"
        p2 = self.profiles_dir / "account-2"
        p3_empty = self.profiles_dir / "account-3"

        create_dummy_token_file(p1 / "antigravity-oauth-token", "u1@gmail.com", "User 1")
        create_dummy_token_file(p2 / "antigravity-oauth-token", "u2@gmail.com", "User 2")
        p3_empty.mkdir()  # empty without token

        shutil.copy2(p1 / "antigravity-oauth-token", gemini_manager.ACTIVE_TOKEN_FILE)
        gemini_manager.write_active_proxy_env(1081, "u1@gmail.com", profile_name="account-1")

        # Rotate: account-1 -> account-2 (skips account-3!)
        ok, msg = gemini_manager.switch_next_profile()
        self.assertTrue(ok)
        self.assertEqual(gemini_manager.get_active_profile_name(), "account-2")

        # Rotate again: account-2 -> account-1
        ok2, msg2 = gemini_manager.switch_next_profile()
        self.assertTrue(ok2)
        self.assertEqual(gemini_manager.get_active_profile_name(), "account-1")

    def test_delete_profile(self):
        p1 = self.profiles_dir / "account-1"
        p2 = self.profiles_dir / "account-2"
        create_dummy_token_file(p1 / "antigravity-oauth-token", "u1@gmail.com", "User 1")
        create_dummy_token_file(p2 / "antigravity-oauth-token", "u2@gmail.com", "User 2")

        shutil.copy2(p1 / "antigravity-oauth-token", gemini_manager.ACTIVE_TOKEN_FILE)
        gemini_manager.write_active_proxy_env(1081, "u1@gmail.com", profile_name="account-1")

        # Delete active profile account-1
        ok, msg = gemini_manager.delete_profile("account-1")
        self.assertTrue(ok)
        self.assertFalse(p1.exists())

        # Should automatically switch active to account-2
        active_now = gemini_manager.get_token_info(gemini_manager.ACTIVE_TOKEN_FILE)
        self.assertEqual(active_now["email"], "u2@gmail.com")

    def test_rename_profile_success(self):
        p_dir = self.profiles_dir / "old-acc"
        create_dummy_token_file(p_dir / "antigravity-oauth-token", "rename_test@gmail.com", "Rename User")
        with open(p_dir / "profile_config.json", "w", encoding="utf-8") as f:
            json.dump({"profile_name": "old-acc", "port": 1085, "manual": True}, f)

        settings_manager.set_account_proxy_binding("old-acc", 1085, manual=True)
        shutil.copy2(p_dir / "antigravity-oauth-token", gemini_manager.ACTIVE_TOKEN_FILE)
        gemini_manager.write_active_proxy_env(1085, "rename_test@gmail.com", profile_name="old-acc")

        ok, msg = gemini_manager.rename_profile("old-acc", "super-account")
        self.assertTrue(ok)
        self.assertIn("super-account", msg)

        # Verify old folder is gone and new folder exists
        self.assertFalse(p_dir.exists())
        new_p_dir = self.profiles_dir / "super-account"
        self.assertTrue(new_p_dir.exists())
        self.assertTrue((new_p_dir / "antigravity-oauth-token").exists())

        # Verify profile_config.json updated
        with open(new_p_dir / "profile_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg.get("profile_name"), "super-account")
        self.assertEqual(cfg.get("port"), 1085)

        # Verify settings_manager binding migrated
        old_bind = settings_manager.get_account_proxy_binding("old-acc")
        self.assertIsNone(old_bind)
        new_bind = settings_manager.get_account_proxy_binding("super-account")
        self.assertIsNotNone(new_bind)
        self.assertEqual(new_bind.get("port"), 1085)

        # Verify active profile updated
        self.assertEqual(gemini_manager.get_active_profile_name(), "super-account")

    def test_rename_profile_validation(self):
        p_dir = self.profiles_dir / "valid-acc"
        create_dummy_token_file(p_dir / "antigravity-oauth-token", "valid@gmail.com", "Valid User")

        # Empty name
        ok, msg = gemini_manager.rename_profile("valid-acc", "   ")
        self.assertFalse(ok)
        self.assertIn("пустым", msg)

        # Invalid characters
        for bad in ["bad/name", "bad\\name", "bad:name", "bad*name", "bad?name", 'bad"name', "bad<name", "bad>name", "bad|name"]:
            ok, msg = gemini_manager.rename_profile("valid-acc", bad)
            self.assertFalse(ok)
            self.assertIn("недопустимые", msg)

        # Same name is a no-op success
        ok, msg = gemini_manager.rename_profile("valid-acc", "valid-acc")
        self.assertTrue(ok)

        # Existing target name
        (self.profiles_dir / "existing-acc").mkdir()
        ok, msg = gemini_manager.rename_profile("valid-acc", "existing-acc")
        self.assertFalse(ok)
        self.assertIn("уже существует", msg)

        # Non-existent source
        ok, msg = gemini_manager.rename_profile("does-not-exist", "new-name")
        self.assertFalse(ok)
        self.assertIn("не найден", msg)

    def test_rename_propagates_to_strategy_history(self):
        import strategy_manager
        # Add test event into strategy history
        strategy_manager.log_event(
            account_email="old_email@gmail.com",
            profile_name="old_profile_name",
            port=1081,
            ip="1.2.3.4",
            country="Germany",
            event="test_event",
            duration_seconds=300.0,
        )

        p_dir = self.profiles_dir / "old_profile_name"
        create_dummy_token_file(p_dir / "antigravity-oauth-token", "old_email@gmail.com", "User")
        ok, msg = gemini_manager.rename_profile("old_profile_name", "new_profile_name")
        self.assertTrue(ok)

        # Check that history now has new_profile_name
        history = strategy_manager.load_history()
        matching = [e for e in history if e.get("profile_name") == "new_profile_name"]
        self.assertGreaterEqual(len(matching), 1)

        # Check evaluate_account_strategy with profile_name
        tendencies = strategy_manager.get_account_tendencies()
        eval_res = strategy_manager.evaluate_account_strategy(
            "old_email@gmail.com", "Germany", tendencies, profile_name="new_profile_name"
        )
        self.assertIsNotNone(eval_res)

    def test_multiple_profiles_same_email_only_one_active(self):
        """Если два профиля содержат токен одного и того же аккаунта, активным должен быть ровно один."""
        p1 = self.profiles_dir / "prof-1"
        p2 = self.profiles_dir / "prof-2"
        create_dummy_token_file(p1 / "antigravity-oauth-token", "shared@gmail.com", "Shared User")
        create_dummy_token_file(p2 / "antigravity-oauth-token", "shared@gmail.com", "Shared User")

        shutil.copy2(p1 / "antigravity-oauth-token", gemini_manager.ACTIVE_TOKEN_FILE)
        gemini_manager.write_active_proxy_env(1081, "shared@gmail.com", profile_name="prof-1")

        profs = gemini_manager.list_profiles()
        p1_info = next(p for p in profs if p["profile_name"] == "prof-1")
        p2_info = next(p for p in profs if p["profile_name"] == "prof-2")

        self.assertTrue(p1_info["is_active"])
        self.assertFalse(p2_info["is_active"])

        # Switch to prof-2
        ok, msg = gemini_manager.switch_profile("prof-2")
        self.assertTrue(ok)

        profs2 = gemini_manager.list_profiles()
        p1_info2 = next(p for p in profs2 if p["profile_name"] == "prof-1")
        p2_info2 = next(p for p in profs2 if p["profile_name"] == "prof-2")

        self.assertFalse(p1_info2["is_active"])
        self.assertTrue(p2_info2["is_active"])


if __name__ == "__main__":
    unittest.main()


