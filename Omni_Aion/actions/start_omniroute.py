"""Блок 5: Запуск и мониторинг готовности OmniRoute (:20128).

Обеспечивает:
1. Запуск OmniRoute в выделенном изолированном сокете tmux (-L omniroute),
   что гарантирует защиту от сигналов SIGHUP при перезапуске других сервисов.
2. Проверку доступности HTTP-сервера (:20128) без проксирования loopback.
3. Быстрое ожидание готовности эндпоинта /v1/models.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from actions.base import ActionResult, BaseAction, WSL_DISTRO

logger = logging.getLogger("herdr.omni_aion.actions.start_omniroute")


class StartOmniRouteAction(BaseAction):
    """Действие по запуску и контролю работоспособности сервера OmniRoute."""

    name: str = "start_omniroute"
    description: str = "Изолированный запуск OmniRoute (:20128) в tmux (-L omniroute)"

    def __init__(
        self,
        port: int = 20128,
        distro: str = WSL_DISTRO,
        force_restart: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.port = port
        self.distro = distro
        self.force_restart = force_restart

    def _is_online(self) -> tuple[bool, int]:
        """Проверяет доступность сервера OmniRoute по HTTP."""
        cmd = f"curl --noproxy '*' -s -o /dev/null -w '%{{http_code}}' -m 3 http://127.0.0.1:{self.port}/"
        code, out, _ = self.run_wsl_cmd(cmd, distro=self.distro, timeout=5.0)
        if code == 0 and out.strip() in ("200", "307", "308"):
            return True, int(out.strip())
        return False, 0

    def _check_models(self) -> int:
        """Проверяет количество доступных моделей через /v1/models."""
        cmd = (
            f"curl --noproxy '*' -s -m 4 http://127.0.0.1:{self.port}/v1/models "
            "-H 'Authorization: Bearer sk-omniroute-secret'"
        )
        code, out, _ = self.run_wsl_cmd(cmd, distro=self.distro, timeout=6.0)
        if code == 0 and out.strip():
            try:
                data = json.loads(out)
                models = data.get("data", [])
                return len(models)
            except Exception:
                pass
        return 0

    def run(self, **kwargs: Any) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        # 1. Проверка текущего статуса
        def step_check_status():
            online, status_code = self._is_online()
            if online and not self.force_restart:
                models_cnt = self._check_models()
                return "skipped", f"OmniRoute уже работает на порту {self.port} (моделей: {models_cnt})", {
                    "http_code": status_code,
                    "models_count": models_cnt,
                }
            return "ok", "Требуется запуск / перезапуск OmniRoute", {}

        self.execute_step(result, "check_current_status", step_check_status)
        if result.steps[-1]["status"] == "skipped":
            return result.finish()

        # 2. Запуск в изолированном сервере tmux (-L omniroute)
        def step_launch_tmux():
            if self.dry_run:
                return "dry_run", f"Запуск 'omniroute serve --no-open' в tmux -L omniroute на порту {self.port}", {}

            start_cmd = (
                "tmux -L omniroute kill-session -t omniroute 2>/dev/null; "
                "sleep 1; "
                "tmux -L omniroute new -d -s omniroute bash -lc 'omniroute serve --no-open'"
            )
            self.run_wsl_cmd(start_cmd, distro=self.distro, timeout=10.0)

            # Ожидание готовности
            deadline = time.time() + 25.0
            models_cnt = 0
            while time.time() < deadline:
                time.sleep(1.5)
                online, _ = self._is_online()
                if online:
                    models_cnt = self._check_models()
                    if models_cnt > 0:
                        return "ok", f"OmniRoute успешно запущен (доступно {models_cnt} моделей)", {
                            "models_count": models_cnt
                        }

            # Если порт слушает, но каталог моделей ещё загружается
            online, code = self._is_online()
            if online:
                return "ok", f"OmniRoute запущен (HTTP {code}), каталог моделей инициализируется в фоне", {}

            raise TimeoutError(f"OmniRoute не ответил на порту {self.port} за 25 секунд")

        self.execute_step(result, "launch_omniroute_service", step_launch_tmux)

        return result.finish()
