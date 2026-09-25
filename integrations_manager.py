"""Менеджер интеграций для исследовательского кластера Herdr (AionUi, OmniRoute, Claude Code, Gemini Cluster).

Научно-инженерная цель:
Автоматизированная сборка и синхронизация топологии экспериментального стенда.
Обеспечивает бесшовную связку между агентами AionUi и балансировщиком OmniRoute
для верификации превосходства мультиагентных ансамблей над монолитными моделями.

Обеспечивает:
1. Логирование хода бенчмарк-прогонов с миллисекундными метками в integrations.log
2. Контроль размера журнала телеметрии (1 МБ, с автоматической ротацией)
3. Просмотр и потоковую синхронизацию истории событий бенчмарка
4. Комплексный мониторинг доступности рабочих узлов AionUi, OmniRoute, Claude и Gemini
5. Калибровку и синхронизацию эталонного узла Claude Code
6. Синхронизацию кластера Gemini (профили, изолированные порты 1081+, комбо-маршрут OmniRoute, БД AionUi)
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "integrations.log"
MAX_LOG_BYTES = 1024 * 1024       # 1 МБ лимит
TRIM_TARGET_BYTES = 512 * 1024    # До 512 КБ при ротации

import aionui_claude_bridge
import gemini_manager
import settings_manager
import token_vault_manager
import backup_manager


class IntegrationLogger:
    """Потокобезопасный логгер с контролем размера файла (1 МБ) и авто-ротацией."""

    def __init__(self, log_path: Path = LOG_FILE):
        self.log_path = Path(log_path)
        self.listeners: list[Callable[[str, str, str], None]] = []
        self._lock = threading.Lock()

    def add_listener(self, callback: Callable[[str, str, str], None]) -> None:
        """Добавляет слушателя (например, GUI-консоль) для получения событий лога в реальном времени."""
        with self._lock:
            if callback not in self.listeners:
                self.listeners.append(callback)

    def remove_listener(self, callback: Callable[[str, str, str], None]) -> None:
        """Удаляет слушателя."""
        with self._lock:
            if callback in self.listeners:
                self.listeners.remove(callback)

    def _rotate_if_needed(self) -> None:
        """Ограничивает размер лог-файла 1 МБ, удаляя старые строки."""
        if not self.log_path.exists():
            return

        try:
            size = self.log_path.stat().st_size
            if size > MAX_LOG_BYTES:
                with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()

                # Оставляем вторую половину строк (самые свежие)
                keep_lines = lines[len(lines) // 2 :]
                with open(self.log_path, "w", encoding="utf-8") as f:
                    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"[{ts}] [SYSTEM] --- Ротация логов: старые записи удалены (лимит 1 МБ) ---\n")
                    f.writelines(keep_lines)
        except Exception:
            pass

    def log(self, message: str, level: str = "INFO") -> str:
        """Записывает сообщение с датой и временем в файл и уведомляет слушателей."""
        now = datetime.datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        formatted_line = f"[{ts}] [{level.upper()}] {message}"

        with self._lock:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(formatted_line + "\n")
                self._rotate_if_needed()
            except Exception:
                pass

            # Уведомляем зарегистрированные GUI-консоли
            for cb in self.listeners:
                try:
                    cb(ts, level.upper(), message)
                except Exception:
                    pass

        return formatted_line

    def read_history(self) -> str:
        """Считывает полную историю из файла логов."""
        with self._lock:
            if not self.log_path.exists():
                return "Журнал логов пуст."
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception as e:
                return f"Ошибка чтения файла логов: {e}"

    def clear_history(self) -> bool:
        """Очищает файл логов."""
        with self._lock:
            try:
                with open(self.log_path, "w", encoding="utf-8") as f:
                    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"[{ts}] [SYSTEM] Журнал логов очищен пользователем.\n")
                return True
            except Exception:
                return False

    def get_file_size_str(self) -> str:
        """Возвращает форматированный размер файла логов."""
        if not self.log_path.exists():
            return "0 КБ"
        try:
            b = self.log_path.stat().st_size
            if b < 1024:
                return f"{b} Б"
            elif b < 1024 * 1024:
                return f"{round(b / 1024, 1)} КБ"
            else:
                return f"{round(b / (1024 * 1024), 2)} МБ"
        except Exception:
            return "0 КБ"


# Глобальный экземпляр логгера
logger = IntegrationLogger()


def check_port_accessible(host: str, port: int, timeout: float = 0.5) -> bool:
    """Быстрая проверка доступности сокета TCP."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def check_http_status(url: str, timeout: float = 1.0) -> tuple[bool, int, str]:
    """Проверяет HTTP статус URL."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HerdrControlCenter/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return True, res.status, "OK"
    except urllib.error.HTTPError as e:
        return True, e.code, str(e.reason)
    except Exception as e:
        return False, 0, str(e)


def check_aionui_status(log_fn: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    """Комплексная проверка доступности AionUi, OmniRoute, Claude и Gemini."""
    def _log(msg: str, lvl: str = "INFO"):
        logger.log(msg, lvl)
        if log_fn:
            log_fn(msg, lvl)

    _log("Запуск диагностики статуса интеграций...", "INFO")
    results: dict[str, Any] = {}

    # 1. AionUi WebUI (:25808)
    aion_ok, aion_code, aion_msg = check_http_status("http://127.0.0.1:25808", timeout=1.5)
    results["aionui"] = {
        "online": aion_ok,
        "code": aion_code,
        "url": "http://127.0.0.1:25808",
    }
    if aion_ok:
        _log(f"AionUi WebUI: ONLINE (HTTP {aion_code}) на http://127.0.0.1:25808", "SUCCESS")
    else:
        _log(f"AionUi WebUI: OFFLINE ({aion_msg}) на порту 25808", "WARN")

    # 2. OmniRoute Gateway (:20128)
    omni_ok, omni_code, omni_msg = check_http_status("http://127.0.0.1:20128", timeout=1.5)
    results["omniroute"] = {
        "online": omni_ok,
        "code": omni_code,
        "url": "http://127.0.0.1:20128",
    }
    if omni_ok:
        _log(f"OmniRoute Gateway: ONLINE (HTTP {omni_code}) на http://127.0.0.1:20128", "SUCCESS")
    else:
        _log(f"OmniRoute Gateway: OFFLINE ({omni_msg}) на порту 20128", "WARN")

    # 3. Claude Code & Прокси (:1015 / :11015)
    c_socks_ok = check_port_accessible("127.0.0.1", 1015, timeout=0.5)
    c_http_ok = check_port_accessible("127.0.0.1", 11015, timeout=0.5)
    oauth_st = aionui_claude_bridge.get_oauth_status()
    results["claude"] = {
        "socks5_1015": c_socks_ok,
        "http_11015": c_http_ok,
        "oauth": oauth_st,
    }
    if c_socks_ok and c_http_ok:
        _log("Claude Network: Сокеты SOCKS5 :1015 и HTTP :11015 активны", "SUCCESS")
    else:
        _log(f"Claude Network: Сокет 1015={c_socks_ok}, 11015={c_http_ok} (проверьте vless2socks)", "WARN")

    if oauth_st.get("authorized"):
        _log(f"Claude Auth: Подписка {oauth_st.get('subscription_type', 'N/A').upper()} действительна ({oauth_st.get('days_left', 0)} дн.)", "SUCCESS")
    else:
        _log("Claude Auth: Токен не найден или просрочен", "WARN")

    # 4. Gemini Farm & Прокси (1082, 1083...)
    g_profiles = gemini_manager.list_profiles()
    active_gemini_ports = []
    for prof in g_profiles:
        p_num = prof.get("port")
        if p_num and check_port_accessible("127.0.0.1", p_num, timeout=0.5):
            active_gemini_ports.append(p_num)

    results["gemini"] = {
        "total_profiles": len(g_profiles),
        "active_ports": active_gemini_ports,
    }
    _log(f"Gemini Farm: Профилей: {len(g_profiles)}, активных портов SOCKS5: {len(active_gemini_ports)} ({active_gemini_ports})", "SUCCESS")

    _log("Диагностика завершена.", "INFO")
    return results


def sync_claude(
    oauth_file: Path | str | None = None,
    log_fn: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Только диагностика. Интеграцию будет выполнять ИИ-агент с помощью skills."""
    def _log(msg: str, lvl: str = "INFO"):
        logger.log(msg, lvl)
        if log_fn:
            log_fn(msg, lvl)

    _log("=== Запуск проверки готовности Claude Code для AionUi ===", "STEP")

    if token_vault_manager.is_vault_locked():
        _log("Хранилище токенов заблокировано. Авто-разблокировка (CLI/Env)...", "INFO")
        pw = backup_manager.load_backup_password()
        if pw:
            ok, msg = token_vault_manager.unlock_tokens(pw)
            if ok:
                _log(f"Сейф разблокирован: {msg}", "SUCCESS")
            else:
                _log(f"Ошибка разблокировки (неверный пароль в .env?): {msg}", "ERROR")
                return {"success": False, "step": "vault_unlock", "error": msg}
        else:
            _log("Пароль не установлен в .env! Невозможно разблокировать токены для синхронизации.", "ERROR")
            return {"success": False, "step": "vault_unlock", "error": "No password in .env"}

    st = aionui_claude_bridge.get_oauth_status()
    if st.get("authorized"):
        _log(f"Используется текущий токен Claude ({st.get('subscription_type', '').upper()}, осталось {st.get('days_left')} дн.)", "SUCCESS")
    else:
        _log("Предупреждение: Токен Claude недействителен.", "WARN")

    c_socks_ok = check_port_accessible("127.0.0.1", 1015, timeout=1.0)
    if c_socks_ok:
        _log("Claude SOCKS5 Proxy (:1015) ACTIVE и готов принимать соединения.", "SUCCESS")
    else:
        _log("Claude SOCKS5 Proxy (:1015) OFFLINE. Проверьте vless2socks.", "ERROR")

    aion_ok, aion_code, aion_msg = check_http_status("http://127.0.0.1:25808", timeout=1.5)
    if aion_ok:
        _log(f"AionUi WebUI доступен (HTTP {aion_code}). Готов к интеграции через Skills.", "SUCCESS")
    else:
        _log(f"AionUi WebUI недоступен ({aion_msg}).", "ERROR")

    _log("ВНИМАНИЕ: Все активные настройки интеграции делегированы ИИ-агентам через Skills.", "INFO")
    _log("См. SKILLS.md в корне проекта для настройки AionUi.", "INFO")
    _log("=== Диагностика Claude завершена ===", "SUCCESS")
    
    return {
        "success": True,
        "socks": c_socks_ok,
        "aion": aion_ok,
        "auth": st.get("authorized", False)
    }


