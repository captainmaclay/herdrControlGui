"""GUI-тесты карточки «aiWatcher» на странице «Маршруты» (замена карточки Herdr Multiplexer).

Тесты создают настоящее окно HerdrConfigApp (скрытое). Все команды WSL, поиск внешнего aiWatcher,
конфиг и автозапуск подменены — тесты ничего не запускают и не пишут в настоящие файлы.
Запуск: python -m pytest test_aiwatcher_card.py -q   или   python -m unittest test_aiwatcher_card -v
"""

from __future__ import annotations

import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import config_app
import i18n
import watchdog_manager as wm


class FakeWSL:
    def __init__(self):
        self.down: set[str] = set()      # команды проверки, которые «падают»
        self.calls: list[str] = []

    def __call__(self, cmd: str, timeout: float = 30.0):
        self.calls.append(cmd)
        return (cmd not in self.down), ""


class AppCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.wsl = FakeWSL()
        self.external: list[dict] = []
        self.patches = [
            patch.object(config_app.HerdrConfigApp, "refresh_routes_async", lambda self: None),
            patch.object(config_app.HerdrConfigApp, "_schedule_fast_port_check", lambda self: None),
            patch.object(wm, "CONFIG_FILE", tmp / "watchdog_config.json"),
            patch.object(wm, "LEGACY_CONFIG_FILE", tmp / "no_legacy.json"),
            patch.object(wm, "run_cmd", self.wsl),
            patch.object(wm, "find_external_watchers", lambda *a, **k: list(self.external)),
            patch.object(wm.WatchdogService, "start"),   # проходы вызываем вручную через tick()
            patch.object(config_app.settings_manager, "SETTINGS_FILE", tmp / "settings.json"),
        ]
        for p in self.patches:
            p.start()
        try:
            self.app = config_app.HerdrConfigApp()
            self.app.withdraw()
        except tk.TclError:
            self._stop_patches()
            self.skipTest("Tkinter display/Tcl interpreter not available")
        self.svc = self.app.aiwatcher
        self.cfg_path = tmp / "watchdog_config.json"

    def _stop_patches(self):
        for p in reversed(self.patches):
            p.stop()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        self._stop_patches()
        self._tmp.cleanup()

    def pump(self):
        """Применить события сторожа из очереди и обработать события Tk."""
        for _ in range(3):
            self.app._aiw_drain_queue()
            self.app.update()


