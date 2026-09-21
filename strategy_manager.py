"""Модуль управления стратегией прокси и историей использования аккаунтов.

Функционал:
1. Хранение журнала событий (запуски, проверки маршрута, ротации) в strategy_history.json
2. Анализ тенденций использования прокси: выявление доминирующей страны для каждого аккаунта
3. Формирование рекомендаций:
   - 🟢 ОК — текущий SOCKS5 соответствует исторической тенденции аккаунта
   - 🟡 Nice to change — рекомендуется сменить прокси на страну доминирующей активности
4. Расширенный лог: история запусков и распределение использования уникальных IP по аккаунтам
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "strategy_history.json"
MAX_HISTORY_ENTRIES = 1000


def load_history() -> list[dict[str, Any]]:
    """Загружает журнал событий из strategy_history.json."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []


def save_history(history: list[dict[str, Any]]) -> None:
    """Сохраняет журнал событий в strategy_history.json."""
    try:
        # Ограничиваем максимальное количество записей
        if len(history) > MAX_HISTORY_ENTRIES:
            history = history[-MAX_HISTORY_ENTRIES:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def log_event(
    account_email: str,
    profile_name: str,
    port: int,
    ip: str,
    country: str,
    event: str = "route_check",
    note: str = "",
) -> dict[str, Any]:
    """Добавляет новую запись в журнал истории использования."""
    if not account_email or account_email in ("Неизвестно", "Unknown", "-"):
        account_email = profile_name or "default"

    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "account_email": account_email,
        "profile_name": profile_name or "account",
        "port": port,
        "ip": ip or "-",
        "country": country if country and country.lower() != "undefined" else "undefined",
        "event": event,
        "note": note,
    }

    history = load_history()

    # Избегаем дублирования одинаковых записей чаще чем раз в 60 секунд
    if history:
        last = history[-1]
        if (
            last.get("account_email") == entry["account_email"]
            and last.get("port") == entry["port"]
            and last.get("ip") == entry["ip"]
            and last.get("event") == entry["event"]
        ):
            try:
                # Если прошло менее 60 секунд, не плодим лишние однотипные логи
                t_last = time.mktime(time.strptime(last.get("timestamp", ""), "%Y-%m-%d %H:%M:%S"))
                t_curr = time.mktime(time.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S"))
                if t_curr - t_last < 60:
                    return last
            except Exception:
                pass

    history.append(entry)
    save_history(history)
    return entry


def get_account_tendencies() -> dict[str, dict[str, Any]]:
    """Агрегирует историю и вычисляет тенденции по странам для каждого аккаунта.
    
    Возвращает словарь {account_email: {...}}
    """
    history = load_history()
    acc_map: dict[str, list[str]] = {}
    acc_ips: dict[str, Counter] = {}

    for entry in history:
        email = entry.get("account_email", "unknown")
        country = entry.get("country", "undefined")
        ip = entry.get("ip", "-")

        if email not in acc_map:
            acc_map[email] = []
            acc_ips[email] = Counter()

        if country and country.lower() not in ("undefined", "-"):
            acc_map[email].append(country)
        if ip and ip != "-":
            acc_ips[email][ip] += 1

    result: dict[str, dict[str, Any]] = {}

    for email, countries in acc_map.items():
        total_country_launches = len(countries)
        if total_country_launches == 0:
            result[email] = {
                "top_country": "undefined",
                "top_percent": 0,
                "top_count": 0,
                "total_launches": len(acc_ips[email]),
                "country_breakdown": {},
                "unique_ips_count": len(acc_ips[email]),
            }
            continue

        c_counter = Counter(countries)
        top_country, top_count = c_counter.most_common(1)[0]
        top_percent = int(round((top_count / total_country_launches) * 100))

        breakdown = {c: int(round((cnt / total_country_launches) * 100)) for c, cnt in c_counter.most_common()}

        result[email] = {
            "top_country": top_country,
            "top_percent": top_percent,
            "top_count": top_count,
            "total_launches": total_country_launches,
            "country_breakdown": breakdown,
            "unique_ips_count": len(acc_ips[email]),
        }

    return result


def evaluate_account_strategy(
    account_email: str,
    current_country: str,
    tendencies: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Сравнивает текущую страну прокси с исторической тенденцией аккаунта.
    
    Возвращает:
    - status: 'ok' | 'nice_to_change' | 'neutral'
    - status_label: '🟢 ОК' | '🟡 Nice to change' | '⚪ Нет данных'
    - recommendation: подробная рекомендация для UI
    - dominant_country: страна исторической тенденции
    - dominant_percent: процент запусков
    """
    if tendencies is None:
        tendencies = get_account_tendencies()

    info = tendencies.get(account_email)
    curr_c = (current_country or "").strip()
    is_curr_valid = curr_c and curr_c.lower() not in ("undefined", "-")

    if not info or info.get("total_launches", 0) < 2 or info.get("top_country") == "undefined":
        return {
            "status": "neutral",
            "status_label": "⚪ Базовый режим",
            "recommendation": "Недостаточно данных запусков для выявления устойчивой гео-тенденции.",
            "dominant_country": curr_c if is_curr_valid else "undefined",
            "dominant_percent": 100 if is_curr_valid else 0,
            "total_launches": info.get("total_launches", 0) if info else 0,
        }

    top_country = info["top_country"]
    top_percent = info["top_percent"]
    total = info["total_launches"]

    if not is_curr_valid:
        return {
            "status": "nice_to_change",
            "status_label": "🟡 Nice to change",
            "recommendation": f"Страна текущего SOCKS5 не определена. Рекомендуется назначить IP из {top_country} (историческая тенденция: {top_percent}% сессий).",
            "dominant_country": top_country,
            "dominant_percent": top_percent,
            "total_launches": total,
        }

    if curr_c.lower() == top_country.lower():
        return {
            "status": "ok",
            "status_label": "🟢 ОК",
            "recommendation": f"Полное соответствие: текущий SOCKS5 ({curr_c}) совпадает с исторической привычкой аккаунта ({top_percent}% активности). Риск фрод-контроля Google минимален.",
            "dominant_country": top_country,
            "dominant_percent": top_percent,
            "total_launches": total,
        }
    else:
        return {
            "status": "nice_to_change",
            "status_label": "🟡 Nice to change",
            "recommendation": f"Текущий IP из {curr_c}, однако аккаунт чаще всего ({top_percent}% из {total} сессий) запускался из {top_country}. Рекомендуется выбрать прокси из {top_country} во избежание подозрительных проверок безопасности Google.",
            "dominant_country": top_country,
            "dominant_percent": top_percent,
            "total_launches": total,
        }


def get_extended_ip_stats() -> list[dict[str, Any]]:
    """Возвращает агрегированную статистику использования уникальных IP всеми аккаунтами."""
    history = load_history()
    # Группировка по IP
    ip_map: dict[str, dict[str, Any]] = {}

    for entry in history:
        ip = entry.get("ip", "-")
        if not ip or ip == "-":
            continue

        if ip not in ip_map:
            ip_map[ip] = {
                "ip": ip,
                "country": entry.get("country", "undefined"),
                "ports": set(),
                "accounts": set(),
                "total_launches": 0,
                "first_seen": entry.get("timestamp", "-"),
                "last_seen": entry.get("timestamp", "-"),
            }

        data = ip_map[ip]
        data["ports"].add(entry.get("port"))
        data["accounts"].add(entry.get("account_email"))
        data["total_launches"] += 1
        data["last_seen"] = entry.get("timestamp", data["last_seen"])
        if entry.get("country") and entry.get("country") != "undefined":
            data["country"] = entry.get("country")

    result = []
    for ip, data in ip_map.items():
        result.append({
            "ip": ip,
            "country": data["country"],
            "ports": sorted(list(data["ports"])),
            "accounts": sorted(list(data["accounts"])),
            "total_launches": data["total_launches"],
            "first_seen": data["first_seen"],
            "last_seen": data["last_seen"],
        })

    # Сортируем по количеству запусков по убыванию
    result.sort(key=lambda x: x["total_launches"], reverse=True)
    return result


def seed_initial_history_if_empty(profiles: list[dict[str, Any]], proxies: list[dict[str, Any]]) -> None:
    """Если история пуста, создает начальные записи на основе текущих профилей."""
    history = load_history()
    if history:
        return

    proxies_map = {p.get("port"): p for p in proxies}

    for prof in profiles:
        email = prof.get("email") or prof.get("profile_name")
        port = prof.get("port", 1081)
        p_data = proxies_map.get(port, {})
        ip = p_data.get("ip", "-")
        country = p_data.get("country", "undefined")

        if ip and ip != "-":
            # Инициализируем 3 начальные отметки для наглядности статистики
            for _ in range(3):
                log_event(
                    account_email=email,
                    profile_name=prof.get("profile_name", "account"),
                    port=port,
                    ip=ip,
                    country=country,
                    event="initial_seed",
                    note="Начальная калибровка профиля",
                )
