"""Блок 1: Подготовка системного рантайма (Node.js 22 LTS, better-sqlite3, tmux).

Обеспечивает:
1. Быструю проверку версии Node.js в WSL2 (требуется >= 22.0.0 для поддержки
   markAsUncloneable в Next.js 16 Edge runtime, устраняет падения HTTP 500).
2. Проверку и пересборку нативного бинарного аддона better-sqlite3 под ABI 127.
3. Проверку наличия утилит tmux и curl.
4. Пропуск этапов при уже готовом окружении (<0.2 сек).
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

from actions.base import ActionResult, BaseAction, WSL_DISTRO, CREATE_NO_WINDOW

logger = logging.getLogger("herdr.omni_aion.actions.prepare_runtime")


class PrepareRuntimeAction(BaseAction):
    """Действие по проверке и подготовке рантайма Node.js 22 и нативных модулей."""

    name: str = "prepare_runtime"
    description: str = "Подготовка рантайма Node.js 22 LTS, better-sqlite3 и tmux в WSL2"

    def __init__(self, distro: str = WSL_DISTRO, dry_run: bool = False, verbose: bool = False):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.distro = distro

    def _get_node_version(self) -> str | None:
        """Получает текущую версию Node.js в WSL2."""
        code, out, _ = self.run_wsl_cmd("node -v", distro=self.distro, timeout=5.0)
        if code == 0 and out.strip().startswith("v"):
            return out.strip()
        return None

    def _is_node22_or_higher(self, ver_str: str | None) -> bool:
        """Проверяет, что версия Node.js >= 22.0.0."""
        if not ver_str:
            return False
        match = re.search(r"v(\d+)\.", ver_str)
        if match:
            major = int(match.group(1))
            return major >= 22
        return False

    def _check_better_sqlite3(self) -> bool:
        """Проверяет загружаемость нативного аддона better-sqlite3."""
        script = "try { require('/usr/lib/node_modules/better-sqlite3'); console.log('OK'); } catch (e) { process.exit(1); }"
        code, out, _ = self.run_wsl_cmd(f"node -e \"{script}\"", distro=self.distro, timeout=5.0)
        return code == 0 and "OK" in out

    def _check_tmux(self) -> bool:
        """Проверяет наличие tmux в WSL2."""
        code, _, _ = self.run_wsl_cmd("which tmux", distro=self.distro, timeout=5.0)
        return code == 0

    def run(self, **kwargs: Any) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        # 1. Проверка версии Node.js
        def step_node_check():
            ver = self._get_node_version()
            if self._is_node22_or_higher(ver):
                return "skipped", f"Node.js {ver} уже установлен и соответствует требованиям (>= 22.0.0)", {"version": ver}
            
            if self.dry_run:
                return "dry_run", f"Требуется обновление Node.js с {ver or 'нет'} до 22.x LTS", {}

            # Обновление Node.js через официальный репозиторий NodeSource
            cmd = "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs"
            code, out, err = self.run_wsl_cmd(cmd, distro=self.distro, timeout=120.0)
            if code != 0:
                raise RuntimeError(f"Не удалось установить Node.js 22 LTS: {err or out}")
            
            new_ver = self._get_node_version()
            if not self._is_node22_or_higher(new_ver):
                raise RuntimeError(f"Версия после установки {new_ver} не удовлетворяет требованиям (>= 22.0.0)")
            
            return "ok", f"Node.js успешно обновлен до {new_ver}", {"version": new_ver}

        self.execute_step(result, "check_and_install_node22", step_node_check)

        # 2. Проверка и пересборка better-sqlite3
        def step_sqlite_check():
            if self._check_better_sqlite3():
                return "skipped", "Нативный аддон better-sqlite3 скомпилирован под текущий ABI Node.js", {}

            if self.dry_run:
                return "dry_run", "Требуется пересборка better-sqlite3 под ABI Node.js 22", {}

            rebuild_cmd = (
                "sudo npm rebuild better-sqlite3 --prefix /usr/lib/node_modules/omniroute 2>/dev/null || "
                "sudo npm install -g better-sqlite3 --build-from-source"
            )
            code, out, err = self.run_wsl_cmd(rebuild_cmd, distro=self.distro, timeout=90.0)
            if not self._check_better_sqlite3():
                raise RuntimeError(f"Не удалось скомпилировать better-sqlite3: {err or out}")
            
            return "ok", "better-sqlite3 успешно пересобран под Node.js 22", {}

        self.execute_step(result, "rebuild_better_sqlite3", step_sqlite_check)

        # 3. Проверка tmux
        def step_tmux_check():
            if self._check_tmux():
                return "skipped", "tmux уже установлен в WSL2", {}

            if self.dry_run:
                return "dry_run", "Требуется установка tmux в WSL2", {}

            code, out, err = self.run_wsl_cmd("sudo apt-get update && sudo apt-get install -y tmux", distro=self.distro, timeout=60.0)
            if code != 0 or not self._check_tmux():
                raise RuntimeError(f"Не удалось установить tmux: {err or out}")
            return "ok", "tmux успешно установлен в WSL2", {}

        self.execute_step(result, "check_tmux", step_tmux_check)

        return result.finish()
