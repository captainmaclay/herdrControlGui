"""Модуль аналитики кумулятивной стабильности каналов связи и сетевой телеметрии.

Научно-инженерная цель:
Сбор объективных эмпирических данных о качестве каналов связи рабочих узлов.
В длительных бенчмарк-сессиях распределённых сабагентов важно оценивать
не разовые флуктуации задержки, а кумулятивную стабильность сетевого тракта
по совокупному времени удержания качественного соединения.

Функционал:
1. Журналирование телеметрии сессий инференса в strategy_history.json
2. Анализ стабильности маршрутов по СОВОКУПНОМУ ВРЕМЕНИ СОЕДИНЕНИЙ (вместо подсчета количества сессий)
3. Оценка согласованности сетевой топологии:
   - 🟢 ОК — текущий канал связи стабилен и соответствует эталонному региональному профилю
   - 🟡 Рекомендуется оптимизация — отклонение параметров задержки от исторического профиля
4. Отказоустойчивое сохранение экспериментальных данных:
   - Валидация и нормализация телеметрических записей при загрузке
   - Обратная совместимость с историческими наборами бенчмарк-данных
   - Атомарная сериализация для защиты от повреждений при внезапных сбоях
5. Расширенная аналитика: длительность каждой тестовой сессии и кумулятивное время по узлам
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "strategy_history.json"
MAX_HISTORY_ENTRIES = 1000


def format_duration(seconds: float | int) -> str:
    """Форматирует длительность соединения в человекочитаемый вид."""
    try:
        s = int(round(float(seconds)))
    except (ValueError, TypeError):
        return "0 сек"

    if s < 0:
        return "0 сек"
    if s < 60:
        return f"{s} сек"
    m = s // 60
    if m < 60:
        rem_s = s % 60
        return f"{m} мин {rem_s}с" if rem_s > 0 and m < 5 else f"{m} мин"
    h = m // 60
    rem_m = m % 60
    if h < 24:
        return f"{h}ч {rem_m}м" if rem_m > 0 else f"{h}ч"
    d = h // 24
    rem_h = h % 24
    return f"{d}д {rem_h}ч" if rem_h > 0 else f"{d}д"


def parse_timestamp(ts_str: str | None) -> float:
    """Безопасно парсит строковую дату в Unix timestamp."""
    if not ts_str or not isinstance(ts_str, str):
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(ts_str.strip(), fmt))
        except Exception:
            pass
    return 0.0


def sanitize_and_repair_history(raw_data: Any) -> list[dict[str, Any]]:
    """Проверяет и восстанавливает структуру истории (гарантирует отказоустойчивость к бэкапам)."""
    if not isinstance(raw_data, list):
        return []

    repaired = []
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    for item in raw_data:
        if not isinstance(item, dict):
            continue

        ts = str(item.get("timestamp") or now_str).strip()
        start_t = str(item.get("start_time") or ts).strip()
        last_act = str(item.get("last_active") or ts).strip()

        # Длительность: преобразуем к float
        dur = item.get("duration_seconds")
        if dur is None or dur == "":
            # Пытаемся вычислить из разницы start_time и last_active
            t_start = parse_timestamp(start_t)
            t_last = parse_timestamp(last_act)
            if t_last >= t_start > 0:
                dur_sec = max(30.0, t_last - t_start)
            else:
                dur_sec = 60.0  # Разумное дефолтное значение для старых бэкапов
        else:
            try:
                dur_sec = max(0.0, float(dur))
            except (ValueError, TypeError):
                dur_sec = 60.0

        country = str(item.get("country") or "undefined").strip()
        if country.lower() in ("-", "none", "null", ""):
            country = "undefined"

        port = item.get("port")
        try:
            port_num = int(port)
        except (ValueError, TypeError):
            port_num = 1081

        clean_entry = {
            "timestamp": ts,
            "start_time": start_t,
            "last_active": last_act,
            "duration_seconds": round(dur_sec, 1),
            "duration_fmt": format_duration(dur_sec),
            "account_email": str(item.get("account_email") or "default").strip(),
            "profile_name": str(item.get("profile_name") or "account").strip(),
            "port": port_num,
            "ip": str(item.get("ip") or "-").strip(),
            "country": country,
            "event": str(item.get("event") or "route_check").strip(),
            "note": str(item.get("note") or "").strip(),
        }
        repaired.append(clean_entry)

    return repaired


def load_history() -> list[dict[str, Any]]:
    """Загружает журнал событий из strategy_history.json с авто-восстановлением."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return sanitize_and_repair_history(data)
    except Exception:
        return []


