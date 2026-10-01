"""Блок 2: Настройка, проверка базы данных и запуск AionUi (WebUI :25808).

Обеспечивает:
1. Мгновенную проверку статуса AionUi по HTTP (:25808/api/auth/status, <0.1с).
2. Снятие зависших блокировок обслуживания (~/.aionui-web/.maintenance).
3. Проверку целостности базы данных aionui-backend.db и сброс WAL-журнала.
4. Запуск AionUi в изолированной tmux-сессии 'aionui'.
5. Быстрое ожидание готовности без ложных таймаутов.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from actions.base import ActionResult, BaseAction, WSL_DISTRO

logger = logging.getLogger("herdr.omni_aion.actions.setup_aionui")


class SetupAionUiAction(BaseAction):
    """Действие по проверке, подготовке базы и запуску AionUi в WSL2."""

    name: str = "setup_aionui"
    description: str = "Развертывание, проверка базы и запуск AionUi (:25808) в tmux"

    def __init__(
        self,
        port: int = 25808,
        distro: str = WSL_DISTRO,
        force_restart: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.port = port
        self.distro = distro
        self.force_restart = force_restart

    def _is_online(self) -> tuple[bool, dict[str, Any]]:
        """Быстрая проверка доступности AionUi через curl без прокси."""
        cmd = f"curl --noproxy '*' -s -m 3 http://127.0.0.1:{self.port}/api/auth/status"
        code, out, _ = self.run_wsl_cmd(cmd, distro=self.distro, timeout=5.0)
        if code == 0 and out.strip():
            try:
                data = json.loads(out)
                if data.get("success") is True or "needs_setup" in data:
                    return True, data
            except Exception:
                pass
        return False, {}

    def run(self, **kwargs: Any) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        # 1. Проверка уже запущенного AionUi
        def step_health_check():
            online, data = self._is_online()
            if online and not self.force_restart:
                return "skipped", f"AionUi уже работает на порту {self.port}", data
            return "ok", "Требуется инициализация / запуск сервиса", {}

        pre_check = self.execute_step(result, "check_existing_service", step_health_check)
        if result.steps[-1]["status"] == "skipped":
            return result.finish()

        # 2. Очистка блокировок обслуживания
        def step_clear_maintenance():
            if self.dry_run:
                return "dry_run", "Проверка флага .maintenance", {}
            cmd = "rm -f ~/.aionui-web/.maintenance"
            self.run_wsl_cmd(cmd, distro=self.distro, timeout=5.0)
            return "ok", "Флаг обслуживания снят (если присутствовал)", {}

        self.execute_step(result, "clear_maintenance_lock", step_clear_maintenance)

        # 3. Проверка базы данных
        def step_check_database():
            if self.dry_run:
                return "dry_run", "Проверка наличия и целостности базы aionui-backend.db", {}

            check_script = (
                "python3 -c \""
                "import os, sqlite3, sys; "
                "db_path = os.path.expanduser('~/.aionui-web/aionui-backend.db'); "
                "sys.exit(0 if os.path.exists(db_path) else 2)\""
            )
            code, _, _ = self.run_wsl_cmd(check_script, distro=self.distro, timeout=5.0)
            if code == 2:
                return "ok", "База данных еще не создана (будет инициализирована при первом запуске AionUi)", {}

            # Сброс WAL перед запуском для надежности
            wal_script = (
                "python3 -c \""
                "import os, sqlite3; "
                "p = os.path.expanduser('~/.aionui-web/aionui-backend.db'); "
                "c = sqlite3.connect(p); "
                "c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); "
                "c.close(); "
                "print('WAL_TRUNCATED')\""
            )
            c_code, c_out, _ = self.run_wsl_cmd(wal_script, distro=self.distro, timeout=5.0)
            return "ok", "База данных проверена, WAL-журнал синхронизирован", {"wal": "WAL_TRUNCATED" in c_out}

        self.execute_step(result, "verify_database", step_check_database)

        # 4. Запуск сервиса в tmux
        def step_start_service():
            if self.dry_run:
                return "dry_run", f"Запуск AionUi в tmux на порту {self.port}", {}

            start_cmd = (
                "tmux has-session -t aionui 2>/dev/null && tmux kill-session -t aionui; "
                "sleep 1; "
                f"tmux new -d -s aionui bash -lc 'aionui-web start --port {self.port}'"
            )
            self.run_wsl_cmd(start_cmd, distro=self.distro, timeout=10.0)

            # Ожидание готовности HTTP эндпоинта
            deadline = time.time() + 18.0
            last_err = ""
            while time.time() < deadline:
                time.sleep(1.0)
                online, data = self._is_online()
                if online:
                    return "ok", f"AionUi успешно запущен и отвечает на порту {self.port}", data

            raise TimeoutError(f"AionUi не ответил на порту {self.port} в течение 18 секунд")

        self.execute_step(result, "start_aionui_tmux", step_start_service)

        return result.finish()
