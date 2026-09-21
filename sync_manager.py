"""Модуль мониторинга сетевых маршрутов Herdr (WSL2).

Отслеживает:
- Состояние сервера Herdr и активных агентов (agy, claude) внутри WSL2
- Статус туннеля SOCKS5 активного аккаунта Gemini (порт 1081, 1082, 1083...)
- Прямое подключение через оригинальный IP для моделей Anthropic Claude
"""

from __future__ import annotations

import concurrent.futures
import json
import socket
import subprocess
import time
from typing import Any

import requests
import gemini_manager
import proxy_manager
import settings_manager
import strategy_manager

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


def probe_gemini_routing() -> dict[str, Any]:
    """Проверяет доступность моделей Gemini через персональный SOCKS5 прокси активного профиля.
    
    Если соединение не работает и включен auto_proxy_failover,
    автоматически подбирает другой рабочий прокси (с приоритетом той же страны)
    и переключает туннель.
    """
    active_port = gemini_manager.get_active_proxy_port()
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
                timeout=3.5,
                headers={"User-Agent": "HerdrRouter/1.0"},
            )
            lat = int((time.time() - t0) * 1000)
            google_ok = True
        except Exception as exc:
            google_exc = exc

    if socks_open and google_ok:
        try:
            curr_proxies = proxy_manager.load_proxies()
            curr_p = next((p for p in curr_proxies if p.get("port") == active_port), None)
            email = gemini_manager.get_active_profile_email() or "default"
            strategy_manager.log_event(
                account_email=email,
                profile_name="active_account",
                port=active_port,
                ip=curr_p.get("ip", "-") if curr_p else "-",
                country=curr_p.get("country", "undefined") if curr_p else "undefined",
                event="route_check",
            )
        except Exception:
            pass

        return {
            "online": True,
            "port": active_port,
            "status_text": f"SOCKS5 активен ({SOCKS5_HOST}:{active_port})",
            "route": f"socks5h://{SOCKS5_HOST}:{active_port}",
            "latency_ms": lat,
            "details": f"Трафик Gemini туннелируется через персональный SOCKS5 :{active_port}",
        }

    # Если мы здесь, значит порт закрыт либо Google API недоступен
    auto_failover = settings_manager.get_setting("auto_proxy_failover", True)
    if auto_failover:
        # Определяем предпочтительную страну текущего сбойного прокси
        curr_proxies = proxy_manager.load_proxies()
        curr_p = next((p for p in curr_proxies if p.get("port") == active_port), None)
        pref_country = curr_p.get("country") if curr_p else None

        ok, msg, best = gemini_manager.handle_proxy_failover(active_port, pref_country)
        if ok and best:
            new_port = best.get("port")
            # Проверяем новый порт
            t_new = time.time()
            new_socks_open = check_port_open(SOCKS5_HOST, new_port, timeout=1.0)
            if new_socks_open:
                try:
                    p_url = f"socks5h://{SOCKS5_HOST}:{new_port}"
                    requests.get(
                        GEMINI_ENDPOINT,
                        proxies={"http": p_url, "https": p_url},
                        timeout=3.5,
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

                    return {
                        "online": True,
                        "port": new_port,
                        "status_text": f"SOCKS5 переключен ({SOCKS5_HOST}:{new_port})",
                        "route": f"socks5h://{SOCKS5_HOST}:{new_port}",
                        "latency_ms": new_lat,
                        "details": f"Сбой :{active_port}. {msg}",
                        "failover_occurred": True,
                        "failover_msg": msg,
                        "old_port": active_port,
                        "new_port": new_port,
                    }
                except Exception as e_new:
                    return {
                        "online": False,
                        "port": new_port,
                        "status_text": f"Сбой нового SOCKS5 :{new_port}",
                        "route": f"socks5h://{SOCKS5_HOST}:{new_port}",
                        "latency_ms": None,
                        "details": f"Переключено на :{new_port}, но соединение не удалось: {e_new}",
                        "failover_occurred": True,
                        "failover_msg": msg,
                        "old_port": active_port,
                        "new_port": new_port,
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
    """Проверяет прямое подключение к Anthropic Claude через оригинальный IP."""
    t0 = time.time()
    ip = get_public_ip(timeout=2.0)

    try:
        resp = requests.get(
            ANTHROPIC_ENDPOINT,
            timeout=3.0,
            headers={"User-Agent": "HerdrRouter/1.0"},
        )
        lat = int((time.time() - t0) * 1000)
        return {
            "online": True,
            "status_text": f"Оригинальный IP: {ip}",
            "route": "Прямое подключение (без прокси)",
            "ip": ip,
            "latency_ms": lat,
            "details": "Прямой доступ к API Anthropic через оригинальный IP",
        }
    except Exception as exc:
        return {
            "online": False,
            "status_text": "Нет связи с Anthropic",
            "route": "Прямое подключение",
            "ip": ip,
            "latency_ms": None,
            "details": f"Ошибка соединения: {exc}",
        }


def probe_herdr_wsl() -> dict[str, Any]:
    """Проверяет живой статус Herdr и запущенных агентов внутри WSL."""
    creation_flags = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(
            ["wsl", "-d", "Ubuntu", "-u", "f", "bash", "-lic", "herdr pane list"],
            capture_output=True, text=True, timeout=3.0,
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