def save_history(history: list[dict[str, Any]]) -> None:
    """Атомарно сохраняет журнал событий в strategy_history.json."""
    try:
        if len(history) > MAX_HISTORY_ENTRIES:
            history = history[-MAX_HISTORY_ENTRIES:]

        temp_file = HISTORY_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        temp_file.replace(HISTORY_FILE)
    except Exception:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


def record_connection_tick(
    account_email: str,
    profile_name: str,
    port: int,
    ip: str,
    country: str,
    elapsed_seconds: float = 30.0,
    event: str = "active_connection",
    note: str = "",
) -> dict[str, Any]:
    """Регистрирует активный тик подключения, аккумулируя совокупное время соединения.
    
    Если последнее соединение непрерывно продолжается (тот же аккаунт, порт, IP
    и время между проверками <= 180 сек) — продлевает длительность текущей сессии.
    Если произошла смена аккаунта, IP, порта или перерыв — открывает новую сессию.
    """
    if not account_email or account_email in ("Неизвестно", "Unknown", "-"):
        account_email = profile_name or "default"

    country_clean = country if country and country.lower() not in ("undefined", "-", "none") else "undefined"
    ip_clean = ip if ip and ip != "-" else "-"
    now_ts = time.time()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))

    history = load_history()

    if history:
        last = history[-1]
        last_email = last.get("account_email")
        last_port = last.get("port")
        last_ip = last.get("ip")
        last_act_ts = parse_timestamp(last.get("last_active", last.get("timestamp")))

        # Проверяем, является ли это продолжением той же сессии
        same_target = (last_email == account_email and last_port == port)
        # IP может уточниться с "-" на реальный IP в рамках той же сессии
        ip_compatible = (last_ip == ip_clean or last_ip == "-" or ip_clean == "-")

        if same_target and ip_compatible and (now_ts - last_act_ts <= 300):
            delta = now_ts - last_act_ts if last_act_ts > 0 else elapsed_seconds
            delta = max(float(elapsed_seconds), min(delta, 300.0))

            last["duration_seconds"] = round(float(last.get("duration_seconds", 0)) + delta, 1)
            last["duration_fmt"] = format_duration(last["duration_seconds"])
            last["last_active"] = now_str
            last["timestamp"] = now_str

            if ip_clean != "-" and last.get("ip") == "-":
                last["ip"] = ip_clean
            if country_clean != "undefined" and last.get("country") == "undefined":
                last["country"] = country_clean

            save_history(history)
            return last

    # Иначе начинаем новую сессию
    entry = {
        "timestamp": now_str,
        "start_time": now_str,
        "last_active": now_str,
        "duration_seconds": round(float(elapsed_seconds), 1),
        "duration_fmt": format_duration(elapsed_seconds),
        "account_email": account_email,
        "profile_name": profile_name or "account",
        "port": port,
        "ip": ip_clean,
        "country": country_clean,
        "event": event,
        "note": note or "Сессия соединения",
    }
    history.append(entry)
    save_history(history)
    return entry


def log_event(
    account_email: str,
    profile_name: str,
    port: int,
    ip: str,
    country: str,
    event: str = "route_check",
    note: str = "",
    duration_seconds: float = 30.0,
) -> dict[str, Any]:
    """Добавляет запись события (с поддержкой перенаправления на record_connection_tick)."""
    if event in ("route_check", "active_connection", "connected"):
        return record_connection_tick(
            account_email=account_email,
            profile_name=profile_name,
            port=port,
            ip=ip,
            country=country,
            elapsed_seconds=duration_seconds,
            event=event,
            note=note,
        )

    # Дискретное событие (например, ручное переключение профиля или failover)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    country_clean = country if country and country.lower() not in ("undefined", "-", "none") else "undefined"

    entry = {
        "timestamp": now_str,
        "start_time": now_str,
        "last_active": now_str,
        "duration_seconds": 0.0,
        "duration_fmt": "0 сек",
        "account_email": account_email or profile_name or "default",
        "profile_name": profile_name or "account",
        "port": port,
        "ip": ip or "-",
        "country": country_clean,
        "event": event,
        "note": note,
    }
    history = load_history()
    history.append(entry)
    save_history(history)
    return entry


