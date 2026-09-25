"""Модуль управления пулом изолированных сетевых интерфейсов и сокетов инференса.

Научно-инженерная цель:
Предоставление прецизионной сетевой изоляции для распределённых сабагентов.
В сравнительных бенчмарках ансамблей ИИ каждый рабочий узел должен иметь
собственный независимый сокет (1081, 1082, 1083...) для исключения TCP-интерференции,
корректного замера Time To First Token (TTFT) и исследования регионального разброса задержек.

Поддерживает:
- Регистрацию и учёт пула сокетов в proxies.json
- Автоматическое выделение портов с шагом +1 от 1081 (1081, 1082, 1083...)
- Проверку доступности сокетов и непрерывную телеметрию сквозного трафика
- Определение и валидацию региональной принадлежности эндпоинта
- Пагинацию по 10 элементов в интерфейсе исследователя
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
PROXIES_FILE = BASE_DIR / "proxies.json"

DEFAULT_HOST = "127.0.0.1"
BASE_PORT = 1081
PAGE_SIZE = 10
VLESS2SOCKS_INSTANCES_FILE: Path | None = None


def find_vless2socks_instances_file() -> Path | None:
    """Ищет файл instances.json от проекта vless2socks."""
    if VLESS2SOCKS_INSTANCES_FILE is not None:
        return VLESS2SOCKS_INSTANCES_FILE if VLESS2SOCKS_INSTANCES_FILE.exists() else None
    candidates = [
        BASE_DIR.parent / "vless2socks" / "instances.json",
        Path("D:/My files/vless2socks/instances.json"),
        BASE_DIR / "instances.json",
    ]
    env_dir = os.environ.get("VLESS2SOCKS_DIR")
    if env_dir:
        candidates.insert(0, Path(env_dir) / "instances.json")
    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c
        except Exception:
            pass
    return None


def sync_from_vless2socks(instances_path: Path | None = None) -> list[dict[str, Any]]:
    """Синхронизирует прокси из instances.json проекта vless2socks в proxies.json."""
    path = instances_path or find_vless2socks_instances_file()
    if not path or not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            v_data = json.load(f)
        if not isinstance(v_data, list):
            return []
    except Exception:
        return []

    current_proxies = []
    if PROXIES_FILE.exists():
        try:
            with open(PROXIES_FILE, "r", encoding="utf-8") as f:
                current_proxies = json.load(f)
                if not isinstance(current_proxies, list):
                    current_proxies = []
        except Exception:
            current_proxies = []

    existing_by_port = {p.get("port"): p for p in current_proxies if isinstance(p.get("port"), int)}
    changed = False

    import urllib.parse

    for inst in v_data:
        listen = inst.get("listen", "127.0.0.1:1081")
        h, p_str = (listen.split(":") + ["1081"])[:2]
        try:
            port = int(p_str)
        except ValueError:
            continue
        host = h.strip() or DEFAULT_HOST

        raw_name = (inst.get("name") or "").strip()
        url = inst.get("url", "")
        if not raw_name and "#" in url:
            try:
                raw_name = urllib.parse.unquote(url.split("#", 1)[1]).strip()
            except Exception:
                pass

        label = raw_name or ("System Proxy" if port == 1015 else f"SOCKS5 :{port}")

        if port in existing_by_port:
            entry = existing_by_port[port]
            if "claude" not in entry:
                entry["claude"] = True if port == 1015 else False
                changed = True
            if raw_name and entry.get("label") != raw_name:
                entry["label"] = raw_name
                changed = True
        else:
            existing_ids = [p.get("id", 0) for p in current_proxies if isinstance(p.get("id"), int)]
            new_id = max(existing_ids, default=0) + 1
            new_entry = {
                "id": new_id,
                "host": host,
                "port": port,
                "label": label,
                "status": "unknown",
                "ip": "-",
                "country": "undefined",
                "latency_ms": None,
                "last_checked": None,
                "claude": True if port == 1015 else False,
            }
            current_proxies.append(new_entry)
            existing_by_port[port] = new_entry
            changed = True

    if changed or not PROXIES_FILE.exists():
        def sort_key(p):
            port = p.get("port", 99999)
            return (0 if port == 1015 else 1, port)
        current_proxies.sort(key=sort_key)
        for i, p in enumerate(current_proxies, 1):
            p["id"] = i
        save_proxies(current_proxies)

    return current_proxies


def load_proxies(refresh_status: bool = False) -> list[dict[str, Any]]:
    """Загружает список прокси из proxies.json с автосинхронизацией из vless2socks.
    
    Если файл отсутствует, создает начальный список с портами 1015 и 1081.
    Если refresh_status=True, выполняет быструю проверку локальных портов и обновляет статус.
    """
    data = []
    if PROXIES_FILE.exists():
        try:
            with open(PROXIES_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, list) and len(d) > 0:
                    data = d
        except Exception:
            data = []

    if not data and not PROXIES_FILE.exists():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and (Path(meipass) / "proxies.json").exists():
            try:
                with open(Path(meipass) / "proxies.json", "r", encoding="utf-8") as mf:
                    b_data = json.load(mf)
                if isinstance(b_data, list) and len(b_data) > 0:
                    data = b_data
            except Exception:
                pass

    try:
        v_file = find_vless2socks_instances_file()
        if v_file:
            synced = sync_from_vless2socks(v_file)
            if synced:
                if refresh_status and refresh_local_ports_status(synced):
                    save_proxies(synced)
                return synced
    except Exception:
        pass

    if data:
        for p in data:
            if "claude" not in p:
                p["claude"] = True if p.get("port") == 1015 else False
        if refresh_status and refresh_local_ports_status(data):
            save_proxies(data)
        return data

    fallback = [
        {
            "id": 1,
            "host": DEFAULT_HOST,
            "port": 1015,
            "label": "System Proxy",
            "status": "unknown",
            "ip": "-",
            "country": "undefined",
            "latency_ms": None,
            "last_checked": None,
            "claude": True,
        },
        {
            "id": 2,
            "host": DEFAULT_HOST,
            "port": BASE_PORT,
            "label": "Xray SOCKS5 (Основной)",
            "status": "unknown",
            "ip": "-",
            "country": "undefined",
            "latency_ms": None,
            "last_checked": None,
            "claude": False,
        }
    ]
    if refresh_status:
        refresh_local_ports_status(fallback)
    save_proxies(fallback)
    return fallback


def save_proxies(proxies: list[dict[str, Any]]) -> None:
    """Сохраняет список прокси в proxies.json."""
    temp_file = PROXIES_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(proxies, f, indent=2, ensure_ascii=False)
        temp_file.replace(PROXIES_FILE)
    except Exception:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


def get_next_available_port() -> int:
    """Вычисляет следующий свободный порт начиная с BASE_PORT (1081)."""
    proxies = load_proxies()
    valid_ports = [p.get("port") for p in proxies if isinstance(p.get("port"), int) and p.get("port") >= BASE_PORT]
    return max(valid_ports) + 1 if valid_ports else BASE_PORT


def add_proxy(
    host: str = DEFAULT_HOST,
    port: int | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Добавляет новый прокси.
    
    Если порт не указан, автоматически вычисляет следующий порт (от 1081 + 1 и далее).
    """
    proxies = load_proxies()

    if port is None:
        port = get_next_available_port()

    existing_ids = [p.get("id", 0) for p in proxies if isinstance(p.get("id"), int)]
    new_id = max(existing_ids, default=0) + 1

    if not label:
        label = f"SOCKS5 :{port}"

    new_proxy = {
        "id": new_id,
        "host": host.strip() or DEFAULT_HOST,
        "port": int(port),
        "label": label.strip(),
        "status": "unknown",
        "ip": "-",
        "country": "undefined",
        "latency_ms": None,
        "last_checked": None,
        "claude": True if int(port) == 1015 else False,
    }
    proxies.append(new_proxy)
    save_proxies(proxies)
    return new_proxy


