"""Пакет автоматизированных системных действий Herdr Control Center (actions).

Предоставляет программный и командный интерфейс для:
- Установки и настройки Claude Code CLI и Claude Desktop GUI в WSL2
- Диагностики и исцеления мигания/дребезга Killswitch
- Управления сетевой изоляцией ядра Linux в WSL2 (Zero-Leak Jail)
- Сквозной верификации маршрутизации туннелей и Anthropic API
"""

from __future__ import annotations

from typing import Type

from actions.base import ActionResult, BaseAction
from actions.install_claude_wsl import InstallClaudeWslAction
from actions.killswitch_heal import KillswitchHealAction
from actions.verify_connectivity import VerifyConnectivityAction
from actions.wsl_isolation import WslIsolationAction
from Omni_Aion.actions.pipeline import OmniAionDeployPipeline

ACTIONS_REGISTRY: dict[str, Type[BaseAction]] = {
    "install_claude_wsl": InstallClaudeWslAction,
    "killswitch_heal": KillswitchHealAction,
    "wsl_isolation": WslIsolationAction,
    "verify_connectivity": VerifyConnectivityAction,
    "omni_aion_pipeline": OmniAionDeployPipeline,
}

__all__ = [
    "ActionResult",
    "BaseAction",
    "InstallClaudeWslAction",
    "KillswitchHealAction",
    "WslIsolationAction",
    "VerifyConnectivityAction",
    "ACTIONS_REGISTRY",
    "get_action",
    "list_actions",
]


def list_actions() -> list[dict[str, str]]:
    """Возвращает список всех зарегистрированных действий с описаниями."""
    return [
        {"name": name, "description": cls.description}
        for name, cls in ACTIONS_REGISTRY.items()
    ]


def get_action(name: str, **kwargs) -> BaseAction:
    """Создает экземпляр зарегистрированного действия по его имени."""
    if name not in ACTIONS_REGISTRY:
        raise KeyError(
            f"Действие '{name}' не найдено. Доступные действия: {list(ACTIONS_REGISTRY.keys())}"
        )
    return ACTIONS_REGISTRY[name](**kwargs)
