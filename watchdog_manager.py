"""aiWatcher, встроенный в Herdr Control Center.

Сторож сервисов в WSL2: каждые N секунд проверяет AionUi и OmniRoute и,
если сервис не отвечает, запускает его заново. Раньше это было отдельное
приложение D:\\My files\\aiWatcher (watcher_app.py); теперь весь его функционал здесь:

- включение/выключение всего сторожа и каждого сервиса отдельно;
- проверка (check_cmd) и запуск (start_cmd) через `wsl.exe bash -lc`;
- журнал событий и статусы сервисов для карточки «aiWatcher» на странице «Маршруты»;
- настройки в watchdog_config.json (совместимы с watcher_config.json aiWatcher, импортируются при первом запуске).

Улучшения по сравнению с отдельным aiWatcher:
- таймаут на каждую команду WSL (раньше зависшая команда вешала сторожа навсегда);
- «период прогрева» после запуска: сервис не перезапускается повторно, пока стартует
  (раньше OmniRoute можно было запустить дважды, а AionUi грузится ~9 с);
- флаг обслуживания ~/.aionui-web/.maintenance уважается (ремонт базы не прерывается);
- AionUi считается живым по ответу порта 25808, а не по имени процесса;
- обнаружение внешнего (старого) aiWatcher: встроенный не работает параллельно с ним.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "watchdog_config.json"
LEGACY_CONFIG_FILE = Path(r"D:\My files\aiWatcher\watcher_config.json")
LEGACY_AUTORUN = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup\ai_watcher_autorun.vbs"

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000
CMD_TIMEOUT_S = 30.0
EXTERNAL_CHECK_EVERY_S = 60.0

MAINT_TTL_MIN = 30
AIONUI_CHECK_CMD = (
    f'[ -n "$(find $HOME/.aionui-web/.maintenance -mmin -{MAINT_TTL_MIN} 2>/dev/null)" ] || '
    "curl --noproxy '*' -s -o /dev/null --max-time 3 http://127.0.0.1:25808/"
)
AIONUI_START_CMD = (
    "tmux new -d -s aionui env -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy "
    "-u ALL_PROXY -u all_proxy NO_PROXY='*' no_proxy='*' "
    "/home/f/.local/bin/aionui-web start --no-open --port 25808"
)

HELPER_SCRIPTS = "/mnt/d/My files/aionUi_helper/scripts"
# «Здоровье» AionUi: процесс может жить, но отвечать 502 на /api/auth/status — тогда в браузере
# экран входа «Connection failed, please try again» (прокси в окружении или повисший бэкенд на битой базе).
AIONUI_HEALTH_CMD = (
    f'[ -n "$(find $HOME/.aionui-web/.maintenance -mmin -{MAINT_TTL_MIN} 2>/dev/null)" ] || '
    "{ c=$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' --max-time 5 "
    "http://127.0.0.1:25808/api/auth/status); [ \"$c\" -ge 200 ] && [ \"$c\" -lt 500 ]; }"
)
# Лечение: диагностика + чистый перезапуск под флагом обслуживания (скрипт из aionUi_helper)
AIONUI_HEAL_CMD = f'python3 "{HELPER_SCRIPTS}/fix_aionui_login.py" --fix'
# Ручные действия для кнопок карточки
AIONUI_RESTART_CMD = AIONUI_HEAL_CMD
AIONUI_REPAIR_DB_CMD = f'python3 "{HELPER_SCRIPTS}/repair_aionui_db.py"'
HEAL_TIMEOUT_S = 180.0
REPAIR_TIMEOUT_S = 1800.0

DEFAULT_CONFIG: dict[str, Any] = {
    "watcher_on": True,
    "interval": 5,
    "grace_seconds": 30,
    "apps": {
        "omniroute": {
            "enabled": True,
            "check_cmd": "ps aux | grep '[o]mniroute'",
            "start_cmd": "nohup omniroute start > /dev/null 2>&1 &",
        },
        "aionui": {
            "enabled": True,
            "check_cmd": AIONUI_CHECK_CMD,
            "start_cmd": AIONUI_START_CMD,
            "health_cmd": AIONUI_HEALTH_CMD,
            "heal_cmd": AIONUI_HEAL_CMD,
            "heal_after_failures": 3,
            "heal_cooldown_s": 600,
        },
    },
}

# Статусы сервиса
ST_UNKNOWN = "unknown"
ST_RUNNING = "running"
ST_STARTING = "starting"        # команда запуска отправлена, идёт период прогрева
ST_RESTARTED = "restarted"      # только что упал и был перезапущен
ST_START_FAILED = "start_failed"
ST_DISABLED = "disabled"
ST_PAUSED = "paused"            # весь сторож выключен
ST_EXTERNAL = "external"        # работает внешний aiWatcher — встроенный ничего не делает
ST_UNHEALTHY = "unhealthy"      # процесс жив, но не отвечает правильно (например, 502 → экран входа)
ST_HEALING = "healing"          # идёт автоматическое лечение (чистый перезапуск)
ST_HEAL_FAILED = "heal_failed"  # лечение не помогло — нужен ремонт базы или ручной разбор

Runner = Callable[[str], "tuple[bool, str]"]


# ─────────────────────────── Выполнение команд ───────────────────────────

def run_cmd(cmd: str, timeout: float = CMD_TIMEOUT_S) -> tuple[bool, str]:
    """Выполняет bash-команду в WSL (на Windows) или локально. (успех, вывод)."""
    args = ["wsl.exe", "bash", "-lc", cmd] if IS_WINDOWS else ["bash", "-lc", cmd]
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "encoding": "utf-8",
                              "errors": "replace", "timeout": timeout}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        res = subprocess.run(args, **kwargs)
        out = (res.stdout or "").strip() or (res.stderr or "").strip()
        return res.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, f"timeout {timeout:.0f}s"
    except Exception as e:  # wsl.exe не найден и т.п.
        return False, str(e)


def find_external_watchers(runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None) -> list[dict]:
    """Ищет запущенный отдельный aiWatcher (watcher_app.py или его headless-батник). Только Windows."""
    if not IS_WINDOWS and runner is None:
        return []
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'watcher_app\\.py|start_aiWatcher_headless' } "
          "| Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress")
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]
    try:
        if runner is not None:
            res = runner(args)
        else:
            res = subprocess.run(args, capture_output=True, text=True, timeout=15,
                                 creationflags=CREATE_NO_WINDOW)
        return parse_process_json(res.stdout)
    except Exception:
        return []


def parse_process_json(text: str | None) -> list[dict]:
    """Разбирает вывод ConvertTo-Json (объект или массив) и отбрасывает сам процесс PowerShell."""
    if not text or not text.strip():
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for p in data if isinstance(data, list) else []:
        cmd = str(p.get("CommandLine") or "")
        if "Get-CimInstance" in cmd:
            continue
        out.append({"pid": int(p.get("ProcessId") or 0), "cmd": cmd})
    return [p for p in out if p["pid"]]


def stop_external_watchers(procs: list[dict]) -> int:
    """Завершает процессы внешнего aiWatcher (сначала headless-цикл, затем python)."""
    killed = 0
    ordered = sorted(procs, key=lambda p: 0 if "headless" in p["cmd"] else 1)
    for p in ordered:
        try:
            subprocess.run(["taskkill", "/PID", str(p["pid"]), "/T", "/F"], capture_output=True,
                           timeout=10, creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0)
            killed += 1
        except Exception:
            pass
    return killed


def disable_legacy_autorun(path: Path = LEGACY_AUTORUN) -> bool:
    """Отключает автозапуск отдельного aiWatcher (переименование .vbs -> .vbs.disabled, обратимо)."""
    try:
        if path.exists():
            path.rename(path.with_name(path.name + ".disabled"))
            return True
    except OSError:
        pass
    return False


# ─────────────────────────── Конфигурация ───────────────────────────

def _upgrade_app(name: str, app: dict) -> dict:
    """Старые команды aiWatcher для AionUi заменяются безопасными (флаг обслуживания, порт, без прокси)."""
    app = dict(app)
    if name == "aionui":
        if ".maintenance" not in app.get("check_cmd", ""):
            app["check_cmd"] = AIONUI_CHECK_CMD
        start = app.get("start_cmd", "")
        if "--no-open" not in start or "NO_PROXY" not in start:
            app["start_cmd"] = AIONUI_START_CMD
    return app


def normalize_config(raw: Any) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return cfg
    if isinstance(raw.get("watcher_on"), bool):
        cfg["watcher_on"] = raw["watcher_on"]
    for key, lo, hi in (("interval", 2, 600), ("grace_seconds", 0, 600)):
        try:
            cfg[key] = max(lo, min(hi, int(raw.get(key, cfg[key]))))
        except (TypeError, ValueError):
            pass
    apps = raw.get("apps")
    if isinstance(apps, dict):
        merged: dict[str, dict] = {}
        for name, app in apps.items():
            if not isinstance(app, dict) or not app.get("check_cmd") or not app.get("start_cmd"):
                continue
            base = dict(cfg["apps"].get(name, {}))
            base.update({k: app[k] for k in ("enabled", "check_cmd", "start_cmd", "health_cmd", "heal_cmd",
                                             "heal_after_failures", "heal_cooldown_s") if k in app})
            base["enabled"] = bool(base.get("enabled", True))
            merged[name] = _upgrade_app(name, base)
        for name, app in cfg["apps"].items():
            merged.setdefault(name, app)
        cfg["apps"] = merged
    return cfg


_DEFAULT = object()


def load_config(path: Path | None = None, legacy: Any = _DEFAULT, persist: bool = True) -> dict:
    """Загружает watchdog_config.json; при первом запуске импортирует конфиг отдельного aiWatcher.
    Пути по умолчанию читаются из модуля во время вызова (тесты подменяют CONFIG_FILE/LEGACY_CONFIG_FILE).
    persist=False — ничего не записывать на диск (конфиг сохранится при первом запуске сторожа)."""
    path = path or CONFIG_FILE
    if legacy is _DEFAULT:
        legacy = LEGACY_CONFIG_FILE
    for src in (path, legacy):
        if src is None:
            continue
        try:
            if src.exists():
                cfg = normalize_config(json.loads(src.read_text(encoding="utf-8")))
                if src != path and persist:
                    save_config(cfg, path)
                return cfg
        except (OSError, ValueError):
            continue
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if persist:
        save_config(cfg, path)
    return cfg


def save_config(cfg: dict, path: Path | None = None) -> None:
    """Атомарная запись (tmp + replace), чтобы сбой не оставил пустой конфиг."""
    path = path or CONFIG_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


# ─────────────────────────── Сервис ───────────────────────────

class WatchdogService:
    """Логика сторожа без GUI. Потокобезопасна; UI подписывается через on_event."""

    def __init__(self, config_path: Path | None = None, legacy_path: Any = _DEFAULT,
                 runner: Runner | None = None,
                 external_finder: Callable[[], list[dict]] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 on_event: Callable[[str, str, str], None] | None = None):
        self.config_path = config_path or CONFIG_FILE
        # Создание сервиса ничего не пишет на диск; конфиг сохраняется при start() или изменении настроек
        self.config = load_config(self.config_path, legacy_path, persist=False)
        # run_cmd / find_external_watchers берутся из модуля в момент вызова — их можно подменить в тестах
        self.runner = runner or (lambda cmd, timeout=CMD_TIMEOUT_S: run_cmd(cmd, timeout))
        self.external_finder = external_finder or (lambda: find_external_watchers())
        self.clock = clock
        self.on_event = on_event
        self.statuses: dict[str, str] = {n: ST_UNKNOWN for n in self.config["apps"]}
        self.last_output: dict[str, str] = {}
        self.log: deque[str] = deque(maxlen=300)
        self.external: list[dict] = []
        self._last_external_check = -1e18
        self._started_at: dict[str, float] = {}
        self._health_failures: dict[str, int] = {}
        self._last_heal: dict[str, float] = {}
        self._action_lock = threading.Lock()
        self.action_running: str | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ── события/журнал ──
    def _emit(self, kind: str, app: str, text: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        with self._lock:
            self.log.append(line)
        if self.on_event:
            try:
                self.on_event(kind, app, line)
            except Exception:
                pass

    def _set_status(self, app: str, status: str) -> None:
        with self._lock:
            changed = self.statuses.get(app) != status
            self.statuses[app] = status
        if changed and self.on_event:
            try:
                self.on_event("status", app, status)
            except Exception:
                pass

    # ── настройки ──
    @property
    def enabled(self) -> bool:
        return bool(self.config.get("watcher_on", True))

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self.config["watcher_on"] = bool(on)
            save_config(self.config, self.config_path)
        self._emit("log", "", "Watcher activated." if on else "Watcher deactivated.")
        if not on:
            for n in self.config["apps"]:
                self._set_status(n, ST_PAUSED)
        self.wake()

    def set_app_enabled(self, app: str, on: bool) -> None:
        with self._lock:
            if app not in self.config["apps"]:
                return
            self.config["apps"][app]["enabled"] = bool(on)
            save_config(self.config, self.config_path)
        self._emit("log", app, f"Monitoring for {app} is now {'enabled' if on else 'disabled'}.")
        self.wake()

    # ── внешний aiWatcher ──
    def refresh_external(self, force: bool = False) -> list[dict]:
        now = self.clock()
        if force or now - self._last_external_check >= EXTERNAL_CHECK_EVERY_S:
            self._last_external_check = now
            found = self.external_finder() or []
            if bool(found) != bool(self.external):
                self._emit("external", "", "Обнаружен внешний aiWatcher — встроенный сторож на паузе."
                           if found else "Внешний aiWatcher не найден — встроенный сторож работает.")
            self.external = found
        return self.external

    # ── один проход ──
    def tick(self) -> dict[str, str]:
        if self.refresh_external():
            for n in self.config["apps"]:
                self._set_status(n, ST_EXTERNAL)
            return dict(self.statuses)
        if not self.enabled:
            for n in self.config["apps"]:
                self._set_status(n, ST_PAUSED)
            return dict(self.statuses)

        grace = float(self.config.get("grace_seconds", 30))
        for name, app in list(self.config["apps"].items()):
            if self._stop.is_set():
                break
            if not app.get("enabled", True):
                self._set_status(name, ST_DISABLED)
                continue
            ok, out = self.runner(app["check_cmd"])
            if ok:
                started = self._started_at.get(name)
                if started is not None and self.clock() - started < grace:
                    # сервис поднялся, но ещё прогревается: здоровье (502 во время загрузки) не проверяем
                    self._set_status(name, ST_RUNNING)
                    continue
                self._started_at.pop(name, None)
                self._check_health(name, app)
                continue
            started = self._started_at.get(name)
            if started is not None and self.clock() - started < grace:
                self._set_status(name, ST_STARTING)
                continue
            self._emit("log", name, f"{name} is down. Attempting to start...")
            ok_start, out_start = self.runner(app["start_cmd"])
            self.last_output[name] = out_start
            self._started_at[name] = self.clock()
            if ok_start:
                self._emit("log", name, f"Successfully sent start command for {name}.")
                self._set_status(name, ST_RESTARTED)
            else:
                self._emit("log", name, f"Failed to start {name}: {out_start}")
                self._set_status(name, ST_START_FAILED)
        return dict(self.statuses)

    # ── здоровье и лечение ──
    def _run(self, cmd: str, timeout: float) -> tuple[bool, str]:
        try:
            return self.runner(cmd, timeout=timeout)
        except TypeError:          # простой runner(cmd) без таймаута
            return self.runner(cmd)

    def _check_health(self, name: str, app: dict) -> None:
        health = app.get("health_cmd")
        if not health:
            self._health_failures.pop(name, None)
            self._set_status(name, ST_RUNNING)
            return
        ok, _ = self.runner(health)
        if ok:
            if self._health_failures.pop(name, 0):
                self._emit("log", name, f"{name} responds normally again.")
            self._set_status(name, ST_RUNNING)
            return
        fails = self._health_failures.get(name, 0) + 1
        self._health_failures[name] = fails
        need = int(app.get("heal_after_failures", 3))
        self._set_status(name, ST_UNHEALTHY)
        if fails == 1:
            self._emit("log", name, f"{name} is running but unhealthy (e.g. HTTP 502 → login page "
                                    f"'Connection failed'). Will heal after {need} failed checks.")
        heal = app.get("heal_cmd")
        if not heal or fails < need:
            return
        cooldown = float(app.get("heal_cooldown_s", 600))
        last = self._last_heal.get(name)
        if last is not None and self.clock() - last < cooldown:
            if fails == need:
                self._emit("log", name, f"{name} still unhealthy; last heal was less than "
                                        f"{int(cooldown)} s ago — waiting (see ToRepair / repair DB).")
            return
        self._last_heal[name] = self.clock()
        self._health_failures[name] = 0
        self._set_status(name, ST_HEALING)
        self._emit("log", name, f"Healing {name}: {heal}")
        ok_h, out_h = self._run(heal, HEAL_TIMEOUT_S)
        self._emit_output(name, out_h)
        self._started_at[name] = self.clock()       # после лечения — снова период прогрева
        if ok_h:
            self._emit("log", name, f"{name} healed (clean restart done).")
            self._set_status(name, ST_RESTARTED)
        else:
            self._emit("log", name, f"Healing {name} failed. If the log mentions 'malformed', "
                                    f"press 'Repair AionUi DB'.")
            self._set_status(name, ST_HEAL_FAILED)

    def _emit_output(self, name: str, out: str, lines: int = 12) -> None:
        tail = [l for l in (out or "").splitlines() if l.strip()][-lines:]
        for l in tail:
            self._emit("log", name, f"  │ {l[:220]}")

    def run_action(self, title: str, cmd: str, timeout: float,
                   on_done: Callable[[bool, str], None] | None = None) -> bool:
        """Ручное действие (кнопки «Перезапустить AionUi», «Починить базу») в отдельном потоке.
        Возвращает False, если другое действие уже выполняется."""
        if not self._action_lock.acquire(blocking=False):
            self._emit("log", "", f"Busy: '{self.action_running}' is still running.")
            return False
        self.action_running = title

        def worker():
            try:
                self._emit("action", "", f"▶ {title}...")
                ok, out = self._run(cmd, timeout)
                self._emit_output("", out, lines=20)
                self._emit("action", "", f"{'✓' if ok else '✕'} {title}: {'done' if ok else 'failed'}")
                self._health_failures.clear()
                if on_done:
                    try:
                        on_done(ok, out)
                    except Exception:
                        pass
            finally:
                self.action_running = None
                self._action_lock.release()
                self.wake()

        threading.Thread(target=worker, name="aiWatcher-action", daemon=True).start()
        return True

    # ── поток ──
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.config_path.exists():
            save_config(self.config, self.config_path)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="aiWatcher", daemon=True)
        self._thread.start()
        self._emit("log", "", "aiWatcher Started.")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout)

    def wake(self, recheck_external: bool = False) -> None:
        """Немедленный внеочередной проход (кнопка «Проверить сейчас», смена настроек).
        recheck_external=True — заодно заново поискать внешний aiWatcher (в фоновом потоке)."""
        if recheck_external:
            self._last_external_check = -1e18
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self._emit("log", "", f"Watcher error: {e}")
            self._wake.wait(float(self.config.get("interval", 5)))
            self._wake.clear()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