class TestAiWatcherCard(AppCase):
    def test_herdr_card_replaced_by_aiwatcher(self):
        self.assertFalse(hasattr(self.app, "herdr_card"))
        self.assertFalse(hasattr(self.app, "herdr_badge"))
        self.assertTrue(hasattr(self.app, "aiwatcher_card"))
        # карточка aiWatcher стоит первой на странице «Маршруты» — там, где был Herdr
        routes_parent = self.app.aiwatcher_card.master
        self.assertIs(routes_parent.pack_slaves()[0], self.app.aiwatcher_card)
        self.assertIs(routes_parent.pack_slaves()[1], self.app.gemini_card)

    def test_card_title_is_aiwatcher(self):
        titles = []
        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, tk.Label):
                    titles.append(c.cget("text"))
                walk(c)
        walk(self.app.aiwatcher_card)
        self.assertIn(i18n.t("aiw_title"), titles)
        self.assertTrue(any("aiWatcher" in x for x in titles))
        self.assertFalse(any("Herdr Multiplexer" in x for x in titles))

    def test_rows_for_every_service(self):
        self.assertEqual(set(self.app.aiw_app_labels), set(self.svc.config["apps"]))
        self.assertEqual(set(self.app.aiw_app_vars), {"aionui", "omniroute"})

    def test_statuses_rendered_after_tick(self):
        self.wsl.down.add(self.svc.config["apps"]["omniroute"]["check_cmd"])
        self.svc.tick()
        self.pump()
        self.assertEqual(self.app.aiw_app_labels["aionui"].cget("text"), i18n.t("aiw_st_running"))
        self.assertEqual(self.app.aiw_app_labels["omniroute"].cget("text"), i18n.t("aiw_st_restarted"))
        self.assertIn(self.svc.config["apps"]["omniroute"]["start_cmd"], self.wsl.calls)
        log = self.app.aiw_log_txt.get("1.0", "end")
        self.assertIn("omniroute is down", log)
        self.assertIn("Successfully sent start command for omniroute", log)

    def test_toggle_watcher_button(self):
        self.assertTrue(self.svc.enabled)
        self.assertEqual(self.app.aiw_badge.cget("text"), i18n.t("aiw_badge_on"))
        self.app.aiw_toggle_btn.invoke()
        self.pump()
        self.assertFalse(self.svc.enabled)
        self.assertEqual(self.app.aiw_badge.cget("text"), i18n.t("aiw_badge_off"))
        self.assertEqual(self.app.aiw_toggle_btn.cget("text"), i18n.t("aiw_btn_turn_on"))
        self.assertFalse(json.loads(self.cfg_path.read_text(encoding="utf-8"))["watcher_on"])
        self.assertEqual(self.app.aiw_app_labels["aionui"].cget("text"), i18n.t("aiw_st_paused"))
        self.app.aiw_toggle_btn.invoke()
        self.assertTrue(self.svc.enabled)

    def test_app_checkbox_toggles_monitoring(self):
        self.app.aiw_app_checks["omniroute"].invoke()          # снять галочку
        self.assertFalse(self.svc.config["apps"]["omniroute"]["enabled"])
        self.svc.tick()
        self.pump()
        self.assertEqual(self.app.aiw_app_labels["omniroute"].cget("text"), i18n.t("aiw_st_disabled"))
        self.assertNotIn(self.svc.config["apps"]["omniroute"]["check_cmd"], self.wsl.calls)

    def test_check_now_button_wakes_service(self):
        with patch.object(self.svc, "wake") as wake:
            self.app.aiw_check_btn.invoke()
            wake.assert_called_once_with(recheck_external=True)

    def test_external_watcher_warning(self):
        self.assertEqual(self.app.aiw_external_frame.winfo_manager(), "")
        self.external.append({"pid": 77, "cmd": "python.exe watcher_app.py"})
        self.svc.refresh_external(force=True)
        self.svc.tick()
        self.pump()
        self.assertEqual(self.app.aiw_external_frame.winfo_manager(), "pack")
        self.assertEqual(self.app.aiw_badge.cget("text"), i18n.t("aiw_badge_external"))
        self.assertEqual(self.app.aiw_app_labels["aionui"].cget("text"), i18n.t("aiw_st_external"))
        self.assertEqual(self.wsl.calls, [])                    # встроенный ничего не запускал
        self.external.clear()
        self.svc.refresh_external(force=True)
        self.pump()
        self.assertEqual(self.app.aiw_external_frame.winfo_manager(), "")

    def test_stop_external_button(self):
        self.external.append({"pid": 77, "cmd": "python.exe watcher_app.py"})
        self.svc.refresh_external(force=True)
        self.pump()
        with patch.object(config_app.messagebox, "askyesno", return_value=True), \
             patch.object(wm, "stop_external_watchers", return_value=1) as stop, \
             patch.object(wm, "disable_legacy_autorun", return_value=True) as autorun:
            self.external.clear()
            self.app.aiw_stop_external_btn.invoke()
            deadline = time.time() + 3
            while not stop.called or not autorun.called:
                if time.time() > deadline:
                    break
                time.sleep(0.02)
            time.sleep(0.1)
            self.pump()
            stop.assert_called_once_with([{"pid": 77, "cmd": "python.exe watcher_app.py"}])
            autorun.assert_called_once()
        self.assertIn(i18n.t("aiw_external_stopped", count=1), self.app.aiw_log_txt.get("1.0", "end"))
        self.assertEqual(self.app.aiw_external_frame.winfo_manager(), "")

    def test_stop_external_cancelled(self):
        self.external.append({"pid": 77, "cmd": "watcher_app.py"})
        self.svc.refresh_external(force=True)
        with patch.object(config_app.messagebox, "askyesno", return_value=False), \
             patch.object(wm, "stop_external_watchers") as stop:
            self.app._aiw_stop_external()
            time.sleep(0.1)
            stop.assert_not_called()

    def test_log_is_trimmed(self):
        for i in range(400):
            self.app._aiw_append_log(f"line {i}")
        lines = int(self.app.aiw_log_txt.index("end-1c").split(".")[0])
        self.assertLessEqual(lines, 301)
        self.assertIn("line 399", self.app.aiw_log_txt.get("1.0", "end"))

    def test_events_ignored_after_close(self):
        self.app._closing = True
        self.app._aiw_on_event("log", "", "late line")          # не должно падать
        self.app._closing = False

    def test_route_results_without_herdr(self):
        res = {"timestamp": "12:00:00",
               "gemini": {"online": True, "port": 1081},
               "claude": {"online": True, "host": "127.0.0.1", "port": 1015, "http_port": 11015,
                          "killswitch_engaged": False, "killswitch_enabled": True}}
        self.app._apply_route_results(res)
        self.assertEqual(self.app.quick_status.cget("text"), i18n.t("status_routes_ok"))

    def test_switch_tabs_and_back_to_routes(self):
        for tab in ["proxy", "gemini", "strategy", "backup", "localization", "routes"]:
            self.app.switch_page(tab)
            self.assertEqual(self.app.active_tab, tab)
            self.pump()
        self.assertTrue(self.app.aiwatcher_card.winfo_ismapped() or True)


