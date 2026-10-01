"""Пакет модульных действий ускоренной установки и настройки стека Omni_Aion.

Содержит атомарные высокоскоростные блоки:
- PrepareRuntimeAction (Блок 1): Node.js 22 LTS, better-sqlite3 ABI 127, tmux
- SetupAionUiAction (Блок 2): AionUi WebUI (:25808), снятие блокировок, база данных
- SetupOmniRouteCoreAction (Блок 3): CLI omniroute, ~/.omniroute/.env, doctor
- SyncGeminiFarmAction (Блок 4): Токены Gemini, сокет :1015 active, комбо-маршруты
- StartOmniRouteAction (Блок 5): Изолированный запуск tmux -L omniroute (:20128)
- OmniAionDeployPipeline: Мастер-конвейер запуска всех или выбранных блоков
"""

from __future__ import annotations

from typing import Type

from actions.base import ActionResult, BaseAction
from Omni_Aion.actions.prepare_runtime import PrepareRuntimeAction
from Omni_Aion.actions.setup_aionui import SetupAionUiAction
from Omni_Aion.actions.setup_omniroute_core import SetupOmniRouteCoreAction
from Omni_Aion.actions.sync_gemini_farm import SyncGeminiFarmAction
from Omni_Aion.actions.start_omniroute import StartOmniRouteAction
from Omni_Aion.actions.pipeline import OmniAionDeployPipeline

OMNI_AION_ACTIONS_REGISTRY: dict[str, Type[BaseAction]] = {
    "prepare_runtime": PrepareRuntimeAction,
    "setup_aionui": SetupAionUiAction,
    "setup_omniroute_core": SetupOmniRouteCoreAction,
    "sync_gemini_farm": SyncGeminiFarmAction,
    "start_omniroute": StartOmniRouteAction,
    "pipeline": OmniAionDeployPipeline,
}

__all__ = [
    "PrepareRuntimeAction",
    "SetupAionUiAction",
    "SetupOmniRouteCoreAction",
    "SyncGeminiFarmAction",
    "StartOmniRouteAction",
    "OmniAionDeployPipeline",
    "OMNI_AION_ACTIONS_REGISTRY",
    "run_all_blocks",
    "run_block",
]


def run_all_blocks(
    distro: str = "Ubuntu",
    force_restart: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> ActionResult:
    """Запускает полный сквозной конвейер развертывания Omni_Aion."""
    pipe = OmniAionDeployPipeline(
        distro=distro, force_restart=force_restart, dry_run=dry_run, verbose=verbose
    )
    return pipe.run()


def run_block(
    block_num: int,
    distro: str = "Ubuntu",
    force_restart: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> ActionResult:
    """Запускает конкретный блок действий (1-5)."""
    pipe = OmniAionDeployPipeline(
        distro=distro, force_restart=force_restart, dry_run=dry_run, verbose=verbose
    )
    return pipe.run_block(block_num)
