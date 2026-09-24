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
    """Определяет активного пользователя WSL2 без блокирующих вызовов сети."""
    return (os.environ.get("WSL_USER") or os.environ.get("USERNAME") or "default").strip()


WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")
WSL_USER = get_wsl_user(WSL_DISTRO)
WSL_SETTINGS_FILE = Path(rf"\\wsl$\{WSL_DISTRO}\home\{WSL_USER}\.gemini\antigravity-cli\settings.json")

DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "en",                 # Default interface language ('en' or 'ru')
    "auto_proxy_failover": True,      # Автоподбор Proxy при сбое подключения (той же страны)
    "auto_refresh_routes": True,      # Автопроверка каждые 30 сек
    "auto_backup_enabled": False,     # Автобэкап по умолчанию выключен
    "backup_interval_hours": 12,      # Интервал автобэкапа
    "backup_dir": ".",                # Папка бэкапов по умолчанию
    "last_backup_time": "-",          # Время последнего бэкапа
    "account_proxy_bindings": {},     # Ручные привязки аккаунтов к портам: {profile_name: {"port": int, "manual": bool}}
    "claude_proxy_host": "127.0.0.1", # Хост прокси для Claude Code
    "claude_proxy_port": 1015,        # Порт прокси для Claude Code (по умолчанию 1015 System Proxy)
    "claude_killswitch": True,        # Killswitch для Claude (по умолчанию включен)
    "claude_node_isolate": True,      # Изоляция Node.js/claude.exe в брандмауэре (Windows)
    "port_check_interval_seconds": 2.0, # Интервал быстрой проверки доступности портов и Killswitch (сек)
}


def load_settings() -> dict[str, Any]:
    """Загружает настройки из settings.json."""
    if not SETTINGS_FILE.exists():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and (Path(meipass) / "settings.json").exists():
            try:
                with open(Path(meipass) / "settings.json", "r", encoding="utf-8") as mf:
                    b_data = json.load(mf)
                if isinstance(b_data, dict):
                    merged = dict(DEFAULT_SETTINGS)
                    merged.update(b_data)
                    save_settings(merged)
                    return merged
            except Exception:
                pass
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


def get_account_proxy_binding(profile_name: str) -> dict[str, Any] | None:
    """Возвращает данные привязки прокси для профиля (port, manual)."""
    s = load_settings()
    bindings = s.get("account_proxy_bindings", {})
    if isinstance(bindings, dict):
        return bindings.get(profile_name)
    return None


def set_account_proxy_binding(profile_name: str, port: int, manual: bool = True) -> None:
    """Сохраняет привязку прокси для профиля (с отметкой ручного выбора)."""
    s = load_settings()
    if "account_proxy_bindings" not in s or not isinstance(s["account_proxy_bindings"], dict):
        s["account_proxy_bindings"] = {}
    s["account_proxy_bindings"][profile_name] = {"port": int(port), "manual": bool(manual)}
    save_settings(s)


def clear_account_proxy_binding(profile_name: str) -> None:
    """Удаляет привязку прокси для указанного профиля."""
    s = load_settings()
    bindings = s.get("account_proxy_bindings", {})
    if isinstance(bindings, dict) and profile_name in bindings:
        del bindings[profile_name]
        s["account_proxy_bindings"] = bindings
        save_settings(s)


def clear_all_account_proxy_bindings() -> None:
    """Сбрасывает все индивидуальные привязки прокси для возврата к последовательному порядку."""
    s = load_settings()
    s["account_proxy_bindings"] = {}
    save_settings(s)


def get_claude_proxy_host() -> str:
    """Возвращает настроенный хост прокси для Claude Code."""
    return str(get_setting("claude_proxy_host", DEFAULT_SETTINGS["claude_proxy_host"])).strip() or "127.0.0.1"


def get_claude_proxy_port() -> int:
    """Возвращает настроенный порт прокси для Claude Code."""
    val = get_setting("claude_proxy_port", DEFAULT_SETTINGS["claude_proxy_port"])
    try:
        return int(val)
    except (ValueError, TypeError):
        return 1015


def get_claude_killswitch() -> bool:
    """Возвращает статус флага Killswitch для Claude Code (по умолчанию True)."""
    return bool(get_setting("claude_killswitch", DEFAULT_SETTINGS["claude_killswitch"]))


def get_claude_node_isolate() -> bool:
    """Возвращает статус флага жесткой изоляции Node.js (брандмауэр Windows)."""
    return bool(get_setting("claude_node_isolate", DEFAULT_SETTINGS["claude_node_isolate"]))


def set_claude_proxy_settings(host: str, port: int, killswitch: bool, node_isolate: bool = False) -> None:
    """Сохраняет настройки прокси и Killswitch для Claude Code в settings.json."""
    s = load_settings()
    s["claude_proxy_host"] = str(host).strip() or "127.0.0.1"
    try:
        s["claude_proxy_port"] = int(port)
    except (ValueError, TypeError):
        s["claude_proxy_port"] = 1015
    s["claude_killswitch"] = bool(killswitch)
    s["claude_node_isolate"] = bool(node_isolate)
    save_settings(s)


def get_port_check_interval() -> float:
    """Возвращает интервал быстрой проверки доступности локальных портов (в секундах)."""
    try:
        val = float(get_setting("port_check_interval_seconds", DEFAULT_SETTINGS["port_check_interval_seconds"]))
        return max(0.5, min(30.0, val))
    except (ValueError, TypeError):
        return 2.0