def delete_proxy(proxy_id: int) -> bool:
    """Удаляет прокси по ID."""
    proxies = load_proxies()
    initial_len = len(proxies)
    proxies = [p for p in proxies if p.get("id") != proxy_id]
    if len(proxies) < initial_len:
        save_proxies(proxies)
        return True
    return False


def set_proxy_claude_flag(port: int, enabled: bool) -> bool:
    """Устанавливает или снимает флаг Claude для прокси по указанному порту."""
    proxies = load_proxies()
    found = False
    for p in proxies:
        if p.get("port") == port:
            p["claude"] = bool(enabled)
            found = True
            break
    if found:
        save_proxies(proxies)
        return True
    return False


def get_proxy_claude_flag(port: int) -> bool:
    """Возвращает статус флага Claude для прокси (для 1015 по умолчанию True, для остальных False)."""
    proxies = load_proxies()
    for p in proxies:
        if p.get("port") == port:
            return bool(p.get("claude", True if port == 1015 else False))
    return True if port == 1015 else False


def check_port_accessible(host: str, port: int, timeout: float = 1.0) -> bool:
    """Быстрая проверка открытости порта."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def check_socks5_handshake(host: str, port: int, timeout: float = 0.15) -> bool:
    """Быстрая проверка доступности и рукопожатия SOCKS5."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.sendall(b"\x05\x01\x00")
        resp = s.recv(2)
        s.close()
        return resp == b"\x05\x00"
    except Exception:
        return False


