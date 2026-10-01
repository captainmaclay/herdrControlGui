"""
Omni_Aion: Автономный модуль развертывания, управления и самотестирования стека
OmniRoute + AionUi 2.1.47 + Gemini OAuth Farm для Herdr Control Center.

Поддерживает:
- Сборку дистрибутива (.hbin) с шифрованием мастер-ключом AES-256-GCM.
- Однократную установку с расшифровкой учетных данных и токенов Gemini.
- Полную автономную работу без паролей при повседневных запусках.
- Сквозную диагностику и автоматическое самотестирование (self-test).
"""

from .stack_bundle_manager import (
    build_stack_bundle,
    install_stack_bundle,
    verify_stack_services,
    compute_key_fingerprint,
    find_latest_bundle,
    MAGIC_HEADER,
    DEFAULT_BUNDLE_DIR,
    DEFAULT_BUNDLE_NAME,
)
from .actions import (
    PrepareRuntimeAction,
    SetupAionUiAction,
    SetupOmniRouteCoreAction,
    SyncGeminiFarmAction,
    StartOmniRouteAction,
    OmniAionDeployPipeline,
    run_all_blocks,
    run_block,
)

__all__ = [
    "build_stack_bundle",
    "install_stack_bundle",
    "verify_stack_services",
    "compute_key_fingerprint",
    "find_latest_bundle",
    "MAGIC_HEADER",
    "DEFAULT_BUNDLE_DIR",
    "DEFAULT_BUNDLE_NAME",
    "PrepareRuntimeAction",
    "SetupAionUiAction",
    "SetupOmniRouteCoreAction",
    "SyncGeminiFarmAction",
    "StartOmniRouteAction",
    "OmniAionDeployPipeline",
    "run_all_blocks",
    "run_block",
]
