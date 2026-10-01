"""Мастер-пайплайн развертывания и обслуживания стека Omni_Aion.

Оркестрирует последовательное выполнение 5 модульных блоков:
1. prepare_runtime: Node.js 22 LTS, better-sqlite3 ABI 127, tmux.
2. setup_aionui: AionUi WebUI (:25808), снятие блокировок, проверка базы.
3. setup_omniroute_core: CLI omniroute, ~/.omniroute/.env, doctor.
4. sync_gemini_farm: Токены Gemini, сокет :1015 active, комбо-маршруты.
5. start_omniroute: Изолированный запуск tmux -L omniroute (:20128).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from actions.base import ActionResult, BaseAction, WSL_DISTRO
from Omni_Aion.actions.prepare_runtime import PrepareRuntimeAction
from Omni_Aion.actions.setup_aionui import SetupAionUiAction
from Omni_Aion.actions.setup_omniroute_core import SetupOmniRouteCoreAction
from Omni_Aion.actions.sync_gemini_farm import SyncGeminiFarmAction
from Omni_Aion.actions.start_omniroute import StartOmniRouteAction

logger = logging.getLogger("herdr.omni_aion.actions.pipeline")


class OmniAionDeployPipeline(BaseAction):
    """Мастер-действие для полного сквозного или выборочного развертывания стека."""

    name: str = "omni_aion_pipeline"
    description: str = "Полный конвейер ускоренной установки и настройки стека OmniRoute + AionUi"

    def __init__(
        self,
        distro: str = WSL_DISTRO,
        aionui_port: int = 25808,
        omniroute_port: int = 20128,
        force_restart: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.distro = distro
        self.aionui_port = aionui_port
        self.omniroute_port = omniroute_port
        self.force_restart = force_restart

        # Инициализация отдельных блоков
        self.b1_runtime = PrepareRuntimeAction(distro=distro, dry_run=dry_run, verbose=verbose)
        self.b2_aionui = SetupAionUiAction(
            port=aionui_port, distro=distro, force_restart=force_restart, dry_run=dry_run, verbose=verbose
        )
        self.b3_omni_core = SetupOmniRouteCoreAction(
            port=omniroute_port, distro=distro, dry_run=dry_run, verbose=verbose
        )
        self.b4_farm_sync = SyncGeminiFarmAction(distro=distro, dry_run=dry_run, verbose=verbose)
        self.b5_omni_start = StartOmniRouteAction(
            port=omniroute_port, distro=distro, force_restart=force_restart, dry_run=dry_run, verbose=verbose
        )

    def run_block(self, block_number: int) -> ActionResult:
        """Запускает конкретный блок по его номеру (1-5)."""
        blocks = {
            1: self.b1_runtime,
            2: self.b2_aionui,
            3: self.b3_omni_core,
            4: self.b4_farm_sync,
            5: self.b5_omni_start,
        }
        if block_number not in blocks:
            raise ValueError(f"Неизвестный номер блока {block_number}. Допустимые: 1, 2, 3, 4, 5")
        action = blocks[block_number]
        logger.info(f"Запуск блока {block_number}: {action.name}")
        return action.run()

    def run(self, **kwargs: Any) -> ActionResult:
        """Выполняет сквозной конвейер развертывания всех 5 блоков."""
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)
        t_start = time.time()

        blocks = [
            ("1. Runtime (Node.js 22 LTS, better-sqlite3)", self.b1_runtime),
            ("2. AionUi WebUI (:25808)", self.b2_aionui),
            ("3. OmniRoute Core (.env, doctor)", self.b3_omni_core),
            ("4. Gemini Farm & Sockets (:1015, combos)", self.b4_farm_sync),
            ("5. OmniRoute Daemon (:20128)", self.b5_omni_start),
        ]

        for label, action in blocks:
            def make_step_fn(act: BaseAction):
                def _step():
                    sub_res = act.run()
                    if not sub_res.success:
                        raise RuntimeError(f"Сбой в блоке '{act.name}': {sub_res.errors}")
                    # Собираем данные шагов
                    skipped = all(s.get("status") == "skipped" for s in sub_res.steps)
                    status = "skipped" if skipped else "ok"
                    msg = f"Блок завершен за {sub_res.duration_seconds}с ({len(sub_res.steps)} шагов)"
                    return status, msg, sub_res.to_dict()
                return _step

            self.execute_step(result, label, make_step_fn(action), fatal=True)

        result.data["total_pipeline_time_sec"] = round(time.time() - t_start, 3)
        return result.finish()
