"""Модуль мониторинга сетевых маршрутов Herdr (WSL2).

Отслеживает:
- Состояние сервера Herdr и активных агентов (agy, claude) внутри WSL2
- Статус туннеля SOCKS5 активного аккаунта Gemini (порт 1081, 1082, 1083...)
- Прямое подключение через оригинальный IP для моделей Anthropic Claude
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any

import requests
import gemini_manager
import proxy_manager
import settings_manager
import strategy_manager
import claude_manager

SOCKS5_HOST = "127.0.0.1"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com"
IPIFY_ENDPOINT = "https://api.ipify.org"


def check_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Проверяет, слушается ли локальный порт."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def get_public_ip(timeout: float = 2.0) -> str:
    """Получает внешний IP адрес напрямую (без прокси)."""
    try:
        resp = requests.get(IPIFY_ENDPOINT, timeout=timeout, headers={"User-Agent": "HerdrRouter/1.0"})
        return resp.text.strip()
    except Exception:
        return "Не определён"


# ── Состояние автовозврата на забинденный SOCKS5 (Failback Recovery State) ────
_FAILBACK_STATE: dict[str, Any] = {
    "target_port": None,           # Забинденный целевой порт
    "attempts": 0,                 # Количество проверок восстановления
    "next_check_time": 0.0,        # Timestamp следующей проверки
    "last_interval": 30.0,         # Текущий интервал в секундах
}


def get_failback_interval(attempts: int) -> float:
    """Вычисляет интервал повторной проверки забинденного порта (30 сек, затем реже):
    - Попытки 1..4: каждые 30 секунд (первые 2 минуты)
    - Попытки 5..7: каждые 60 секунд (1 минута)
    - Попытки 8..10: каждые 120 секунд (2 минуты)
    - Попытка 11+: каждые 300 секунд (5 минут)
    """
    if attempts < 5:
        return 30.0
    elif attempts < 8:
        return 60.0
    elif attempts < 11:
        return 120.0
    else:
        return 300.0


def get_failback_status() -> dict[str, Any]:
    """Возвращает информацию о текущем состоянии автовозврата на забинденный порт."""
    bound_port = gemini_manager.get_active_bound_port()
    active_port = gemini_manager.get_active_proxy_port()
    in_failover = (active_port != bound_port)
    now = time.time()
    next_check = _FAILBACK_STATE.get("next_check_time", 0.0)
    remaining = max(0, int(round(next_check - now))) if next_check > now else 0

    return {
        "in_failover": in_failover,
        "bound_port": bound_port,
        "active_port": active_port,
        "attempts": _FAILBACK_STATE.get("attempts", 0),
        "next_check_seconds": remaining,
        "interval_seconds": _FAILBACK_STATE.get("last_interval", 30.0),
    }


def reset_failback_state() -> None:
    """Сбрасывает состояние автовозврата."""
    _FAILBACK_STATE["target_port"] = None
    _FAILBACK_STATE["attempts"] = 0
    _FAILBACK_STATE["next_check_time"] = 0.0
    _FAILBACK_STATE["last_interval"] = 30.0


def probe_gemini_routing() -> dict[str, Any]:
    """Проверяет доступность моделей Gemini через SOCKS5 прокси.
    
    1. Если аккаунт находится в режиме failover (активный порт != забинденный порт),
       стремится восстановить связь с забинденным портом (проверки каждые 30с, затем реже).
    2. При восстановлении забинденного порта мгновенно производит автовозврат (failback).
    3. При сбое текущего порта подбирает рабочий прокси через auto_proxy_failover.
    """
    active_port = gemini_manager.get_active_proxy_port()
    bound_port = gemini_manager.get_active_bound_port()
    in_failover = (active_port != bound_port)
    now = time.time()

    # ── 1. Проверка автовозврата на забинденный порт (Failback check) ──────────
    if in_failover:
        if _FAILBACK_STATE["target_port"] != bound_port:
            _FAILBACK_STATE["target_port"] = bound_port
            _FAILBACK_STATE["attempts"] = 0
            _FAILBACK_STATE["next_check_time"] = now  # Проверить сразу при первой возможности
            _FAILBACK_STATE["last_interval"] = 30.0

        if now >= _FAILBACK_STATE["next_check_time"]:
            # Проверяем доступность забинденного порта (только если он не занят Claude)
            is_claude = proxy_manager.get_proxy_claude_flag(bound_port)
            bound_socks_open = False if is_claude else check_port_open(SOCKS5_HOST, bound_port, timeout=1.0)
            bound_google_ok = False
            b_lat = None

            if bound_socks_open:
                try:
                    b_url = f"socks5h://{SOCKS5_HOST}:{bound_port}"
                    t_b = time.time()
                    resp = requests.get(
                        GEMINI_ENDPOINT,
                        proxies={"http": b_url, "https": b_url},
                        timeout=4.5,
                        headers={"User-Agent": "HerdrRouter/1.0"},
                    )
                    b_lat = int((time.time() - t_b) * 1000)
                    bound_google_ok = True
                except Exception:
                    bound_google_ok = False

            if bound_socks_open and bound_google_ok:
                # ЗАБИНДЕННЫЙ ПОРТ ВОССТАНОВЛЕН! ВЫПОЛНЯЕМ АВТОВОЗВРАТ
                email = gemini_manager.get_active_profile_email()
                prof_name = gemini_manager.get_active_profile_name()
                gemini_manager.write_active_proxy_env(bound_port, email)

                curr_proxies = proxy_manager.load_proxies()
                curr_p = next((p for p in curr_proxies if p.get("port") == bound_port), None)
                p_ip = curr_p.get("ip", "-") if curr_p else "-"
                p_co = curr_p.get("country", "undefined") if curr_p else "undefined"

                msg = f"Забинденный SOCKS5 :{bound_port} ({p_ip} • {p_co}) снова в сети! Автовозврат с :{active_port} ➔ :{bound_port} выполнен."
                try:
                    strategy_manager.log_event(
                        account_email=email or "default",
                        profile_name=prof_name,
                        port=bound_port,
                        ip=p_ip,
                        country=p_co,
                        event="failback_recovery",
                        note=msg,
                    )
                except Exception:
                    pass

                reset_failback_state()
                return {
                    "online": True,
                    "port": bound_port,
                    "bound_port": bound_port,
                    "status_text": f"SOCKS5 возвращен ({SOCKS5_HOST}:{bound_port})",
                    "route": f"socks5h://{SOCKS5_HOST}:{bound_port}",
                    "latency_ms": b_lat,
                    "details": msg,
                    "failback_occurred": True,
                    "failback_msg": msg,
                    "old_port": active_port,
                    "new_port": bound_port,
                }
            else:
                # Забинденный порт пока не отвечает — увеличиваем счетчик и интервал проверки (backoff)
                _FAILBACK_STATE["attempts"] += 1
                interval = get_failback_interval(_FAILBACK_STATE["attempts"])
                _FAILBACK_STATE["last_interval"] = interval
                _FAILBACK_STATE["next_check_time"] = now + interval
    else:
        reset_failback_state()

    # ── 2. Проверка текущего активного порта ──────────────────────────────────
    t0 = time.time()
    socks_open = check_port_open(SOCKS5_HOST, active_port, timeout=1.0)
    google_ok = False
    google_exc = None
    lat = None

    if socks_open:
        try:
            proxy_url = f"socks5h://{SOCKS5_HOST}:{active_port}"
            proxies_dict = {"http": proxy_url, "https": proxy_url}

            resp = requests.get(
                GEMINI_ENDPOINT,
                proxies=proxies_dict,
                timeout=4.5,
                headers={"User-Agent": "HerdrRouter/1.0"},
            )
            lat = int((time.time() - t0) * 1000)
            google_ok = True
        except Exception as exc:
            google_exc = exc

        # Если первая попытка сорвалась (например, временный лаг), повторяем один раз
        if not google_ok:
            time.sleep(0.4)
            try:
                t_retry = time.time()
                resp = requests.get(
                    GEMINI_ENDPOINT,
                    proxies=proxies_dict,
                    timeout=4.5,
                    headers={"User-Agent": "HerdrRouter/1.0"},
                )
                lat = int((time.time() - t_retry) * 1000)
                google_ok = True
                google_exc = None
            except Exception as exc2:
                google_exc = exc2

    if socks_open and google_ok:
        try:
            curr_proxies = proxy_manager.load_proxies()
            curr_p = next((p for p in curr_proxies if p.get("port") == active_port), None)
            email = gemini_manager.get_active_profile_email() or "default"
            strategy_manager.record_connection_tick(
                account_email=email,
                profile_name="active_account",
                port=active_port,
                ip=curr_p.get("ip", "-") if curr_p else "-",
                country=curr_p.get("country", "undefined") if curr_p else "undefined",
                elapsed_seconds=30.0,
                event="active_connection",
                note=f"SOCKS5 :{active_port} стабилен ({lat} ms)",
            )
        except Exception:
            pass

        rem_sec = max(0, int(round(_FAILBACK_STATE["next_check_time"] - time.time()))) if in_failover else 0
        int_sec = int(_FAILBACK_STATE["last_interval"])
        if in_failover:
            st_text = f"SOCKS5 Failover ({SOCKS5_HOST}:{active_port}) ➔ Цель :{bound_port}"
            details = f"Временный Failover на :{active_port}. Стремится вернуться на забинденный :{bound_port} (проверка через {rem_sec}с, интервал {int_sec}с)"
        else:
            st_text = f"SOCKS5 активен ({SOCKS5_HOST}:{active_port})"
            details = f"Трафик Gemini туннелируется через персональный SOCKS5 :{active_port}"

        return {
            "online": True,
            "port": active_port,
            "bound_port": bound_port,
            "in_failover": in_failover,
            "next_failback_sec": rem_sec,
            "status_text": st_text,
            "route": f"socks5h://{SOCKS5_HOST}:{active_port}",
            "latency_ms": lat,
            "details": details,
        }

    # ── 3. Сбой текущего порта -> Auto-Failover ───────────────────────────────
    auto_failover = settings_manager.get_setting("auto_proxy_failover", True)
    if auto_failover:
        curr_proxies = proxy_manager.load_proxies()
        curr_p = next((p for p in curr_proxies if p.get("port") == active_port), None)
        pref_country = curr_p.get("country") if curr_p else None

        ok, msg, best = gemini_manager.handle_proxy_failover(active_port, pref_country)
        if ok and best:
            new_port = best.get("port")
            t_new = time.time()
            new_socks_open = check_port_open(SOCKS5_HOST, new_port, timeout=1.0)
            if new_socks_open:
                try:
                    p_url = f"socks5h://{SOCKS5_HOST}:{new_port}"
                    requests.get(
                        GEMINI_ENDPOINT,
                        proxies={"http": p_url, "https": p_url},
                        timeout=4.5,
                        headers={"User-Agent": "HerdrRouter/1.0"},
                    )
                    new_lat = int((time.time() - t_new) * 1000)

                    try:
                        email = gemini_manager.get_active_profile_email() or "default"
                        strategy_manager.log_event(
                            account_email=email,
                            profile_name="active_account",
                            port=new_port,
                            ip=best.get("ip", "-"),
                            country=best.get("country", "undefined"),
                            event="failover",
                            note=msg,
                        )
                    except Exception:
                        pass

                    # Запускаем отслеживание возврата на забинденный порт!
                    _FAILBACK_STATE["target_port"] = bound_port
                    _FAILBACK_STATE["attempts"] = 0
                    _FAILBACK_STATE["last_interval"] = 30.0
                    _FAILBACK_STATE["next_check_time"] = time.time() + 30.0

                    return {
                        "online": True,
                        "port": new_port,
                        "bound_port": bound_port,
                        "in_failover": True,
                        "next_failback_sec": 30,
                        "status_text": f"SOCKS5 переключен ({SOCKS5_HOST}:{new_port}) ➔ Цель :{bound_port}",
                        "route": f"socks5h://{SOCKS5_HOST}:{new_port}",
                        "latency_ms": new_lat,
                        "details": f"Сбой :{active_port}. {msg}. Стремится вернуться на забинденный :{bound_port}.",
                        "failover_occurred": True,
                        "failover_msg": msg,
                        "old_port": active_port,
                        "new_port": new_port,
                    }
                except Exception as e_new:
                    return {
                        "online": False,
                        "port": new_port,
                        "bound_port": bound_port,
                        "in_failover": True,
                        "status_text": f"Сбой нового SOCKS5 :{new_port}",
                        "route": f"socks5h://{SOCKS5_HOST}:{new_port}",
                        "latency_ms": None,
                        "details": f"Переключено на :{new_port}, но соединение не удалось: {e_new}",
                        "failover_occurred": True,
                        "failover_msg": msg,
                        "old_port": active_port,
                    }

    # Если автоподбор выключен или не найден альтернативный прокси
    if not socks_open:
        return {
            "online": False,
            "port": active_port,
            "status_text": f"Прокси не запущен (порт :{active_port} закрыт)",
            "route": f"socks5h://{SOCKS5_HOST}:{active_port}",
            "latency_ms": None,
            "details": f"Настройте inbound на порту {active_port} в Xray / SOCKS5",
        }
    else:
        return {
            "online": False,
            "port": active_port,
            "status_text": f"Ошибка шлюза SOCKS5 :{active_port}",
            "route": f"socks5h://{SOCKS5_HOST}:{active_port}",
            "latency_ms": None,
            "details": f"Порт {active_port} открыт, но Google API недоступен: {google_exc}",
        }


def probe_claude_routing() -> dict[str, Any]:
    """Проверяет подключение к Anthropic Claude через настроенный прокси с Killswitch."""
    return claude_manager.probe_claude_route()


def probe_herdr_wsl() -> dict[str, Any]:
    """Проверяет живой статус Herdr и запущенных агентов внутри WSL."""
    creation_flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        distro = getattr(gemini_manager, "WSL_DISTRO", "Ubuntu")
        user = getattr(gemini_manager, "WSL_USER", "default")
        p = subprocess.run(
            ["wsl", "-d", distro, "-u", user, "bash", "-lic", "herdr pane list"],
            capture_output=True, text=True, timeout=3.0,
            encoding="utf-8", errors="replace",
            creationflags=creation_flags
        )
        if p.returncode == 0 and p.stdout.strip():
            data = json.loads(p.stdout)
            panes = data.get("result", {}).get("panes", [])
            agents_summary = []
            for pane in panes:
                agent = pane.get("agent") or "terminal"
                status = pane.get("agent_status") or "running"
                pane_id = pane.get("pane_id")
                agents_summary.append({
                    "pane_id": pane_id,
                    "agent": agent,
                    "status": status,
                    "cwd": pane.get("cwd", ""),
                })
            return {
                "server_running": True,
                "pane_count": len(panes),
                "agents": agents_summary,
                "raw_ok": True,
            }
        else:
            return {
                "server_running": False,
                "pane_count": 0,
                "agents": [],
                "error": p.stderr.strip() or "Herdr server не отвечает",
            }
    except Exception as e:
        return {
            "server_running": False,
            "pane_count": 0,
            "agents": [],
            "error": str(e),
        }


def check_all_routes() -> dict[str, Any]:
    """Параллельно опрашивает маршруты и статус Herdr в WSL."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_gemini = executor.submit(probe_gemini_routing)
        f_claude = executor.submit(probe_claude_routing)
        f_herdr = executor.submit(probe_herdr_wsl)

        gemini_res = f_gemini.result()
        claude_res = f_claude.result()
        herdr_res = f_herdr.result()

    return {
        "timestamp": time.strftime("%H:%M:%S"),
        "gemini": gemini_res,
        "claude": claude_res,
        "herdr_wsl": herdr_res,
    }
