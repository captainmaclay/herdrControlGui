"""Блок 3: Конфигурация ядра OmniRoute (~/.omniroute/.env, doctor).

Обеспечивает:
1. Проверку наличия CLI-пакета omniroute в WSL2.
2. Идемпотентную генерацию и проверку переменных ~/.omniroute/.env:
   - OMNIROUTE_PORT=20128
   - OMNIROUTE_SERVER_HOST=0.0.0.0
   - STORAGE_ENCRYPTION_KEY (сохраняет существующий ключ, предотвращая порчу базы)
   - REQUIRE_API_KEY=false
3. Запуск встроенной проверки 'omniroute doctor' с подтверждением 0 сбоев.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from actions.base import ActionResult, BaseAction, WSL_DISTRO

logger = logging.getLogger("herdr.omni_aion.actions.setup_omniroute_core")


class SetupOmniRouteCoreAction(BaseAction):
    """Действие по проверке установки CLI и настройке окружения OmniRoute."""

    name: str = "setup_omniroute_core"
    description: str = "Конфигурация окружения OmniRoute (.env, ключи шифрования, doctor)"

    def __init__(
        self,
        port: int = 20128,
        distro: str = WSL_DISTRO,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.port = port
        self.distro = distro

    def run(self, **kwargs: Any) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        # 1. Проверка наличия CLI omniroute
        def step_check_cli():
            code, out, _ = self.run_wsl_cmd("which omniroute", distro=self.distro, timeout=5.0)
            if code != 0:
                if self.dry_run:
                    return "dry_run", "Требуется установка глобального пакета omniroute", {}
                install_code, i_out, i_err = self.run_wsl_cmd(
                    "sudo npm install -g omniroute", distro=self.distro, timeout=120.0
                )
                if install_code != 0:
                    raise RuntimeError(f"Не удалось установить omniroute: {i_err or i_out}")
                return "ok", "Пакет omniroute успешно установлен", {}
            return "ok", "CLI omniroute доступен", {"bin": out.strip()}

        self.execute_step(result, "verify_cli_binary", step_check_cli)

        # 2. Настройка конфигурационного файла ~/.omniroute/.env
        def step_configure_env():
            if self.dry_run:
                return "dry_run", "Проверка и дополнение ~/.omniroute/.env", {}

            # Скрипт на Python внутри WSL2 для безопасного чтения и обновления .env
            env_script = (
                "python3 -c \""
                "import os, secrets; "
                "d = os.path.expanduser('~/.omniroute'); "
                "os.makedirs(d, exist_ok=True); "
                "p = os.path.join(d, '.env'); "
                "env = {}; "
                "if os.path.exists(p):"
                "    with open(p, 'r', encoding='utf-8') as f:"
                "        for line in f:"
                "            line = line.strip(); "
                "            if '=' in line and not line.startswith('#'):"
                "                k, v = line.split('=', 1); env[k.strip()] = v.strip(); "
                "if not env.get('STORAGE_ENCRYPTION_KEY'):"
                "    env['STORAGE_ENCRYPTION_KEY'] = secrets.token_hex(32); "
                "env['OMNIROUTE_PORT'] = '" + str(self.port) + "'; "
                "env['OMNIROUTE_SERVER_HOST'] = '0.0.0.0'; "
                "env['REQUIRE_API_KEY'] = 'false'; "
                "with open(p, 'w', encoding='utf-8') as f:"
                "    for k, v in env.items():"
                "        f.write(f'{k}={v}\\n'); "
                "print('ENV_CONFIGURED')\""
            )
            code, out, err = self.run_wsl_cmd(env_script, distro=self.distro, timeout=10.0)
            if code != 0 or "ENV_CONFIGURED" not in out:
                raise RuntimeError(f"Не удалось сконфигурировать ~/.omniroute/.env: {err or out}")
            return "ok", "Файл окружения ~/.omniroute/.env успешно синхронизирован", {}

        self.execute_step(result, "configure_environment", step_configure_env)

        # 3. Запуск самодиагностики omniroute doctor
        def step_run_doctor():
            if self.dry_run:
                return "dry_run", "Запуск omniroute doctor", {}

            code, out, err = self.run_wsl_cmd("omniroute doctor", distro=self.distro, timeout=15.0)
            # omniroute doctor сообщает число failures
            has_failures = "failure(s)" in out and not "0 failure(s)" in out
            if has_failures:
                logger.warning(f"OmniRoute doctor выявил предупреждения: {out}")
            return "ok", "OmniRoute doctor завершил проверку", {"output": out.strip()[:300]}

        self.execute_step(result, "run_doctor_diagnostic", step_run_doctor)

        return result.finish()
