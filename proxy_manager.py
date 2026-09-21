"""Модуль управления и мониторинга SOCKS5-прокси для Herdr.

Поддерживает:
- Хранение списка прокси в proxies.json
- Автоматическое добавление портов с шагом +1 от 1081 (1081, 1082, 1083...)
- Проверку доступности SOCKS5-портов и сквозного трафика
- Определение внешнего IP-адреса через прокси
- Определение страны по IP (с выводом 'undefined' в случае неудачи)
- Пагинацию по 10 элементов
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


def load_proxies() -> list[dict[str, Any]]:
    """Загружает список прокси из proxies.json.
    
    Если файл отсутствует, создает начальный список с портом 1081.
    """
    if not PROXIES_FILE.exists():
        initial = [
            {
                "id": 1,
                "host": DEFAULT_HOST,
                "port": BASE_PORT,
                "label": "Xray SOCKS5 (Основной)",
                "status": "unknown",  # "online", "offline", "unknown"
                "ip": "-",
                "country": "undefined",
                "latency_ms": None,
                "last_checked": None,
            }
        ]
        save_proxies(initial)
        return initial

    try:
        with open(PROXIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return data
    except Exception:
        pass

    # Fallback
    fallback = [
        {
            "id": 1,
            "host": DEFAULT_HOST,
            "port": BASE_PORT,
            "label": "Xray SOCKS5 (Основной)",
            "status": "unknown",
            "ip": "-",
            "country": "undefined",
            "latency_ms": None,
            "last_checked": None,
        }
    ]
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
        existing_ports = [p.get("port", BASE_PORT) for p in proxies if isinstance(p.get("port"), int)]
        if existing_ports:
            port = max(existing_ports) + 1
        else:
            port = BASE_PORT

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


def check_port_accessible(host: str, port: int, timeout: float = 1.0) -> bool:
    """Быстрая проверка открытости порта."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


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
    """
    proxies = load_proxies()
    candidates = [p for p in proxies if p.get("port") != failed_port]
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

