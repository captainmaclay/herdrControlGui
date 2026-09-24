"""Менеджер интеграций для HerdrControlCenter (AionUi, OmniRoute, Claude Code, Gemini Farm).

Обеспечивает:
1. Логирование хода выполнения с метками времени (дата и время) в консоль и файл integrations.log.
2. Контроль размера файла логов (максимум 1 МБ, при превышении старые строки удаляются).
3. Просмотр и синхронизацию истории логов из файла.
4. Комплексную проверку статуса компонентов AionUi, OmniRoute, Claude Code и Gemini Farm.
5. Отдельную синхронизацию Claude Code (OAuth, прокси, патч ресурсов AionUi).
6. Отдельную синхронизацию Gemini Farm (профили, порты 1081+, комбо OmniRoute, БД AionUi, пинг).
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
    """Синхронизирует Claude Code: OAuth2 токен, прокси, Killswitch, ресурсы AionUi и БД."""
    def _log(msg: str, lvl: str = "INFO"):
        logger.log(msg, lvl)
        if log_fn:
            log_fn(msg, lvl)

    _log("=== Запуск синхронизации Claude Code для AionUi ===", "STEP")

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

    # 1. OAuth токен
    if oauth_file:
        _log(f"Импорт нового OAuth файла: {oauth_file}", "INFO")
        imp_res = aionui_claude_bridge.import_oauth_file(oauth_file)
        if imp_res.get("success"):
            _log(f"OAuth токен успешно импортирован (Подписка: {imp_res.get('subscription_type')})", "SUCCESS")
        else:
            _log(f"Ошибка импорта OAuth: {imp_res.get('error')}", "ERROR")
            return {"success": False, "step": "oauth_import", "error": imp_res.get("error")}
    else:
        st = aionui_claude_bridge.get_oauth_status()
        if st.get("authorized"):
            _log(f"Используется текущий токен Claude ({st.get('subscription_type', '').upper()}, осталось {st.get('days_left')} дн.)", "SUCCESS")
        else:
            _log("Предупреждение: Действующий OAuth токен Claude не обнаружен в .credentials.json", "WARN")

    # 2. Сетевые настройки и Killswitch
    _log("Применение настроек прокси (:1015 / :11015) и флага Killswitch...", "INFO")
    p_res = aionui_claude_bridge.ensure_claude_proxy_settings(port=1015, killswitch=True)
    if p_res.get("success"):
        _log("Файлы settings.json (Windows и WSL) успешно обновлены", "SUCCESS")
    else:
        _log(f"Ошибка настройки прокси: {p_res.get('error')}", "ERROR")

    # 3. Патч ресурсов AionUi
    _log("Обновление встроенного бинарника Claude в AionUi до v2.1.280...", "INFO")
    res_patch = aionui_claude_bridge.patch_aionui_managed_resources(target_version="2.1.280")
    if res_patch.get("success"):
        _log("Ресурсы AionUi и manifest.json успешно пропатчены до версии 2.1.280", "SUCCESS")
    else:
        _log(f"Ошибка патча ресурсов AionUi: {res_patch.get('error')}", "WARN")

    # 4. Регистрация в базе данных AionUi
    _log("Регистрация команды 'claude' в базе данных AionUi (agent_metadata)...", "INFO")
    db_patch = aionui_claude_bridge.patch_aionui_database()
    if db_patch.get("success"):
        _log(f"База данных AionUi обновлена (изменено строк: {db_patch.get('rows_affected')})", "SUCCESS")
    else:
        _log(f"Ошибка обновления базы AionUi: {db_patch.get('error')}", "WARN")

    _log("=== Синхронизация Claude Code успешно завершена ===", "SUCCESS")
    return {
        "success": True,
        "proxy": p_res,
        "resources": res_patch,
        "db": db_patch,
    }


def sync_gemini(log_fn: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    """Синхронизирует Gemini Farm: профили, изолированные SOCKS5 порты 1081+, OmniRoute Combo и AionUi."""
    def _log(msg: str, lvl: str = "INFO"):
        logger.log(msg, lvl)
        if log_fn:
            log_fn(msg, lvl)

    _log("=== Запуск синхронизации Gemini Farm для AionUi ===", "STEP")

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

    # 1. Профили
    profiles = gemini_manager.list_profiles()
    _log(f"Обнаружено профилей Gemini в системе: {len(profiles)}", "INFO")

    active_conns = []
    for prof in profiles:
        name = prof.get("name", "Account")
        email = prof.get("email", "unknown")
        port = prof.get("port", 1082)
        alive = check_port_accessible("127.0.0.1", port, timeout=0.5)
        status_icon = "🟢" if alive else "🔴"
        _log(f"{status_icon} Профиль '{name}' ({email}) ➔ SOCKS5 : {port} [Доступен: {alive}]", "INFO")
        active_conns.append({"name": name, "email": email, "port": port, "alive": alive})

    # 2. Проверка регистрации провайдера в AionUi
    _log("Проверка регистрации кастомного провайдера gemini-farm в AionUi...", "INFO")
    wsl_root = aionui_claude_bridge.get_wsl_rootfs_path()
    if wsl_root:
        db_path = wsl_root / f"home/{aionui_claude_bridge.WSL_USER}/.aionui-web/aionui-backend.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path), timeout=5)
                cur = conn.cursor()
                rows = cur.execute("SELECT id, name, base_url, models FROM providers WHERE models LIKE '%gemini-farm%'").fetchall()
                if rows:
                    _log(f"Провайдер 'gemini-farm' уже зарегистрирован в AionUi (Endpoint: {rows[0][2]})", "SUCCESS")
                else:
                    # Добавляем если отсутствует
                    cur.execute("""
                        INSERT OR REPLACE INTO providers (id, platform, name, base_url, models, enabled)
                        VALUES ('custom_gemini_farm', 'custom', 'OmniRoute Gemini Farm', 'http://127.0.0.1:20128/v1', '["gemini-farm"]', 1)
                    """)
                    conn.commit()
                    _log("Провайдер 'gemini-farm' успешно зарегистрирован в AionUi", "SUCCESS")
                conn.close()
            except Exception as e:
                _log(f"Замечание по БД AionUi: {e}", "WARN")

    # 3. Тест пинга к модели через OmniRoute
    _log("Тестовый запрос к модели 'gemini-farm' через OmniRoute (:20128)...", "INFO")
    try:
        req_data = json.dumps({
            "model": "gemini-farm",
            "messages": [{"role": "user", "content": "Ping"}]
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:20128/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-omniroute-secret",
                "Content-Type": "application/json"
            },
            data=req_data
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            _log(f"Успешный ответ от Gemini Farm: '{reply[:60]}...'", "SUCCESS")
            ping_ok = True
    except Exception as e:
        _log(f"Запрос к gemini-farm завершился с замечанием: {e}", "WARN")
        ping_ok = False

    _log("=== Синхронизация Gemini Farm завершена ===", "SUCCESS")
    return {
        "success": True,
        "profiles": active_conns,
        "ping_ok": ping_ok,
    }