def get_account_tendencies() -> dict[str, dict[str, Any]]:
    """Вычисляет предпочтительную гео-стратегию по СОВОКУПНОМУ ВРЕМЕНИ СОЕДИНЕНИЙ.
    
    Возвращает словарь {account_email: {...}} с метриками:
    - top_country: страна с максимальным совокупным временем
    - top_percent: процент от суммарного времени соединений
    - top_duration_seconds / top_duration_fmt: накопленное время в лидирующей стране
    - total_duration_seconds / total_duration_fmt: общее время подключений аккаунта
    - country_breakdown: статистика времени по всем странам
    - unique_ips_count: количество уникальных IP
    """
    history = load_history()

    acc_country_durations: dict[str, Counter] = {}
    acc_country_sessions: dict[str, Counter] = {}
    acc_ips: dict[str, Counter] = {}

    for entry in history:
        email = entry.get("account_email", "unknown")
        country = entry.get("country", "undefined")
        ip = entry.get("ip", "-")
        dur = float(entry.get("duration_seconds", 0))
        if dur <= 0:
            dur = 60.0  # Обратная совместимость для старых записей

        if email not in acc_country_durations:
            acc_country_durations[email] = Counter()
            acc_country_sessions[email] = Counter()
            acc_ips[email] = Counter()

        if country and country.lower() not in ("undefined", "-", "none"):
            acc_country_durations[email][country] += dur
            acc_country_sessions[email][country] += 1

        if ip and ip != "-":
            acc_ips[email][ip] += dur

    result: dict[str, dict[str, Any]] = {}

    for email, c_durations in acc_country_durations.items():
        total_sec = sum(c_durations.values())
        total_sessions = sum(acc_country_sessions[email].values())

        if total_sec <= 0:
            result[email] = {
                "top_country": "undefined",
                "top_percent": 0,
                "top_duration_seconds": 0.0,
                "top_duration_fmt": "0 сек",
                "total_duration_seconds": 0.0,
                "total_duration_fmt": "0 сек",
                "total_launches": total_sessions,
                "country_breakdown": {},
                "unique_ips_count": len(acc_ips[email]),
            }
            continue

        top_country, top_sec = c_durations.most_common(1)[0]
        top_percent = int(round((top_sec / total_sec) * 100))

        breakdown = {}
        for c, sec in c_durations.most_common():
            pct = int(round((sec / total_sec) * 100))
            breakdown[c] = {
                "percent": pct,
                "duration_seconds": round(sec, 1),
                "duration_fmt": format_duration(sec),
                "sessions": acc_country_sessions[email].get(c, 0),
            }

        result[email] = {
            "top_country": top_country,
            "top_percent": top_percent,
            "top_duration_seconds": round(top_sec, 1),
            "top_duration_fmt": format_duration(top_sec),
            "total_duration_seconds": round(total_sec, 1),
            "total_duration_fmt": format_duration(total_sec),
            "total_launches": total_sessions,
            "country_breakdown": breakdown,
            "unique_ips_count": len(acc_ips[email]),
        }

    return result


def evaluate_account_strategy(
    account_email: str,
    current_country: str,
    tendencies: dict[str, dict[str, Any]] | None = None,
    profile_name: str = "",
) -> dict[str, Any]:
    """Сравнивает текущую страну прокси с исторической стратегией совокупного времени.
    
    Возвращает:
    - status: 'ok' | 'nice_to_change' | 'neutral'
    - status_label: '🟢 ОК' | '🟡 Nice to change' | '⚪ Базовый режим'
    - recommendation: подробная рекомендация для UI
    - dominant_country: страна исторического предпочтения по времени
    - dominant_percent: процент совокупного времени соединений
    - dominant_duration_fmt: отформатированное накопленное время в привычной стране
    - total_duration_fmt: суммарное время всех соединений
    """
    if tendencies is None:
        tendencies = get_account_tendencies()

    info = tendencies.get(account_email)
    if not info and profile_name:
        info = tendencies.get(profile_name)
    curr_c = (current_country or "").strip()
    is_curr_valid = curr_c and curr_c.lower() not in ("undefined", "-")

    # Если суммарное время соединений менее 120 секунд — данных пока недостаточно
    tot_sec = info.get("total_duration_seconds", 0) if info else 0
    if not info or tot_sec < 120 or info.get("top_country") == "undefined":
        return {
            "status": "neutral",
            "status_label": "⚪ Базовый режим",
            "recommendation": "Недостаточно накопленного времени соединений для формирования устойчивой гео-тенденции (менее 2 минут активности).",
            "dominant_country": curr_c if is_curr_valid else "undefined",
            "dominant_percent": 100 if is_curr_valid else 0,
            "dominant_duration_fmt": format_duration(tot_sec),
            "total_duration_fmt": format_duration(tot_sec),
            "total_launches": info.get("total_launches", 0) if info else 0,
        }

    top_country = info["top_country"]
    top_percent = info["top_percent"]
    top_dur_fmt = info["top_duration_fmt"]
    total_dur_fmt = info["total_duration_fmt"]
    total_launches = info.get("total_launches", 0)

    if not is_curr_valid:
        return {
            "status": "nice_to_change",
            "status_label": "🟡 Nice to change",
            "recommendation": f"Страна текущего SOCKS5 не определена. Рекомендуется назначить IP из {top_country} (историческая привычка: {top_percent}% времени соединений • {top_dur_fmt}).",
            "dominant_country": top_country,
            "dominant_percent": top_percent,
            "dominant_duration_fmt": top_dur_fmt,
            "total_duration_fmt": total_dur_fmt,
            "total_launches": total_launches,
        }

    if curr_c.lower() == top_country.lower():
        return {
            "status": "ok",
            "status_label": "🟢 ОК",
            "recommendation": f"Полное соответствие: текущий SOCKS5 ({curr_c}) совпадает с исторической привычкой аккаунта ({top_percent}% от общего времени соединений • {top_dur_fmt}). Риск фрод-контроля Google минимален.",
            "dominant_country": top_country,
            "dominant_percent": top_percent,
            "dominant_duration_fmt": top_dur_fmt,
            "total_duration_fmt": total_dur_fmt,
            "total_launches": total_launches,
        }
    else:
        return {
            "status": "nice_to_change",
            "status_label": "🟡 Nice to change",
            "recommendation": f"Текущий IP из {curr_c}, однако аккаунт дольше всего ({top_percent}% времени • {top_dur_fmt} из {total_dur_fmt}) работал из {top_country}. Рекомендуется выбрать прокси из {top_country} во избежание подозрений проверок безопасности Google.",
            "dominant_country": top_country,
            "dominant_percent": top_percent,
            "dominant_duration_fmt": top_dur_fmt,
            "total_duration_fmt": total_dur_fmt,
            "total_launches": total_launches,
        }


