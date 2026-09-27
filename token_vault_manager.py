"""Модуль управления токенами учетных записей (Шифрование отключено).

Все токены рабочих узлов (Gemini и Claude) хранятся в открытом виде (стандартный JSON)
без промежуточного шифрования AES-GCM и без создания файлов .enc.
Функции модуля сохранены как безопасные заглушки (no-op) для обратной совместимости.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LAST_GLOBAL_METADATA_UPDATE = 0.0


def _get_token_paths() -> list[Path]:
    """Пути к токенам (всегда в открытом виде)."""
    return []


def is_vault_locked() -> bool:
    """Шифрование отключено — токены всегда доступны."""
    return False


def lock_tokens(password: str = "") -> tuple[bool, str]:
    """No-op: шифрование токенов отключено."""
    return True, "Шифрование токенов отключено. Все токены хранятся в открытом виде."


def unlock_tokens(password: str = "") -> tuple[bool, str]:
    """No-op: токены уже находятся в открытом виде."""
    return True, "Шифрование токенов отключено. Все токены доступны."


def update_all_metadata() -> None:
    """Обновление метаданных открытых токенов."""
    pass


@contextmanager
def auto_unlock_context(password: str | None = None):
    """Контекстный менеджер-заглушка (токены всегда расшифрованы)."""
    yield


def ensure_locked() -> None:
    """No-op: автоблокировка отключена."""
    pass


def register_vault_interaction() -> None:
    """No-op."""
    pass


def start_vault_watchdog() -> None:
    """No-op: фоновый сторожевой таймер блокировки отключен."""
    pass


if __name__ == "__main__":
    print("Статус хранилища: ШИФРОВАНИЕ ОТКЛЮЧЕНО (Токены всегда доступны)")