def refresh_local_ports_status(proxies: list[dict[str, Any]], timeout: float = 0.15) -> bool:
    """Быстро и в реальном времени проверяет статус локальных SOCKS5-портов (127.0.0.1/localhost) в пуле потоков.
    
    - Если локальный порт закрыт или не отвечает на SOCKS5 рукопожатие:
      помечает его 'offline', сбрасывает ip/country/latency, исключая ложный статус 'online'.
    - Если локальный SOCKS5-порт отвечает, но числился 'offline' или 'unknown':
      помечает 'online'.
    Возвращает True, если хотя бы один статус изменился.
    """
    if not proxies:
        return False

    local_items = []
    for idx, p in enumerate(proxies):
        host = p.get("host", DEFAULT_HOST)
        port = p.get("port")
        if host in ("127.0.0.1", "localhost", "::1") and isinstance(port, int):
            local_items.append((idx, host, port))

    if not local_items:
        return False

    changed = False

    def _check(item):
        idx, host, port = item
        return idx, check_socks5_handshake(host, port, timeout=timeout)

    max_workers = min(10, len(local_items))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_check, local_items))

    for idx, is_alive in results:
        p = proxies[idx]
        cur_status = p.get("status")
        if is_alive:
            if cur_status != "online":
                p["status"] = "online"
                changed = True
        else:
            if cur_status != "offline":
                p["status"] = "offline"
                p["ip"] = "-"
                p["country"] = "undefined"
                p["latency_ms"] = None
                changed = True

    return changed


def probe_single_proxy(proxy: dict[str, Any], timeout: float = 4.0) -> dict[str, Any]:
    """Проверяет работоспособность SOCKS5-прокси, получает IP и страну.
    
    Если страну не удалось определить — строго возвращает 'undefined'.
    """
    host = proxy.get("host", DEFAULT_HOST)
    port = proxy.get("port", BASE_PORT)

    # 1. Проверяем открыт ли порт
    if not check_port_accessible(host, port, timeout=1.0):
        res = dict(proxy)
        res["status"] = "offline"
        res["ip"] = "-"
        res["country"] = "undefined"
        res["latency_ms"] = None
        res["last_checked"] = time.strftime("%H:%M:%S")
        return res

    t0 = time.time()
    proxy_url = f"socks5h://{host}:{port}"
    proxies_dict = {"http": proxy_url, "https": proxy_url}

    # 2. Пытаемся получить IP и геолокацию через ip-api.com
    ip = "-"
    country = "undefined"
    online = False
    latency_ms = None

    try:
        # ip-api.com отдает JSON с IP, страной, городом
        resp = requests.get(
            "http://ip-api.com/json",
            proxies=proxies_dict,
            timeout=timeout,
            headers={"User-Agent": "HerdrProxyCheck/1.0"},
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                ip = data.get("query", "-")
                country = data.get("country") or "undefined"
                online = True
                latency_ms = int((time.time() - t0) * 1000)
    except Exception:
        pass

    # Резервная попытка через api.ipify.org если ip-api.com не ответил, но прокси жив
    if not online:
        try:
            r_ip = requests.get(
                "https://api.ipify.org?format=json",
                proxies=proxies_dict,
                timeout=timeout,
                headers={"User-Agent": "HerdrProxyCheck/1.0"},
            )
            if r_ip.status_code == 200:
                ip_data = r_ip.json()
                ip = ip_data.get("ip", "-")
                online = True
                latency_ms = int((time.time() - t0) * 1000)
                # Пытаемся определить страну по полученному IP
                if ip and ip != "-":
                    try:
                        geo_r = requests.get(f"http://ip-api.com/json/{ip}", timeout=2.0)
                        if geo_r.status_code == 200:
                            country = geo_r.json().get("country") or "undefined"
                    except Exception:
                        country = "undefined"
        except Exception:
            online = False

    res = dict(proxy)
    res["status"] = "online" if online else "offline"
    res["ip"] = ip if online else "-"
    res["country"] = country if (online and country) else "undefined"
    res["latency_ms"] = latency_ms
    res["last_checked"] = time.strftime("%H:%M:%S")
    return res


def check_all_proxies(proxies: list[dict[str, Any]], max_workers: int = 5) -> list[dict[str, Any]]:
    """Параллельно проверяет список прокси в пуле потоков."""
    if not proxies:
        return []

    results: list[dict[str, Any]] = [None] * len(proxies)  # type: ignore

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(probe_single_proxy, p): idx
            for idx, p in enumerate(proxies)
        }
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception:
                err_p = dict(proxies[idx])
                err_p["status"] = "offline"
                err_p["ip"] = "-"
                err_p["country"] = "undefined"
                err_p["last_checked"] = time.strftime("%H:%M:%S")
                results[idx] = err_p

    # Сохраняем обновленные данные в файл
    save_proxies(results)
    return results


