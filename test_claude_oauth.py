"""Тесты вкладки Claude OAuth, исправления портов Gemini и бэкапа профилей Claude.

Проверяет:
1. claude_oauth_manager:
   - Сроки действия access/refresh токенов (относительные и абсолютные).
   - Определение активного профиля и обратную синхронизацию обновлённых токенов.
   - Сохранение активного аккаунта, переключение, удаление, переименование.
   - Все сетевые действия идут строго через Anthropic Claude Proxy со страницы Routes,
     вход блокируется при недоступном прокси, ротации портов нет.
2. gemini_manager:
   - Новый 4-й профиль получает 1084, если 1081-1083 заняты (в т.ч. профилями с email-именами).
   - Дубликаты портов устраняются без перестановки уже назначенных портов.
   - Абсолютное время истечения OAuth2 токена в format_expiry.
3. backup_manager:
   - Профили Claude OAuth и порт Claude Proxy экспортируются и импортируются.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import backup_manager
import claude_oauth_manager as com
import gemini_manager
import settings_manager


def write_credentials(path: Path, access: str, refresh: str, expires_in_s: int = 3600,
                      refresh_expires_in_s: int = 30 * 86400, sub: str = "pro") -> None:
    now_ms = int(time.time() * 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": access,
            "refreshToken": refresh,
            "expiresAt": now_ms + expires_in_s * 1000,
            "refreshTokenExpiresAt": now_ms + refresh_expires_in_s * 1000,
            "scopes": ["user:inference"],
            "subscriptionType": sub,
        }
    }), encoding="utf-8")


def write_claude_json(path: Path, uuid: str, email: str, extra: dict | None = None) -> None:
    data = dict(extra or {})
    data["oauthAccount"] = {"accountUuid": uuid, "emailAddress": email, "displayName": email.split("@")[0]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class ClaudeOAuthTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herdr_claude_oauth_"))
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

    def make_profile(self, name: str, uuid: str, email: str, access: str, refresh: str, **kw) -> Path:
        d = com.PROFILES_DIR / name
        write_credentials(d / ".credentials.json", access, refresh, **kw)
        write_claude_json(d / ".claude.json", uuid, email)
        return d

    def make_active(self, uuid: str, email: str, access: str, refresh: str) -> None:
        write_credentials(com.ACTIVE_CREDENTIALS_FILE, access, refresh)
        write_claude_json(com.ACTIVE_CLAUDE_JSON, uuid, email, extra={"numStartups": 5})


class TestClaudeOAuthExpiry(unittest.TestCase):
    def test_format_ms_expiry_valid_contains_absolute_time(self):
        exp_ms = (time.time() + 2 * 3600 + 120) * 1000
        text, expired = com.format_ms_expiry(exp_ms)
        self.assertFalse(expired)
        self.assertIn("Действителен (2ч", text)
        self.assertIn(time.strftime("%d.%m.%Y", time.localtime(exp_ms / 1000)), text)
        self.assertIn(" • до ", text)

    def test_format_ms_expiry_days(self):
        text, expired = com.format_ms_expiry((time.time() + 5 * 86400 + 3600 * 3 + 60) * 1000)
        self.assertFalse(expired)
        self.assertIn("5д 3ч", text)

    def test_format_ms_expiry_expired(self):
        text, expired = com.format_ms_expiry((time.time() - 60) * 1000)
        self.assertTrue(expired)
        self.assertTrue(text.startswith("Истёк "))

    def test_format_ms_expiry_missing(self):
        self.assertEqual(com.format_ms_expiry(None), ("Не определено", False))


class TestClaudeOAuthProfiles(ClaudeOAuthTestBase):
    def test_credentials_info_access_expired_but_refresh_alive_is_usable(self):
        f = self.tmp / "c.json"
        write_credentials(f, "a", "r", expires_in_s=-60)
        info = com.get_credentials_info(f)
        self.assertTrue(info["access_expired"])
        self.assertFalse(info["is_expired"], "Пока жив refresh-токен, профиль пригоден")
        self.assertEqual(info["subscription"], "pro")

    def test_credentials_info_missing_file(self):
        info = com.get_credentials_info(self.tmp / "nope.json")
        self.assertFalse(info["exists"])

    def test_list_profiles_marks_active_by_account_uuid_and_hides_tokens(self):
        self.make_profile("alice", "uuid-a", "alice@x.com", "acc-a", "ref-a")
        self.make_profile("bob", "uuid-b", "bob@x.com", "acc-b", "ref-b")
        self.make_active("uuid-b", "bob@x.com", "acc-b", "ref-b")

        profiles = {p["profile_name"]: p for p in com.list_profiles()}
        self.assertFalse(profiles["alice"]["is_active"])
        self.assertTrue(profiles["bob"]["is_active"])
        self.assertEqual(profiles["bob"]["email"], "bob@x.com")
        self.assertEqual(profiles["bob"]["proxy_port"], 1015)
        self.assertEqual(profiles["bob"]["proxy_http_port"], 11015)
        self.assertIn("до", profiles["bob"]["expiry_text"])
        for p in profiles.values():
            self.assertNotIn("access_token", p)
            self.assertNotIn("refresh_token", p)

    def test_refreshed_active_tokens_are_synced_back_to_profile(self):
        self.make_profile("bob", "uuid-b", "bob@x.com", "acc-old", "ref-old")
        # Claude Code обновил токены (refresh-токен ротировался)
        self.make_active("uuid-b", "bob@x.com", "acc-new", "ref-new")

        self.assertEqual(com.sync_active_back_to_profile(), "bob")
        data = json.loads((com.PROFILES_DIR / "bob" / ".credentials.json").read_text(encoding="utf-8"))
        self.assertEqual(data["claudeAiOauth"]["refreshToken"], "ref-new")

    def test_save_active_as_profile(self):
        self.make_active("uuid-a", "alice@x.com", "acc-a", "ref-a")
        ok, msg = com.save_active_as_profile("alice@x.com")
        self.assertTrue(ok, msg)
        prof = com.PROFILES_DIR / "alice@x.com"
        self.assertTrue((prof / ".credentials.json").exists())
        meta = json.loads((prof / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["oauthAccount"]["accountUuid"], "uuid-a")

        # Повторное сохранение того же аккаунта отклоняется
        ok2, _ = com.save_active_as_profile("other")
        self.assertFalse(ok2)
        active = com.get_active_status()
        self.assertTrue(active["saved"])
        self.assertEqual(active["profile_name"], "alice@x.com")

    def test_save_active_rejects_bad_names(self):
        self.make_active("uuid-a", "alice@x.com", "acc-a", "ref-a")
        for bad in ("", "../evil", "a b", "x/y"):
            ok, _ = com.save_active_as_profile(bad)
            self.assertFalse(ok, bad)

    def test_switch_profile_updates_credentials_and_account_and_preserves_previous(self):
        self.make_profile("alice", "uuid-a", "alice@x.com", "acc-a", "ref-a")
        self.make_profile("bob", "uuid-b", "bob@x.com", "acc-b-old", "ref-b-old")
        self.make_active("uuid-b", "bob@x.com", "acc-b-new", "ref-b-new")

        ok, msg = com.switch_profile("alice")
        self.assertTrue(ok, msg)
        self.assertIn(":1015", msg)

        active = json.loads(com.ACTIVE_CREDENTIALS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(active["claudeAiOauth"]["accessToken"], "acc-a")
        claude_json = json.loads(com.ACTIVE_CLAUDE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(claude_json["oauthAccount"]["accountUuid"], "uuid-a")
        self.assertEqual(claude_json["numStartups"], 5, "Остальные поля ~/.claude.json сохраняются")

        # Свежие токены Bob сохранены в его профиль до переключения
        bob = json.loads((com.PROFILES_DIR / "bob" / ".credentials.json").read_text(encoding="utf-8"))
        self.assertEqual(bob["claudeAiOauth"]["refreshToken"], "ref-b-new")
        self.assertTrue((self.claude_dir / ".credentials.json.bak_herdr").exists())

        profiles = {p["profile_name"]: p for p in com.list_profiles()}
        self.assertTrue(profiles["alice"]["is_active"])
        self.assertFalse(profiles["bob"]["is_active"])

    def test_switch_to_fully_expired_profile_is_refused(self):
        self.make_profile("old", "uuid-o", "o@x.com", "a", "r", expires_in_s=-10, refresh_expires_in_s=-10)
        ok, _ = com.switch_profile("old")
        self.assertFalse(ok)

    def test_delete_active_profile_refused_and_standby_allowed(self):
        self.make_profile("alice", "uuid-a", "alice@x.com", "acc-a", "ref-a")
        self.make_profile("bob", "uuid-b", "bob@x.com", "acc-b", "ref-b")
        self.make_active("uuid-a", "alice@x.com", "acc-a", "ref-a")

        ok, _ = com.delete_profile("alice")
        self.assertFalse(ok)
        ok, _ = com.delete_profile("bob")
        self.assertTrue(ok)
        self.assertFalse((com.PROFILES_DIR / "bob").exists())

    def test_rename_profile(self):
        self.make_profile("alice", "uuid-a", "alice@x.com", "acc-a", "ref-a")
        ok, msg = com.rename_profile("alice", "work")
        self.assertTrue(ok, msg)
        self.assertTrue((com.PROFILES_DIR / "work" / ".credentials.json").exists())
        ok, _ = com.rename_profile("work", "../bad")
        self.assertFalse(ok)

    def test_suggest_profile_name(self):
        self.assertEqual(com.suggest_profile_name(), "claude-1")
        (com.PROFILES_DIR / "claude-1").mkdir(parents=True)
        self.assertEqual(com.suggest_profile_name(), "claude-2")


class TestClaudeOAuthProxyStrict(ClaudeOAuthTestBase):
    def test_proxy_follows_routes_page_setting_without_rotation(self):
        self.assertEqual(com.get_claude_oauth_proxy()["port"], 1015)
        self.proxy_port = 1090
        px = com.get_claude_oauth_proxy()
        self.assertEqual(px["port"], 1090)
        self.assertEqual(px["http_url"], "http://127.0.0.1:11090")
        self.assertEqual(px["socks_url"], "socks5h://127.0.0.1:1090")

    def test_login_command_uses_only_claude_proxy_and_isolated_config_dir(self):
        cmd = com.build_login_bash_command("work")
        self.assertIn("HTTPS_PROXY='http://127.0.0.1:11015'", cmd)
        self.assertIn("HTTP_PROXY='http://127.0.0.1:11015'", cmd)
        self.assertIn("ALL_PROXY='socks5h://127.0.0.1:1015'", cmd)
        self.assertIn("NO_PROXY=''", cmd)
        self.assertIn(f"CLAUDE_CONFIG_DIR='{com.WSL_PROFILES_PATH}/work'", cmd)
        self.assertIn("claude auth login", cmd)
        self.assertNotIn("1081", cmd)

    def test_login_blocked_when_claude_proxy_offline(self):
        with patch.object(com.claude_manager, "check_port_accessible", return_value=False), \
                patch.object(com.subprocess, "Popen") as popen:
            ok, msg = com.launch_login_terminal("work")
        self.assertFalse(ok)
        self.assertIn("1015", msg)
        popen.assert_not_called()

    def test_login_launches_terminal_when_proxy_online(self):
        with patch.object(com.claude_manager, "check_port_accessible", return_value=True) as chk, \
                patch.object(com.subprocess, "Popen") as popen:
            ok, msg = com.launch_login_terminal("work")
        self.assertTrue(ok, msg)
        chk.assert_called_with("127.0.0.1", 1015, timeout=1.0)
        args = popen.call_args[0][0]
        self.assertIn("claude auth login", args[-1])
        self.assertIn("11015", args[-1])
        meta = json.loads((com.PROFILES_DIR / "work" / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["proxy_port"], 1015)

    def test_login_rejects_invalid_or_existing_profile(self):
        with patch.object(com.subprocess, "Popen") as popen:
            ok, _ = com.launch_login_terminal("bad name; rm -rf")
            self.assertFalse(ok)
            self.make_profile("taken", "u", "t@x.com", "a", "r")
            ok, _ = com.launch_login_terminal("taken")
            self.assertFalse(ok)
        popen.assert_not_called()

    def test_validate_token_goes_through_claude_proxy_only(self):
        self.make_profile("alice", "uuid-a", "alice@x.com", "acc-a", "ref-a")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"account": {"email": "alice@x.com"}}
        with patch.object(com.claude_manager, "check_port_accessible", return_value=True), \
                patch.object(com.requests, "get", return_value=resp) as get:
            res = com.validate_profile_token("alice")
        self.assertTrue(res["success"])
        self.assertEqual(res["email"], "alice@x.com")
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["proxies"], {"http": "http://127.0.0.1:11015", "https": "http://127.0.0.1:11015"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer acc-a")

    def test_validate_token_blocked_when_proxy_offline(self):
        self.make_profile("alice", "uuid-a", "alice@x.com", "acc-a", "ref-a")
        with patch.object(com.claude_manager, "check_port_accessible", return_value=False), \
                patch.object(com.requests, "get") as get:
            res = com.validate_profile_token("alice")
        self.assertFalse(res["success"])
        get.assert_not_called()

    def test_validate_token_401(self):
        self.make_profile("alice", "uuid-a", "alice@x.com", "acc-a", "ref-a")
        with patch.object(com.claude_manager, "check_port_accessible", return_value=True), \
                patch.object(com.requests, "get", return_value=MagicMock(status_code=401)):
            res = com.validate_profile_token("alice")
        self.assertFalse(res["success"])
        self.assertIn("401", res["error"])


class TestGeminiPortAssignment(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herdr_gemini_ports_"))
        self.profiles = self.tmp / "profiles"
        self.cli = self.tmp / "antigravity-cli"
        self.profiles.mkdir()
        self.cli.mkdir()
        self.orig = {n: getattr(gemini_manager, n) for n in (
            "WSL_BASE_DIR", "CLI_DIR", "PROFILES_DIR", "ACTIVE_TOKEN_FILE", "ACTIVE_PROXY_ENV_FILE", "ACTIVE_PROFILE_JSON_FILE")}
        gemini_manager.WSL_BASE_DIR = self.tmp
        gemini_manager.CLI_DIR = self.cli
        gemini_manager.PROFILES_DIR = self.profiles
        gemini_manager.ACTIVE_TOKEN_FILE = self.cli / "antigravity-oauth-token"
        gemini_manager.ACTIVE_PROXY_ENV_FILE = self.cli / "active_proxy.env"
        gemini_manager.ACTIVE_PROFILE_JSON_FILE = self.cli / "active_profile.json"
        self.bindings: dict = {}
        self.claude_ports: set[int] = set()
        pm = gemini_manager.proxy_manager
        self._patches = [
            patch.object(settings_manager, "get_account_proxy_binding", side_effect=lambda n: self.bindings.get(n)),
            patch.object(settings_manager, "set_account_proxy_binding"),
            patch.object(settings_manager, "clear_account_proxy_binding"),
            patch.object(settings_manager, "clear_all_account_proxy_bindings"),
            patch.object(pm, "get_proxy_claude_flag", side_effect=lambda p: p in self.claude_ports),
            patch.object(pm, "load_proxies", return_value=[]),
            patch.object(pm, "save_proxies"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for n, v in self.orig.items():
            setattr(gemini_manager, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, name: str, port: int | None = None, manual: bool = False) -> None:
        d = self.profiles / name
        d.mkdir()
        if port is not None:
            (d / "profile_config.json").write_text(json.dumps({"profile_name": name, "port": port, "manual": manual}))

    def saved_port(self, name: str) -> int:
        return json.loads((self.profiles / name / "profile_config.json").read_text())["port"]

    def test_fourth_account_gets_1084_not_1082(self):
        """Регрессия: 3 профиля (account-1, 2 email-профиля) -> новый account-2 получает 1084."""
        self.add("account-1", 1081)
        self.add("cavatina@gmail.com", 1082)
        self.add("dalliance@gmail.com", 1083)
        self.assertEqual(gemini_manager._calculate_default_sequential_port("account-2"), 1084)
        self.assertEqual(gemini_manager._calculate_default_sequential_port("anything@gmail.com"), 1084)

    def test_ports_follow_numbers_in_list(self):
        self.add("account-1", 1081)
        self.add("cavatina@gmail.com", 1082)
        self.add("dalliance@gmail.com", 1083)
        self.assertEqual(gemini_manager.get_port_assignments(), {
            "account-1": 1081, "cavatina@gmail.com": 1082, "dalliance@gmail.com": 1083})

    def test_deleting_first_account_shifts_others_up(self):
        """Регрессия: удалили #1 -> бывший #2 становится #1 на 1081, #3 -> #2 на 1082."""
        self.add("account-1", 1081)
        self.add("cavatina@gmail.com", 1082)
        self.add("dalliance@gmail.com", 1083)
        ok, _ = gemini_manager.delete_profile("account-1")
        self.assertTrue(ok)
        profiles = gemini_manager.list_profiles()
        self.assertEqual([(p["position"], p["profile_name"], p["port"]) for p in profiles], [
            (1, "cavatina@gmail.com", 1081), (2, "dalliance@gmail.com", 1082)])
        self.assertEqual(self.saved_port("cavatina@gmail.com"), 1081)
        self.assertEqual(self.saved_port("dalliance@gmail.com"), 1082)

    def test_change_position_moves_account_and_reassigns_ports(self):
        for n, port in (("a@gmail.com", 1081), ("b@gmail.com", 1082), ("c@gmail.com", 1083)):
            self.add(n, port)
        ok, msg = gemini_manager.set_profile_position("c@gmail.com", 1)
        self.assertTrue(ok, msg)
        self.assertIn(":1081", msg)
        self.assertEqual(gemini_manager.get_profile_order(), ["c@gmail.com", "a@gmail.com", "b@gmail.com"])
        self.assertEqual(self.saved_port("c@gmail.com"), 1081)
        self.assertEqual(self.saved_port("a@gmail.com"), 1082)
        self.assertEqual(self.saved_port("b@gmail.com"), 1083)
        # Порядок сохраняется в ~/.gemini/profile_order.json (попадает в бэкап вместе с ~/.gemini)
        order_file = json.loads((self.tmp / "profile_order.json").read_text(encoding="utf-8"))
        self.assertEqual(order_file["order"][0], "c@gmail.com")
        # Номер за пределами списка прижимается к краю
        gemini_manager.set_profile_position("c@gmail.com", 99)
        self.assertEqual(gemini_manager.get_profile_order()[-1], "c@gmail.com")

    def test_active_tunnel_follows_new_port(self):
        self.add("a@gmail.com", 1081)
        self.add("b@gmail.com", 1082)
        gemini_manager.ACTIVE_PROFILE_JSON_FILE.write_text(json.dumps({"profile_name": "b@gmail.com"}))
        gemini_manager.set_profile_position("b@gmail.com", 1)
        env = gemini_manager.ACTIVE_PROXY_ENV_FILE.read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:1081", env)

    def test_rename_keeps_position(self):
        self.add("a@gmail.com", 1081)
        self.add("b@gmail.com", 1082)
        gemini_manager.set_profile_position("b@gmail.com", 1)
        with patch.object(gemini_manager.strategy_manager, "rename_account_in_history"):
            ok, _ = gemini_manager.rename_profile("b@gmail.com", "zzz")
        self.assertTrue(ok)
        self.assertEqual(gemini_manager.get_profile_order(), ["zzz", "a@gmail.com"])

    def test_new_profile_is_appended_last(self):
        self.add("b@gmail.com", 1081)
        self.add("c@gmail.com", 1082)
        gemini_manager.save_profile_order(["c@gmail.com", "b@gmail.com"])
        port = gemini_manager._calculate_default_sequential_port("a@gmail.com")
        gemini_manager.reserve_new_profile_port("a@gmail.com", port)
        self.assertEqual(port, 1083)
        self.assertEqual(gemini_manager.get_profile_order(), ["c@gmail.com", "b@gmail.com", "a@gmail.com"])

    def test_manual_binding_has_priority_and_claude_ports_skipped(self):
        self.claude_ports = {1082}
        self.add("account-1", 1081)
        self.add("z@gmail.com", 1083, manual=True)
        self.add("y@gmail.com")
        a = gemini_manager.get_port_assignments()
        self.assertEqual(a["z@gmail.com"], 1083)
        self.assertEqual(a["y@gmail.com"], 1084)
        self.assertNotIn(1082, a.values())
        self.assertEqual(len(set(a.values())), len(a), "Порты не должны повторяться")

    def test_format_expiry_shows_date_and_time(self):
        exp = time.time() + 1800
        text, expired = gemini_manager.format_expiry(exp)
        self.assertFalse(expired)
        self.assertIn(time.strftime("до %H:%M %d.%m.%Y", time.localtime(exp)), text)
        text2, _ = gemini_manager.format_expiry(time.time() - 60)
        self.assertIn("истёк", text2)

    def test_token_expiry_taken_from_token_field(self):
        f = self.profiles / "a" / "antigravity-oauth-token"
        f.parent.mkdir()
        f.write_text(json.dumps({"token": {"access_token": "x", "refresh_token": "r",
                                           "expiry": "2099-01-02T03:04:05.123456789+00:00"}, "id_token": ""}))
        info = gemini_manager.get_token_info(f)
        exp = gemini_manager.parse_token_expiry("2099-01-02T03:04:05.123456789+00:00")
        self.assertIn(time.strftime("%d.%m.%Y", time.localtime(exp)), info["expiry_text"])
        self.assertIn("Refresh: бессрочный", info["expiry_text"])


class TestClaudeOAuthBackupRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herdr_claude_backup_"))
        self.app_dir = self.tmp / "app"
        self.app_dir.mkdir()
        self.wsl_home = self.tmp / "wsl_home"
        self.names = ("BASE_DIR", "SETTINGS_FILE", "WSL_GEMINI_DIR", "WSL_CLAUDE_DIR", "WSL_AIONUI_DIR",
                      "WIN_CLAUDE_DIR", "WIN_AIONUI_DIR")
        self.orig = {n: getattr(backup_manager, n) for n in self.names}
        self.orig_settings = settings_manager.SETTINGS_FILE
        backup_manager.BASE_DIR = self.app_dir
        backup_manager.SETTINGS_FILE = self.app_dir / "settings.json"
        settings_manager.SETTINGS_FILE = self.app_dir / "settings.json"
        backup_manager.WSL_GEMINI_DIR = self.wsl_home / ".gemini"
        backup_manager.WSL_CLAUDE_DIR = self.wsl_home / ".claude"
        backup_manager.WSL_AIONUI_DIR = self.wsl_home / ".aionui-web"
        backup_manager.WIN_CLAUDE_DIR = self.tmp / "win" / ".claude"
        backup_manager.WIN_AIONUI_DIR = self.tmp / "win" / "aionui"
        # Побочные эффекты восстановления не должны трогать реальную систему
        import claude_manager
        import strategy_manager
        self._patches = [
            patch.object(claude_manager, "sync_after_restore"),
            patch.object(strategy_manager, "load_history", return_value={}),
            patch.object(strategy_manager, "save_history"),
        ]
        try:
            import integrations_manager
            self._patches.append(patch.object(integrations_manager.logger, "log"))
        except Exception:
            pass
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for n, v in self.orig.items():
            setattr(backup_manager, n, v)
        settings_manager.SETTINGS_FILE = self.orig_settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_claude_oauth_profiles_and_proxy_port_roundtrip(self):
        (self.app_dir / "settings.json").write_text(json.dumps({"claude_proxy_host": "127.0.0.1", "claude_proxy_port": 1015}))
        prof = backup_manager.WSL_CLAUDE_DIR / "oauth-profiles" / "alice@x.com"
        write_credentials(prof / ".credentials.json", "acc-a", "ref-a")
        write_claude_json(prof / ".claude.json", "uuid-a", "alice@x.com")
        (prof / "profile.json").write_text(json.dumps({"profile_name": "alice@x.com"}))
        (prof / "projects").mkdir()
        (prof / "projects" / "huge.jsonl").write_text("x" * 1000)
        write_credentials(backup_manager.WSL_CLAUDE_DIR / ".credentials.json", "acc-a", "ref-a")

        ok, msg, path = backup_manager.create_encrypted_backup("Pass!123", target_dir=self.tmp / "backups")
        self.assertTrue(ok, msg)

        # Имитируем потерю данных
        shutil.rmtree(backup_manager.WSL_CLAUDE_DIR)
        (self.app_dir / "settings.json").write_text(json.dumps({"claude_proxy_port": 9999}))

        ok, msg, manifest = backup_manager.restore_encrypted_backup(path, "Pass!123")
        self.assertTrue(ok, msg)
        self.assertTrue(manifest.get("includes_claude_oauth_profiles"))

        for fn in (".credentials.json", ".claude.json", "profile.json"):
            self.assertTrue((prof / fn).exists(), fn)
        self.assertFalse((prof / "projects").exists(), "Служебные данные Claude Code не попадают в бэкап")
        restored = json.loads((prof / ".credentials.json").read_text(encoding="utf-8"))
        self.assertEqual(restored["claudeAiOauth"]["refreshToken"], "ref-a")
        self.assertTrue((backup_manager.WSL_CLAUDE_DIR / ".credentials.json").exists())
        settings = json.loads((self.app_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["claude_proxy_port"], 1015)


if __name__ == "__main__":
    unittest.main()


class TestClaudeOAuthTabGUI(unittest.TestCase):
    """Проверяет вкладку Claude OAuth в GUI (после Gemini OAuth)."""

    def setUp(self):
        try:
            import tkinter as tk
            import config_app
        except Exception:
            self.skipTest("Tkinter недоступен")
        self.config_app = config_app
        self._patches = [
            patch.object(config_app.HerdrConfigApp, "refresh_routes_async"),
            patch.object(config_app.HerdrConfigApp, "refresh_claude_profiles_async"),
        ]
        for p in self._patches:
            p.start()
        try:
            self.app = config_app.HerdrConfigApp()
            self.app.withdraw()
        except tk.TclError:
            for p in self._patches:
                p.stop()
            self.skipTest("Tkinter display недоступен")

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        for p in self._patches:
            p.stop()

    def _labels(self, widget) -> list[str]:
        out = []
        for child in widget.winfo_children():
            try:
                out.append(str(child.cget("text")))
            except Exception:
                pass
            out.extend(self._labels(child))
        return out

    def test_tab_is_placed_right_after_gemini(self):
        order = list(self.app.tab_buttons.keys())
        self.assertIn("claude_oauth", order)
        self.assertEqual(order.index("claude_oauth"), order.index("gemini") + 1)

    def test_render_profiles_with_expiry_and_claude_proxy_port(self):
        self.app.claude_proxy_online = True
        self.app.claude_active_status = {
            "exists": True, "expires_at": 1, "email": "alice@x.com", "name": "alice",
            "expiry_text": "Действителен (1ч 0м) • до 10:00 24.09.2026",
            "refresh_expiry_text": "Действителен (30д 0ч) • до 10:00 24.10.2026",
            "subscription": "pro", "saved": True, "profile_name": "alice", "is_expired": False,
        }
        self.app.claude_profiles = [
            {"profile_name": "alice", "exists": True, "is_active": True, "email": "alice@x.com", "name": "alice",
             "expiry_text": "Действителен (1ч 0м) • до 10:00 24.09.2026",
             "refresh_expiry_text": "Действителен (30д 0ч) • до 10:00 24.10.2026",
             "subscription": "pro", "proxy_port": 1015, "is_expired": False},
            {"profile_name": "bob", "exists": True, "is_active": False, "email": "bob@x.com", "name": "bob",
             "expiry_text": "Истёк 08:00 24.09.2026", "refresh_expiry_text": "Действителен (20д 0ч) • до 10:00 14.10.2026",
             "subscription": "max", "proxy_port": 1015, "is_expired": False},
        ]
        with patch.object(settings_manager, "get_claude_proxy_port", return_value=1015), \
                patch.object(settings_manager, "get_claude_proxy_host", return_value="127.0.0.1"):
            self.app.switch_page("claude_oauth")
            self.app.render_claude_oauth_page()
        text = "\n".join(self._labels(self.app.page_claude_oauth))
        self.assertIn("alice@x.com", text)
        self.assertIn("bob@x.com", text)
        self.assertIn("до 10:00 24.09.2026", text)
        self.assertIn("до 10:00 14.10.2026", text)
        self.assertIn(":1015", text)
        self.assertIn("11015", text)

    def test_add_account_uses_login_terminal(self):
        with patch("config_app.simpledialog.askstring", return_value="work"), \
                patch("config_app.claude_oauth_manager.launch_login_terminal", return_value=(True, "ok")) as launch, \
                patch("config_app.messagebox.showinfo"):
            self.app.on_add_claude_account()
        launch.assert_called_once_with("work")

    def test_gemini_cards_show_numbers_and_reorder(self):
        base = {"email": "x@gmail.com", "name": "x", "is_active": False, "expiry_text": "Access до 07:43 24.09.2026",
                "token_exists": True, "is_manual": False, "proxy_status": "unknown"}
        self.app.gemini_profiles = [
            dict(base, position=1, profile_name="a@gmail.com", port=1081, proxy_num=1),
            dict(base, position=2, profile_name="b@gmail.com", port=1082, proxy_num=2),
        ]
        with patch.object(self.config_app.gemini_manager, "is_guard_running", return_value=False), \
                patch.object(self.config_app.proxy_manager, "get_proxy_choices", return_value=[]):
            self.app.render_gemini_page()
        text = self._labels(self.app.gemini_cards_container)
        self.assertIn("#1", text)
        self.assertIn("#2", text)
        self.assertIn("Access до 07:43 24.09.2026", "\n".join(text))

        with patch.object(self.config_app.gemini_manager, "set_profile_position", return_value=(True, "ok")) as setpos, \
                patch.object(self.app, "load_gemini_profiles_data"), patch.object(self.app, "render_gemini_page"):
            self.app.on_change_gemini_position("b@gmail.com", 1)
        setpos.assert_called_once_with("b@gmail.com", 1)
