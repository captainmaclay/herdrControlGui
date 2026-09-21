"""Менеджер постоянных настроек для Herdr Control Center.

Сохраняет настройки между перезапусками в settings.json и синхронизирует с WSL2:
- auto_proxy_failover: Автоподбор рабочего SOCKS5-прокси при сбое (приоритет той же страны)
- auto_refresh_routes: Автопроверка маршрутов каждые 30 секунд
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "settings.json"

import os
import subprocess

def get_wsl_user(distro: str = "Ubuntu") -> str:
    """Определяет активного пользователя WSL2."""
    env_user = os.environ.get("WSL_USER")
    if env_user:
        return env_user
    try:
        res = subprocess.run(["wsl", "-d", distro, "whoami"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return os.environ.get("USERNAME", "default")


WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")
WSL_USER = get_wsl_user(WSL_DISTRO)
WSL_SETTINGS_FILE = Path(rf"\\wsl$\{WSL_DISTRO}\home\{WSL_USER}\.gemini\antigravity-cli\settings.json")

DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "en",                 # Default interface language ('en' or 'ru')
    "auto_proxy_failover": True,      # Автоподбор Proxy при сбое подключения (той же страны)
    "auto_refresh_routes": True,      # Автопроверка каждые 30 сек
}


def load_settings() -> dict[str, Any]:
    """Загружает настройки из settings.json."""
    if not SETTINGS_FILE.exists():
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict[str, Any]) -> None:
    """Сохраняет настройки в settings.json и синхронизирует с WSL2."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    # Синхронизация с WSL2
    try:
        WSL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WSL_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def get_setting(key: str, default: Any = None) -> Any:
    """Возвращает значение конкретной настройки."""
    s = load_settings()
    return s.get(key, default)


def set_setting(key: str, value: Any) -> None:
    """Устанавливает и сохраняет конкретную настройку."""
    s = load_settings()
    s[key] = value
    save_settings(s)