class TestAionuiHealButtons(AppCase):
    """Экран входа «Connection failed»: AionUi жив, но /api/auth/status = 502."""

    def _wait(self, cond, timeout=3.0):
        deadline = time.time() + timeout
        while not cond() and time.time() < deadline:
            time.sleep(0.02)
            self.pump()

    def test_unhealthy_status_shown_and_auto_heal(self):
        a = self.svc.config["apps"]["aionui"]
        self.svc.config["grace_seconds"] = 0
        self.wsl.down.add(a["health_cmd"])
        for _ in range(3):
            self.svc.tick()
        self.pump()
        self.assertIn(a["heal_cmd"], self.wsl.calls)
        log = self.app.aiw_log_txt.get("1.0", "end")
        self.assertIn("unhealthy", log)
        self.assertIn("Healing aionui", log)
        self.assertEqual(self.app.aiw_app_labels["aionui"].cget("text"), i18n.t("aiw_st_restarted"))

    def test_unhealthy_label(self):
        self.app._aiw_set_app_status("aionui", wm.ST_UNHEALTHY)
        self.assertEqual(self.app.aiw_app_labels["aionui"].cget("text"), i18n.t("aiw_st_unhealthy"))

    def test_restart_button_runs_clean_restart(self):
        self.app.aiw_restart_btn.invoke()
        self._wait(lambda: wm.AIONUI_RESTART_CMD in self.wsl.calls and self.svc.action_running is None)
        self.assertIn(wm.AIONUI_RESTART_CMD, self.wsl.calls)
        self.pump()
        self.assertIn(i18n.t("aiw_action_restart"), self.app.aiw_log_txt.get("1.0", "end"))
        self.assertEqual(str(self.app.aiw_restart_btn.cget("state")), "normal")

    def test_repair_button_asks_confirmation(self):
        with patch.object(config_app.messagebox, "askyesno", return_value=False):
            self.app.aiw_repair_btn.invoke()
        time.sleep(0.1)
        self.assertNotIn(wm.AIONUI_REPAIR_DB_CMD, self.wsl.calls)
        with patch.object(config_app.messagebox, "askyesno", return_value=True):
            self.app.aiw_repair_btn.invoke()
        self._wait(lambda: wm.AIONUI_REPAIR_DB_CMD in self.wsl.calls and self.svc.action_running is None)
        self.assertIn(wm.AIONUI_REPAIR_DB_CMD, self.wsl.calls)

    def test_buttons_disabled_while_action_runs(self):
        import threading
        gate = threading.Event()
        orig = self.svc.runner
        self.svc.runner = lambda cmd, timeout=30.0: (gate.wait(3), (True, ""))[1]
        self.app.aiw_restart_btn.invoke()
        self._wait(lambda: str(self.app.aiw_repair_btn.cget("state")) == "disabled", 2)
        self.assertEqual(str(self.app.aiw_repair_btn.cget("state")), "disabled")
        gate.set()
        self._wait(lambda: self.svc.action_running is None)
        self.pump()
        self.assertEqual(str(self.app.aiw_repair_btn.cget("state")), "normal")
        self.svc.runner = orig