def get_paginated_proxies(proxies: list[dict[str, Any]], page: int, page_size: int = PAGE_SIZE) -> tuple[list[dict[str, Any]], int]:
    """Возвращает срез списка прокси для указанной страницы и общее количество страниц.
    
    page: 1-indexed (1, 2, 3...)
    """
    total_items = len(proxies)
    if total_items == 0:
        return [], 1

    total_pages = (total_items + page_size - 1) // page_size
    page = max(1, min(page, total_pages))

    start = (page - 1) * page_size
    end = start + page_size
    return proxies[start:end], total_pages


def find_best_fallback_proxy(
    failed_port: int,
    preferred_country: str | None = None,
) -> dict[str, Any] | None:
    """Ищет рабочий резервный SOCKS5-прокси при сбое текущего.
    
    Приоритет отдается прокси из той же страны (если указана и определена).
    Прокси с флагом Claude строго исключаются из кандидатов для Gemini.
    """
    proxies = load_proxies()
    candidates = [
        p for p in proxies 
        if p.get("port") != failed_port 
        and not p.get("claude", False) 
        and not get_proxy_claude_flag(p.get("port"))
    ]
    if not candidates:
        return None

    # Если страна сбойного порта не передана, пробуем найти ее в proxies.json
    if not preferred_country or preferred_country.lower() in ("undefined", "-", "не определено"):
        curr = next((p for p in proxies if p.get("port") == failed_port), None)
        if curr and curr.get("country") and curr.get("country").lower() not in ("undefined", "-"):
            preferred_country = curr.get("country")

    pref_lower = preferred_country.lower() if preferred_country else ""

    def sort_key(p: dict[str, Any]) -> int:
        p_c = (p.get("country") or "").lower()
        if pref_lower and pref_lower not in ("undefined", "-", "") and p_c == pref_lower:
            return 0  # Высший приоритет: та же страна!
        if p_c and p_c not in ("undefined", "-"):
            return 1  # Известная другая страна
        return 2      # Неопределенная страна

    sorted_candidates = sorted(candidates, key=sort_key)

    for cand in sorted_candidates:
        host = cand.get("host", DEFAULT_HOST)
        port = cand.get("port", BASE_PORT)
        if not check_port_accessible(host, port, timeout=1.0):
            continue

        probe_res = probe_single_proxy(cand, timeout=3.0)
        if probe_res.get("status") == "online":
            # Дополнительно проверяем доступность серверов Google
            try:
                p_url = f"socks5h://{host}:{port}"
                r = requests.get(
                    "https://generativelanguage.googleapis.com",
                    proxies={"https": p_url, "http": p_url},
                    timeout=3.0,
                    headers={"User-Agent": "HerdrProxyFailover/1.0"}
                )
                if r.status_code in (200, 404, 403):
                    return probe_res
            except Exception:
                continue

    return None


def get_proxy_choices(exclude_claude: bool = True) -> list[dict[str, Any]]:
    """Возвращает форматированный список прокси для выпадающего меню выбора в UI.
    
    По умолчанию исключает прокси с установленным флагом Claude, чтобы они не использовались в Gemini.
    """
    proxies = load_proxies(refresh_status=True)
    valid_proxies = [
        p for p in proxies 
        if isinstance(p.get("port"), int)
        and (not exclude_claude or (not p.get("claude", False) and not get_proxy_claude_flag(p.get("port"))))
    ]
    valid_proxies.sort(key=lambda x: x["port"])

    choices = []
    for idx, p in enumerate(valid_proxies, start=1):
        port = p["port"]
        proxy_num = (port - BASE_PORT + 1) if port >= BASE_PORT else idx
        co = p.get("country", "undefined")
        co_str = f" • {co}" if co and co not in ("undefined", "-") else ""
        ip = p.get("ip", "-")
        ip_str = f" ({ip})" if ip and ip != "-" else ""
        st = p.get("status", "unknown")
        st_icon = "🟢 " if st == "online" else ("🔴 " if st == "offline" else "⚪ ")

        display = f"{st_icon}Proxy {proxy_num} (:{port}){co_str}{ip_str}"
        title = f"Proxy {proxy_num} (:{port})"

        choices.append({
            "port": port,
            "proxy_num": proxy_num,
            "title": title,
            "display": display,
            "ip": ip,
            "country": co,
            "status": st,
        })
    return choices


