"""Базовый фреймворк для выполнения системных действий (Actions Framework).

Предоставляет:
- Унифицированный интерфейс действия (BaseAction)
- Структурированный результат выполнения (ActionResult)
- Поддержку сухого прогона (dry-run)
- Идемпотентность и пошаговую регистрацию телеметрии
- Кросс-платформенные хелперы для взаимодействия с Windows и WSL2
"""

from __future__ import annotations

import base64
import dataclasses
import logging
import os
import subprocess
import time
from typing import Any, Callable

logger = logging.getLogger("herdr.actions")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")


@dataclasses.dataclass
class ActionResult:
    action_name: str
    success: bool = True
    dry_run: bool = False
    steps: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)
    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    started_at: float = dataclasses.field(default_factory=time.time)
    finished_at: float = 0.0

    def add_step(
        self,
        name: str,
        status: str,
        message: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Добавляет информацию о выполненном шаге."""
        step_info = {
            "step": name,
            "status": status,  # "ok", "skipped", "failed", "dry_run"
            "message": message,
            "details": details or {},
            "timestamp": time.time(),
        }
        self.steps.append(step_info)
        if status == "failed":
            self.success = False
            if message and message not in self.errors:
                self.errors.append(message)

    def add_error(self, error: str) -> None:
        """Регистрирует ошибку."""
        self.success = False
        self.errors.append(error)

    def finish(self) -> ActionResult:
        """Завершает выполнение действия."""
        if not self.finished_at:
            self.finished_at = time.time()
        return self

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 3)

    def to_dict(self) -> dict[str, Any]:
        """Возвращает словарь для JSON-сериализации."""
        return {
            "action": self.action_name,
            "success": self.success,
            "dry_run": self.dry_run,
            "duration_sec": self.duration_seconds,
            "errors": self.errors,
            "data": self.data,
            "steps": self.steps,
        }

    def summary_str(self) -> str:
        """Человекочитаемый отчет о выполнении."""
        status_sym = "[OK]" if self.success else "[FAILED]"
        prefix = "[DRY-RUN] " if self.dry_run else ""
        lines = [f"{prefix}{status_sym} Действие '{self.action_name}' завершено за {self.duration_seconds}с:"]
        for s in self.steps:
            st = s["status"].upper()
            sym = "+" if st in ("OK", "DRY_RUN") else ("-" if st == "SKIPPED" else "!")
            lines.append(f"  [{sym}] {s['step']}: {s['message']} ({st})")
        if self.errors:
            lines.append("  Ошибки:")
            for err in self.errors:
                lines.append(f"    - {err}")
        return "\n".join(lines)


class BaseAction:
    """Базовый абстрактный класс для всех автоматических действий."""

    name: str = "base_action"
    description: str = "Базовое действие"

    def __init__(self, dry_run: bool = False, verbose: bool = False):
        self.dry_run = dry_run
        self.verbose = verbose

    def execute_step(
        self,
        result: ActionResult,
        step_name: str,
        step_fn: Callable[..., Any],
        *args: Any,
        fatal: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Выполняет один шаг с перехватом ошибок и добавлением в отчет.
        
        Если fatal=True и шаг завершился с ошибкой, дальнейшее выполнение прекращается.
        """
        logger.info(f"[{self.name}] Выполнение шага: {step_name} (dry_run={self.dry_run})")
        try:
            val = step_fn(*args, **kwargs)
            # Если функция сама возвращает tuple (status, message, details)
            if isinstance(val, tuple) and len(val) >= 2:
                status, msg = val[0], val[1]
                details = val[2] if len(val) > 2 else {}
                result.add_step(step_name, status, msg, details)
            else:
                result.add_step(step_name, "ok", "Шаг успешно выполнен", {"result": val} if val is not None else {})
            return val
        except Exception as exc:
            err_msg = f"Ошибка в шаге '{step_name}': {exc}"
            logger.error(f"[{self.name}] {err_msg}", exc_info=self.verbose)
            result.add_step(step_name, "failed", err_msg)
            if fatal:
                raise
            return None

    def run(self, **kwargs: Any) -> ActionResult:
        """Главный метод выполнения действия. Переопределяется в подклассах."""
        raise NotImplementedError("Метод run() должен быть реализован в подклассе.")

    # ---------------- Вспомогательные методы ----------------

    def run_cmd(
        self,
        args: list[str],
        timeout: float = 15.0,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Запускает процесс в Windows без всплытия консольного окна."""
        if self.dry_run:
            logger.info(f"[DRY-RUN CMD] {' '.join(args)}")
            return 0, "[dry-run stdout]", ""
        try:
            res = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd,
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
            return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
        except subprocess.TimeoutExpired:
            return -1, "", f"Превышен таймаут выполнения ({timeout}s)"
        except Exception as ex:
            return -1, "", str(ex)

    def run_powershell(self, script_str: str, timeout: float = 15.0) -> tuple[int, str, str]:
        """Выполняет PowerShell команду/скрипт через Base64 UTF-16LE."""
        if self.dry_run:
            logger.info(f"[DRY-RUN POWERSHELL] {script_str[:120]}...")
            return 0, "[dry-run ps stdout]", ""
        encoded = base64.b64encode(script_str.encode("utf-16-le")).decode("ascii")
        return self.run_cmd(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            timeout=timeout,
        )

    def run_wsl(
        self,
        bash_cmd: str,
        user: str = "root",
        distro: str = WSL_DISTRO,
        timeout: float = 20.0,
    ) -> tuple[int, str, str]:
        """Выполняет команду bash внутри указанного дистрибутива WSL2."""
        if self.dry_run:
            logger.info(f"[DRY-RUN WSL ({distro}:{user})] {bash_cmd[:120]}...")
            return 0, "[dry-run wsl stdout]", ""
        cmd = ["wsl.exe", "-d", distro, "-u", user, "-e", "bash", "-c", bash_cmd]
        return self.run_cmd(cmd, timeout=timeout)