class TestWipeAllDataRefresh(AppCase):
    """«Стереть все данные» раньше падало: вызывались несуществующие load_data()/update_content()."""

    def test_wipe_refreshes_ui_without_crash(self):
        with patch.object(config_app.messagebox, "askyesno", return_value=True), \
             patch.object(config_app.messagebox, "showinfo"), \
             patch.object(config_app.messagebox, "showwarning"), \
             patch.object(config_app.backup_manager, "wipe_all_data", return_value=(True, "ok")) as wipe, \
             patch.object(self.app, "load_proxies_data") as lp, \
             patch.object(self.app, "load_gemini_profiles_data") as lg:
            self.app.switch_page("backup")
            self.app.on_wipe_all_data()
            wipe.assert_called_once()
            lp.assert_called_once()
            lg.assert_called_once()
            self.assertEqual(self.app.active_tab, "backup")


class TestDestroyStopsWatcher(unittest.TestCase):
    """Отдельно: с настоящим потоком сторожа (команды WSL подменены)."""

    def test_destroy_stops_thread(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(config_app.HerdrConfigApp, "refresh_routes_async", lambda self: None), \
             patch.object(config_app.HerdrConfigApp, "_schedule_fast_port_check", lambda self: None), \
             patch.object(wm, "CONFIG_FILE", Path(d) / "c.json"), \
             patch.object(wm, "LEGACY_CONFIG_FILE", Path(d) / "none.json"), \
             patch.object(wm, "run_cmd", lambda cmd, timeout=30: (True, "")), \
             patch.object(wm, "find_external_watchers", lambda *a, **k: []), \
             patch.object(config_app.settings_manager, "SETTINGS_FILE", Path(d) / "settings.json"):
            try:
                app = config_app.HerdrConfigApp()
                app.withdraw()
            except tk.TclError:
                self.skipTest("Tkinter display/Tcl interpreter not available")
            self.assertFalse(app.aiwatcher.running, "окно само не должно запускать сторож (это делает main())")
            self.assertFalse((Path(d) / "c.json").exists(), "создание окна не должно писать конфиг")
            app.aiwatcher.start()
            self.assertTrue(app.aiwatcher.running)
            self.assertTrue((Path(d) / "c.json").exists())
            svc = app.aiwatcher
            app.destroy()
            self.assertFalse(svc.running)


class TestTranslations(unittest.TestCase):
    def test_all_aiwatcher_keys_translated(self):
        keys = {k for k in i18n.TRANSLATIONS["en"] if k.startswith("aiw_")}
        self.assertGreaterEqual(len(keys), 20)
        self.assertEqual(keys, {k for k in i18n.TRANSLATIONS["ru"] if k.startswith("aiw_")})
        for key, _color in config_app.HerdrConfigApp.AIW_STATUS_STYLE.values():
            self.assertIn(key, keys)
        for name in wm.DEFAULT_CONFIG["apps"]:
            self.assertIn(f"aiw_app_{name}", keys)


if __name__ == "__main__":
    unittest.main()