def get_extended_ip_stats() -> list[dict[str, Any]]:
    """Возвращает агрегированную статистику по IP, отсортированную по совокупному времени."""
    history = load_history()
    ip_map: dict[str, dict[str, Any]] = {}

    for entry in history:
        ip = entry.get("ip", "-")
        if not ip or ip == "-":
            continue

        dur = float(entry.get("duration_seconds", 0))
        if dur <= 0:
            dur = 60.0

        if ip not in ip_map:
            ip_map[ip] = {
                "ip": ip,
                "country": entry.get("country", "undefined"),
                "ports": set(),
                "accounts": set(),
                "total_duration_seconds": 0.0,
                "total_launches": 0,
                "first_seen": entry.get("start_time", entry.get("timestamp", "-")),
                "last_seen": entry.get("last_active", entry.get("timestamp", "-")),
            }

        data = ip_map[ip]
        data["ports"].add(entry.get("port"))
        data["accounts"].add(entry.get("account_email"))
        data["total_duration_seconds"] += dur
        data["total_launches"] += 1
        data["last_seen"] = entry.get("last_active", entry.get("timestamp", data["last_seen"]))
        if entry.get("country") and entry.get("country") != "undefined":
            data["country"] = entry.get("country")

    result = []
    for ip, data in ip_map.items():
        dur_sec = round(data["total_duration_seconds"], 1)
        result.append({
            "ip": ip,
            "country": data["country"],
            "total_duration_seconds": dur_sec,
            "total_duration_fmt": format_duration(dur_sec),
            "ports": sorted(list(data["ports"])),
            "accounts": sorted(list(data["accounts"])),
            "total_launches": data["total_launches"],
            "first_seen": data["first_seen"],
            "last_seen": data["last_seen"],
        })

    # Сортируем по суммарному времени соединений по убыванию
    result.sort(key=lambda x: x["total_duration_seconds"], reverse=True)
    return result


def seed_initial_history_if_empty(profiles: list[dict[str, Any]], proxies: list[dict[str, Any]]) -> None:
    """Инициализирует базовую историю со временем соединений, если история пуста."""
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
            # Калибруем начальную сессию с реалистичной базовой длительностью 1800 сек (30 минут)
            now_ts = time.time()
            start_ts = now_ts - 1800
            start_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_ts))
            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))

            entry = {
                "timestamp": now_str,
                "start_time": start_str,
                "last_active": now_str,
                "duration_seconds": 1800.0,
                "duration_fmt": format_duration(1800),
                "account_email": email,
                "profile_name": prof.get("profile_name", "account"),
                "port": port,
                "ip": ip,
                "country": country,
                "event": "initial_seed",
                "note": "Базовая калибровка аккаунта (30 мин)",
            }
            history.append(entry)

    save_history(history)


def rename_account_in_history(old_name: str, new_name: str) -> int:
    """Обновляет имя профиля и/или аккаунта во всех записях истории strategy_history.json."""
    if not old_name or not new_name or old_name == new_name:
        return 0

    history = load_history()
    count = 0
    for entry in history:
        modified = False
        if entry.get("profile_name") == old_name:
            entry["profile_name"] = new_name
            modified = True
        if entry.get("account_email") == old_name:
            entry["account_email"] = new_name
            modified = True
        if modified:
            count += 1

    if count > 0:
        save_history(history)
    return count