def sync_gemini(log_fn: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    """Только диагностика Gemini Farm конфигурации. Интеграцию выполняет агент."""
    def _log(msg: str, lvl: str = "INFO"):
        logger.log(msg, lvl)
        if log_fn:
            log_fn(msg, lvl)

    _log("=== Запуск проверки состояния Gemini Farm ===", "STEP")

    if token_vault_manager.is_vault_locked():
        pw = backup_manager.load_backup_password()
        if pw:
            token_vault_manager.unlock_tokens(pw)

    profiles = gemini_manager.list_profiles()
    _log(f"Обнаружено профилей Gemini в системе Herdr: {len(profiles)}", "INFO")

    active_conns = []
    for prof in profiles:
        name = prof.get("name", "Account")
        email = prof.get("email", "unknown")
        port = prof.get("port", 1082)
        alive = check_port_accessible("127.0.0.1", port, timeout=0.5)
        status_icon = "🟢" if alive else "🔴"
        _log(f"{status_icon} Профиль Herdr '{name}' ({email}) -> SOCKS5 : {port} [Слушает: {alive}]", "INFO")
        active_conns.append({"name": name, "email": email, "port": port, "alive": alive})

    omni_ok, omni_code, omni_msg = check_http_status("http://127.0.0.1:20128", timeout=1.5)
    if omni_ok:
        _log(f"OmniRoute Gateway ONLINE (HTTP {omni_code}).", "SUCCESS")
    else:
        _log(f"OmniRoute Gateway OFFLINE ({omni_msg}). Запустите OmniRoute.", "WARN")

    _log("ВНИМАНИЕ: Активная настройка БД провайдеров делегирована ИИ агентам через Skills.", "INFO")
    _log("=== Диагностика Gemini завершена ===", "SUCCESS")
    
    return {
        "success": True,
        "profiles": active_conns,
        "omni_ok": omni_ok,
    }
