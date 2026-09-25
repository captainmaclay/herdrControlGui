"""Тесты встроенного aiWatcher (watchdog_manager.py).

Запуск:  python -m pytest test_watchdog_manager.py -q      или      python -m unittest test_watchdog_manager -v
Тесты НЕ трогают настоящие конфиги и не запускают сервисы: пути и выполнение команд подменены.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import watchdog_manager as wm


class FakeRunner:
    """Имитация WSL: словарь «команда -> (успех, вывод)», журнал вызовов."""

    def __init__(self, results: dict[str, tuple[bool, str]] | None = None, default=(False, "")):
        self.results = results or {}
        self.default = default
        self.calls: list[str] = []

    def __call__(self, cmd: str, timeout: float = 30.0) -> tuple[bool, str]:
        self.calls.append(cmd)
        self.timeouts = getattr(self, "timeouts", {})
        self.timeouts[cmd] = timeout
        return self.results.get(cmd, self.default)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_cfg(**over) -> dict:
    cfg = json.loads(json.dumps(wm.DEFAULT_CONFIG))
    cfg.update(over)
    return cfg


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg_path = self.tmp / "watchdog_config.json"
        # Никакой тест не должен видеть настоящие файлы
        self.p1 = patch.object(wm, "CONFIG_FILE", self.cfg_path)
        self.p2 = patch.object(wm, "LEGACY_CONFIG_FILE", self.tmp / "no_legacy.json")
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self._tmp.cleanup()

    def service(self, runner=None, external=None, clock=None, cfg=None, events=None):
        if cfg is not None:
            wm.save_config(cfg, self.cfg_path)
        return wm.WatchdogService(
            config_path=self.cfg_path, legacy_path=None,
            runner=runner or FakeRunner(default=(True, "")),
            external_finder=(lambda: external) if external is not None else (lambda: []),
            clock=clock or FakeClock(),
            on_event=(lambda k, a, t: events.append((k, a, t))) if events is not None else None,
        )


# ─────────────────────────── Конфигурация ───────────────────────────

class TestConfig(TmpDirCase):
    def test_defaults_created_on_first_run(self):
        cfg = wm.load_config(self.cfg_path, None)
        self.assertTrue(self.cfg_path.exists())
        self.assertEqual(set(cfg["apps"]), {"aionui", "omniroute"})
        self.assertIn(".maintenance", cfg["apps"]["aionui"]["check_cmd"])
        self.assertIn("--no-open", cfg["apps"]["aionui"]["start_cmd"])

    def test_default_omniroute_cmds(self):
        om = wm.DEFAULT_CONFIG["apps"]["omniroute"]
        self.assertIn("20128", om["check_cmd"])
        self.assertNotIn("ps aux", om["check_cmd"])
        self.assertIn("omniroute serve", om["start_cmd"])
        self.assertNotIn("omniroute start", om["start_cmd"])
        self.assertIn("tmux new -d -s omniroute", om["start_cmd"])

    def test_default_start_cmd_strips_all_proxy_vars(self):
        cmd = wm.DEFAULT_CONFIG["apps"]["aionui"]["start_cmd"]
        for v in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            self.assertIn(f"-u {v}", cmd)
        self.assertIn("NO_PROXY='*'", cmd)

    def test_legacy_aiwatcher_config_is_imported_and_upgraded(self):
        legacy = self.tmp / "watcher_config.json"
        legacy.write_text(json.dumps({
            "watcher_on": False, "interval": 7,
            "apps": {
                "omniroute": {"enabled": False, "check_cmd": "ps aux | grep '[o]mniroute'",
                              "start_cmd": "nohup omniroute start > /dev/null 2>&1 &"},
                "aionui": {"enabled": True, "check_cmd": "ps aux | grep '[a]ionui-web'",
                           "start_cmd": "tmux new -d -s aionui /home/f/.local/bin/aionui-web start"},
            }}), encoding="utf-8")
        cfg = wm.load_config(self.cfg_path, legacy)
        self.assertFalse(cfg["watcher_on"])
        self.assertEqual(cfg["interval"], 7)
        self.assertFalse(cfg["apps"]["omniroute"]["enabled"])
        # опасные старые команды AionUi заменены безопасными
        self.assertEqual(cfg["apps"]["aionui"]["check_cmd"], wm.AIONUI_CHECK_CMD)
        self.assertEqual(cfg["apps"]["aionui"]["start_cmd"], wm.AIONUI_START_CMD)
        # OmniRoute v3.8+: `omniroute start` не существует, grep по имени процесса даёт ложное «живой»
        self.assertEqual(cfg["apps"]["omniroute"]["check_cmd"], wm.OMNIROUTE_CHECK_CMD)
        self.assertEqual(cfg["apps"]["omniroute"]["start_cmd"], wm.OMNIROUTE_START_CMD)
        # импорт сохранён в собственный конфиг, исходный файл не изменён
        self.assertTrue(self.cfg_path.exists())
        self.assertIn("ps aux | grep '[a]ionui-web'", legacy.read_text(encoding="utf-8"))

    def test_persist_false_writes_nothing(self):
        legacy = self.tmp / "watcher_config.json"
        legacy.write_text(json.dumps({"interval": 8}), encoding="utf-8")
        cfg = wm.load_config(self.cfg_path, legacy, persist=False)
        self.assertEqual(cfg["interval"], 8)
        self.assertFalse(self.cfg_path.exists())

    def test_service_saves_config_only_on_start(self):
        svc = wm.WatchdogService(config_path=self.cfg_path, legacy_path=None,
                                 runner=FakeRunner(default=(True, "")), external_finder=lambda: [])
        self.assertFalse(self.cfg_path.exists())
        svc.start()
        svc.stop(timeout=3)
        self.assertTrue(self.cfg_path.exists())

    def test_own_config_has_priority_over_legacy(self):
        wm.save_config(make_cfg(interval=11), self.cfg_path)
        legacy = self.tmp / "watcher_config.json"
        legacy.write_text(json.dumps({"interval": 99}), encoding="utf-8")
        self.assertEqual(wm.load_config(self.cfg_path, legacy)["interval"], 11)

    def test_broken_config_falls_back_to_defaults(self):
        self.cfg_path.write_text("{not json", encoding="utf-8")
        cfg = wm.load_config(self.cfg_path, None)
        self.assertEqual(cfg["interval"], wm.DEFAULT_CONFIG["interval"])

    def test_normalize_clamps_and_filters(self):
        cfg = wm.normalize_config({"interval": 0, "grace_seconds": "abc", "watcher_on": "yes",
                                   "apps": {"bad": {"enabled": True}, "x": "nope",
                                            "custom": {"check_cmd": "true", "start_cmd": "true"}}})
        self.assertEqual(cfg["interval"], 2)                  # минимум 2 с
        self.assertEqual(cfg["grace_seconds"], wm.DEFAULT_CONFIG["grace_seconds"])
        self.assertTrue(cfg["watcher_on"])                    # не bool — игнор
        self.assertNotIn("bad", cfg["apps"])
        self.assertIn("custom", cfg["apps"])
        self.assertIn("aionui", cfg["apps"])                  # стандартные сервисы не теряются

    def test_save_is_atomic_and_leaves_no_tmp(self):
        wm.save_config(make_cfg(interval=9), self.cfg_path)
        self.assertEqual(json.loads(self.cfg_path.read_text(encoding="utf-8"))["interval"], 9)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["watchdog_config.json"])


# ─────────────────────────── Логика проверки ───────────────────────────

class TestTick(TmpDirCase):
    def test_running_services_are_not_restarted(self):
        r = FakeRunner(default=(True, ""))
        svc = self.service(runner=r)
        st = svc.tick()
        self.assertEqual(st, {"omniroute": wm.ST_RUNNING, "aionui": wm.ST_RUNNING})
        self.assertFalse(any(c == app["start_cmd"] for app in svc.config["apps"].values() for c in r.calls))

    def test_down_service_is_started(self):
        cfg = make_cfg()
        aion = cfg["apps"]["aionui"]
        r = FakeRunner({aion["check_cmd"]: (False, ""), aion["start_cmd"]: (True, "")}, default=(True, ""))
        events = []
        svc = self.service(runner=r, cfg=cfg, events=events)
        st = svc.tick()
        self.assertEqual(st["aionui"], wm.ST_RESTARTED)
        self.assertEqual(r.calls.count(aion["start_cmd"]), 1)
        logs = [t for k, a, t in events if k == "log"]
        self.assertTrue(any("aionui is down" in t for t in logs))
        self.assertTrue(any("Successfully sent start command for aionui" in t for t in logs))

    def test_start_failure_is_reported(self):
        cfg = make_cfg()
        om = cfg["apps"]["omniroute"]
        r = FakeRunner({om["check_cmd"]: (False, ""), om["start_cmd"]: (False, "omniroute: not found")},
                       default=(True, ""))
        svc = self.service(runner=r, cfg=cfg)
        self.assertEqual(svc.tick()["omniroute"], wm.ST_START_FAILED)
        self.assertTrue(any("omniroute: not found" in line for line in svc.log))

    def test_grace_period_prevents_double_start(self):
        """OmniRoute раньше можно было запустить дважды: проверка падала, пока сервис грузится."""
        cfg = make_cfg(grace_seconds=30)
        om = cfg["apps"]["omniroute"]
        r = FakeRunner({om["check_cmd"]: (False, ""), om["start_cmd"]: (True, "")}, default=(True, ""))
        clock = FakeClock()
        svc = self.service(runner=r, cfg=cfg, clock=clock)
        svc.tick()
        clock.t += 5
        self.assertEqual(svc.tick()["omniroute"], wm.ST_STARTING)
        clock.t += 10
        svc.tick()
        self.assertEqual(r.calls.count(om["start_cmd"]), 1)
        clock.t += 20                                          # прогрев истёк, сервис так и не поднялся
        self.assertEqual(svc.tick()["omniroute"], wm.ST_RESTARTED)
        self.assertEqual(r.calls.count(om["start_cmd"]), 2)

    def test_recovered_service_resets_grace(self):
        cfg = make_cfg(grace_seconds=30)
        om = cfg["apps"]["omniroute"]
        r = FakeRunner({om["check_cmd"]: (False, ""), om["start_cmd"]: (True, "")}, default=(True, ""))
        svc = self.service(runner=r, cfg=cfg)
        svc.tick()
        r.results[om["check_cmd"]] = (True, "")
        self.assertEqual(svc.tick()["omniroute"], wm.ST_RUNNING)
        self.assertIn("omniroute", svc._started_at)            # ещё прогревается
        svc.clock = lambda: 10**9                              # прогрев закончился
        self.assertEqual(svc.tick()["omniroute"], wm.ST_RUNNING)
        self.assertNotIn("omniroute", svc._started_at)

    def test_disabled_app_is_skipped(self):
        cfg = make_cfg()
        cfg["apps"]["omniroute"]["enabled"] = False
        r = FakeRunner(default=(False, ""))
        svc = self.service(runner=r, cfg=cfg)
        self.assertEqual(svc.tick()["omniroute"], wm.ST_DISABLED)
        self.assertNotIn(cfg["apps"]["omniroute"]["check_cmd"], r.calls)

    def test_watcher_off_does_nothing(self):
        r = FakeRunner(default=(False, ""))
        svc = self.service(runner=r, cfg=make_cfg(watcher_on=False))
        st = svc.tick()
        self.assertEqual(set(st.values()), {wm.ST_PAUSED})
        self.assertEqual(r.calls, [])

    def test_external_watcher_pauses_builtin(self):
        r = FakeRunner(default=(False, ""))
        events = []
        svc = self.service(runner=r, external=[{"pid": 42, "cmd": "python watcher_app.py"}], events=events)
        st = svc.tick()
        self.assertEqual(set(st.values()), {wm.ST_EXTERNAL})
        self.assertEqual(r.calls, [], "при внешнем aiWatcher встроенный не должен ничего запускать")
        self.assertTrue(any(k == "external" for k, _, _ in events))

    def test_external_check_is_cached(self):
        calls = []
        clock = FakeClock()
        svc = self.service(clock=clock)
        svc.external_finder = lambda: calls.append(1) or []
        svc.tick(); svc.tick()
        self.assertEqual(len(calls), 1)
        clock.t += wm.EXTERNAL_CHECK_EVERY_S + 1
        svc.tick()
        self.assertEqual(len(calls), 2)
        svc.wake(recheck_external=True)
        svc.tick()
        self.assertEqual(len(calls), 3)

    def test_set_enabled_and_app_enabled_persist(self):
        svc = self.service()
        svc.set_enabled(False)
        svc.set_app_enabled("omniroute", False)
        svc.set_app_enabled("unknown-app", False)              # не падает
        saved = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertFalse(saved["watcher_on"])
        self.assertFalse(saved["apps"]["omniroute"]["enabled"])
        self.assertEqual(set(svc.statuses.values()), {wm.ST_PAUSED})

    def test_status_events_only_on_change(self):
        events = []
        svc = self.service(events=events)
        svc.tick(); svc.tick()
        status_events = [e for e in events if e[0] == "status"]
        self.assertEqual(len(status_events), 2)                # по одному на сервис

    def test_callback_exception_does_not_break_watcher(self):
        def bad(*_):
            raise RuntimeError("UI died")
        svc = self.service()
        svc.on_event = bad
        self.assertEqual(svc.tick()["aionui"], wm.ST_RUNNING)


# ─────────────────────────── Здоровье и лечение AionUi ───────────────────────────

class TestHealthAndHeal(TmpDirCase):
    """Процесс AionUi жив, порт отвечает, но /api/auth/status = 502 → экран входа «Connection failed».
    Раньше сторож считал такой AionUi «работающим» и ничего не делал."""

    def setUp(self):
        super().setUp()
        self.cfg = make_cfg(grace_seconds=0)
        self.cfg["apps"]["omniroute"]["enabled"] = False
        self.a = self.cfg["apps"]["aionui"]
        self.clock = FakeClock()
        self.r = FakeRunner({self.a["check_cmd"]: (True, ""), self.a["health_cmd"]: (False, ""),
                             self.a["heal_cmd"]: (True, "УСПЕХ: /api/auth/status = 200")}, default=(True, ""))
        self.svc = self.service(runner=self.r, cfg=self.cfg, clock=self.clock)

    def test_defaults_have_health_and_heal(self):
        self.assertIn("/api/auth/status", self.a["health_cmd"])
        self.assertIn(".maintenance", self.a["health_cmd"])
        self.assertIn("fix_aionui_login.py", self.a["heal_cmd"])
        self.assertIn("--fix", self.a["heal_cmd"])

    def test_unhealthy_then_heal_after_n_failures(self):
        self.assertEqual(self.svc.tick()["aionui"], wm.ST_UNHEALTHY)
        self.assertEqual(self.svc.tick()["aionui"], wm.ST_UNHEALTHY)
        self.assertNotIn(self.a["heal_cmd"], self.r.calls)
        self.assertEqual(self.svc.tick()["aionui"], wm.ST_RESTARTED)       # 3-я неудача → лечение
        self.assertEqual(self.r.calls.count(self.a["heal_cmd"]), 1)
        self.assertEqual(self.r.timeouts[self.a["heal_cmd"]], wm.HEAL_TIMEOUT_S)
        self.assertTrue(any("УСПЕХ" in l for l in self.svc.log))
        self.assertNotIn(self.a["start_cmd"], self.r.calls)                # не «запуск», а чистый перезапуск

    def test_heal_cooldown_prevents_loop(self):
        for _ in range(3):
            self.svc.tick()
        for _ in range(6):                                                 # всё ещё 502 (например, битая база)
            self.clock.t += 5
            self.svc.tick()
        self.assertEqual(self.r.calls.count(self.a["heal_cmd"]), 1)
        self.assertTrue(any("waiting" in l for l in self.svc.log))
        self.clock.t += self.a["heal_cooldown_s"]
        for _ in range(3):
            self.svc.tick()
        self.assertEqual(self.r.calls.count(self.a["heal_cmd"]), 2)

    def test_heal_failure_status(self):
        self.r.results[self.a["heal_cmd"]] = (False, "ОШИБКА: AionUi снова запускается сам")
        for _ in range(3):
            st = self.svc.tick()
        self.assertEqual(st["aionui"], wm.ST_HEAL_FAILED)
        self.assertTrue(any("Repair AionUi DB" in l for l in self.svc.log))

    def test_recovery_resets_failures(self):
        self.svc.tick(); self.svc.tick()
        self.r.results[self.a["health_cmd"]] = (True, "")
        self.assertEqual(self.svc.tick()["aionui"], wm.ST_RUNNING)
        self.r.results[self.a["health_cmd"]] = (False, "")
        self.svc.tick(); self.svc.tick()
        self.assertNotIn(self.a["heal_cmd"], self.r.calls)                 # счётчик начался заново

    def test_no_health_check_during_warmup(self):
        cfg = make_cfg(grace_seconds=60)
        cfg["apps"]["omniroute"]["enabled"] = False
        a = cfg["apps"]["aionui"]
        r = FakeRunner({a["check_cmd"]: (False, ""), a["start_cmd"]: (True, ""), a["health_cmd"]: (False, "")},
                       default=(True, ""))
        clock = FakeClock()
        svc = self.service(runner=r, cfg=cfg, clock=clock)
        svc.tick()                                                         # запуск
        r.results[a["check_cmd"]] = (True, "")
        clock.t += 10
        self.assertEqual(svc.tick()["aionui"], wm.ST_RUNNING)
        self.assertNotIn(a["health_cmd"], r.calls)                         # 502 во время загрузки не считается

    def test_app_without_health_cmd_is_just_running(self):
        self.cfg["apps"]["aionui"].pop("health_cmd")
        svc = self.service(runner=self.r, cfg=self.cfg)
        # normalize_config вернёт health_cmd по умолчанию; явно убираем в памяти
        svc.config["apps"]["aionui"].pop("health_cmd", None)
        self.assertEqual(svc.tick()["aionui"], wm.ST_RUNNING)

    def test_old_config_gets_health_defaults(self):
        cfg = wm.normalize_config({"apps": {"aionui": {"enabled": True, "check_cmd": "x", "start_cmd": "y"}}})
        self.assertEqual(cfg["apps"]["aionui"]["health_cmd"], wm.AIONUI_HEALTH_CMD)
        self.assertEqual(cfg["apps"]["aionui"]["heal_cmd"], wm.AIONUI_HEAL_CMD)

    def test_run_action_background_and_busy(self):
        gate = threading.Event()
        calls = []

        def slow(cmd, timeout=30.0):
            calls.append((cmd, timeout))
            gate.wait(3)
            return True, "line1\nline2"
        svc = self.service(runner=slow)
        done = []
        self.assertTrue(svc.run_action("Repair", wm.AIONUI_REPAIR_DB_CMD, wm.REPAIR_TIMEOUT_S,
                                       on_done=lambda ok, out: done.append(ok)))
        time.sleep(0.05)
        self.assertEqual(svc.action_running, "Repair")
        self.assertFalse(svc.run_action("Other", "true", 5))              # занято
        gate.set()
        deadline = time.time() + 3
        while not done and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(done, [True])
        self.assertIsNone(svc.action_running)
        self.assertEqual(calls[0], (wm.AIONUI_REPAIR_DB_CMD, wm.REPAIR_TIMEOUT_S))
        self.assertTrue(any("line2" in l for l in svc.log))


@unittest.skipIf(wm.IS_WINDOWS, "проверка bash-команды — в Linux/WSL")
class TestAionuiHealthCmdReal(unittest.TestCase):
    """Настоящая bash-команда здоровья против локального HTTP-сервера, отвечающего 200 или 502."""

    def _serve(self, code: int):
        import http.server
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(code); self.end_headers(); self.wfile.write(b"x")
            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def _run(self, port: int, home: Path) -> int:
        cmd = wm.AIONUI_HEALTH_CMD.replace("25808", str(port))
        env = {**os.environ, "HOME": str(home), "HTTP_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "socks5h://127.0.0.1:9"}
        return subprocess.run(["bash", "-c", cmd], env=env).returncode

    def test_200_ok_502_fail_flag_skips(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); (home / ".aionui-web").mkdir()
            ok = self._serve(200); bad = self._serve(502)
            try:
                self.assertEqual(self._run(ok.server_address[1], home), 0)      # прокси в env не мешает
                self.assertNotEqual(self._run(bad.server_address[1], home), 0)
                self.assertNotEqual(self._run(1, home), 0)                      # никто не слушает
                (home / ".aionui-web" / ".maintenance").write_text("x")
                self.assertEqual(self._run(bad.server_address[1], home), 0)     # идёт ремонт — не лечить
            finally:
                ok.shutdown(); bad.shutdown(); ok.server_close(); bad.server_close()


# ─────────────────────────── Фоновый поток ───────────────────────────

class TestThread(TmpDirCase):
    def test_start_wake_stop(self):
        r = FakeRunner(default=(True, ""))
        svc = self.service(runner=r, cfg=make_cfg(interval=600))
        svc.start()
        self.assertTrue(svc.running)
        deadline = time.time() + 3
        while len(r.calls) < 2 and time.time() < deadline:
            time.sleep(0.02)
        first = len(r.calls)
        svc.wake()                                             # внеочередной проход, не ждём 600 с
        deadline = time.time() + 3
        while len(r.calls) <= first and time.time() < deadline:
            time.sleep(0.02)
        self.assertGreater(len(r.calls), first)
        svc.stop(timeout=3)
        self.assertFalse(svc.running)

    def test_start_twice_keeps_single_thread(self):
        svc = self.service(cfg=make_cfg(interval=600))
        svc.start()
        t1 = svc._thread
        svc.start()
        self.assertIs(svc._thread, t1)
        svc.stop(timeout=3)

    def test_loop_survives_runner_exception(self):
        calls = []

        def boom(cmd):
            calls.append(cmd)
            raise OSError("wsl.exe crashed")
        svc = self.service(runner=boom, cfg=make_cfg(interval=600))
        svc.start()
        deadline = time.time() + 3
        while not any("Watcher error" in l for l in svc.log) and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(any("Watcher error" in l for l in svc.log))
        self.assertTrue(svc.running)
        svc.stop(timeout=3)


# ─────────────────────────── Вспомогательные функции ───────────────────────────

class TestHelpers(unittest.TestCase):
    @unittest.skipIf(wm.IS_WINDOWS, "на Windows run_cmd вызывает wsl.exe")
    def test_run_cmd_real_bash(self):
        self.assertEqual(wm.run_cmd("echo hi"), (True, "hi"))
        ok, _ = wm.run_cmd("exit 3")
        self.assertFalse(ok)

    @unittest.skipIf(wm.IS_WINDOWS, "на Windows run_cmd вызывает wsl.exe")
    def test_run_cmd_timeout(self):
        ok, out = wm.run_cmd("sleep 5", timeout=0.5)
        self.assertFalse(ok)
        self.assertIn("timeout", out)

    def test_run_cmd_missing_binary(self):
        with patch.object(wm.subprocess, "run", side_effect=FileNotFoundError("wsl.exe")):
            self.assertEqual(wm.run_cmd("true"), (False, "wsl.exe"))

    def test_parse_process_json(self):
        self.assertEqual(wm.parse_process_json(""), [])
        self.assertEqual(wm.parse_process_json("garbage"), [])
        one = json.dumps({"ProcessId": 10, "CommandLine": "python.exe watcher_app.py"})
        self.assertEqual(wm.parse_process_json(one), [{"pid": 10, "cmd": "python.exe watcher_app.py"}])
        many = json.dumps([
            {"ProcessId": 11, "CommandLine": 'cmd /c "start_aiWatcher_headless.bat" --watchdog'},
            {"ProcessId": 12, "CommandLine": "powershell -Command Get-CimInstance Win32_Process ... watcher_app"},
            {"ProcessId": 0, "CommandLine": "x"},
        ])
        self.assertEqual([p["pid"] for p in wm.parse_process_json(many)], [11])

    def test_find_external_uses_runner(self):
        fake = lambda args: subprocess.CompletedProcess(args, 0, json.dumps({"ProcessId": 5, "CommandLine": "watcher_app.py"}), "")
        self.assertEqual(wm.find_external_watchers(runner=fake), [{"pid": 5, "cmd": "watcher_app.py"}])

    def test_stop_external_kills_headless_loop_first(self):
        procs = [{"pid": 1, "cmd": "python watcher_app.py"}, {"pid": 2, "cmd": "cmd start_aiWatcher_headless.bat"}]
        with patch.object(wm.subprocess, "run") as run:
            self.assertEqual(wm.stop_external_watchers(procs), 2)
        pids = [c.args[0][2] for c in run.call_args_list]
        self.assertEqual(pids, ["2", "1"])                    # иначе цикл батника перезапустит python

    def test_disable_legacy_autorun_is_reversible_rename(self):
        with tempfile.TemporaryDirectory() as d:
            vbs = Path(d) / "ai_watcher_autorun.vbs"
            vbs.write_text("x")
            self.assertTrue(wm.disable_legacy_autorun(vbs))
            self.assertFalse(vbs.exists())
            self.assertTrue((Path(d) / "ai_watcher_autorun.vbs.disabled").exists())
            self.assertFalse(wm.disable_legacy_autorun(vbs))   # повторно — ничего


@unittest.skipIf(wm.IS_WINDOWS, "проверка bash-команды — в Linux/WSL")
class TestAionuiCheckCmd(unittest.TestCase):
    """Реальная bash-проверка AionUi: флаг обслуживания и закрытый порт."""

    def _run(self, home: Path) -> int:
        cmd = wm.AIONUI_CHECK_CMD.replace("25808", "1")        # порт 1 заведомо закрыт
        return subprocess.run(["bash", "-c", cmd], env={**os.environ, "HOME": str(home)}).returncode

    def test_flag_logic(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".aionui-web").mkdir()
            self.assertNotEqual(self._run(home), 0)
            flag = home / ".aionui-web" / ".maintenance"
            flag.write_text("repair")
            self.assertEqual(self._run(home), 0)
            old = time.time() - (wm.MAINT_TTL_MIN + 5) * 60
            os.utime(flag, (old, old))
            self.assertNotEqual(self._run(home), 0)


if __name__ == "__main__":
    unittest.main()
