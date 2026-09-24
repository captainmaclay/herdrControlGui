"""Модуль управления OAuth2 профилями Google Gemini (Antigravity CLI).

Обеспечивает:
- Отображение сохраненных Google аккаунтов из WSL2 (~/.gemini/profiles)
- Жесткую привязку: 1 Аккаунт Gemini = 1 отдельный SOCKS5 порт (1081, 1082, 1083...)
- Определение активного в данный момент аккаунта (~/.gemini/antigravity-cli/antigravity-oauth-token)
- Синхронизацию активного порта прокси (~/.gemini/antigravity-cli/active_proxy.env)
- Быструю смену активного аккаунта и его персонального порта в один клик
- Онлайн-валидацию токена в Google API через персональный SOCKS5 порт профиля
- Авто-добавление привязанных портов в список proxies.json
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import proxy_manager
import strategy_manager
import settings_manager

def get_wsl_user(distro: str = "Ubuntu") -> str:
    """Определяет активного пользователя WSL2 без блокирующих вызовов сети."""
    return (os.environ.get("WSL_USER") or os.environ.get("USERNAME") or "default").strip()


WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")
WSL_USER = get_wsl_user(WSL_DISTRO)

# Пути к файлам через WSL UNC-путь в Windows
WSL_BASE_DIR = Path(rf"\\wsl$\{WSL_DISTRO}\home\{WSL_USER}\.gemini")
CLI_DIR = WSL_BASE_DIR / "antigravity-cli"
PROFILES_DIR = WSL_BASE_DIR / "profiles"
ACTIVE_TOKEN_FILE = CLI_DIR / "antigravity-oauth-token"
ACTIVE_PROXY_ENV_FILE = CLI_DIR / "active_proxy.env"
ACTIVE_PROFILE_JSON_FILE = CLI_DIR / "active_profile.json"

BASE_SOCKS5_PORT = 1081


def parse_jwt_claims(id_token: str | None) -> dict[str, Any]:
    """Декодирует полезную нагрузку (payload) из JWT id_token без обращений к сети."""
    if not id_token:
        return {}
    try:
        parts = id_token.split(".")
        if len(parts) >= 2:
            pad = parts[1] + "=" * (4 - len(parts[1]) % 4)
            data = base64.urlsafe_b64decode(pad)
            return json.loads(data)
    except Exception:
        pass
    return {}


def format_expiry(exp_timestamp: int | float | None, is_active: bool = False) -> tuple[str, bool]:
    """Форматирует срок действия access-токена: дата и время окончания + оставшееся время."""
    if not exp_timestamp:
        return "Не определено", False

    diff = exp_timestamp - time.time()
    abs_time = time.strftime("%H:%M %d.%m.%Y", time.localtime(exp_timestamp))
    if diff <= 0:
        if is_active:
            return f"Access истёк {abs_time} (agy обновит автоматически)", False
        return f"Access истёк {abs_time} (обновится при активации)", False

    hours = int(diff // 3600)
    minutes = int((diff % 3600) // 60)
    left = f"{hours}ч {minutes}м" if hours > 0 else f"{minutes} мин"
    return f"Access до {abs_time} ({left})", False


def parse_token_expiry(value: Any) -> float | None:
    """Преобразует поле token.expiry (RFC3339 с наносекундами) в Unix timestamp."""
    if not isinstance(value, str) or not value:
        return None
    try:
        s = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


import token_meta_cache

def get_token_info(token_file: Path, is_active: bool = False) -> dict[str, Any]:
    """Считывает метаданные из файла antigravity-oauth-token, либо из кеша если токен заблокирован."""
    prof_name = "__active__" if token_file.parent.name == "antigravity-cli" else token_file.parent.name
    
    if not token_file.exists():
        if Path(str(token_file) + ".enc").exists():
            meta = token_meta_cache.get_meta("gemini", prof_name)
            if meta:
                exp = meta.get("exp")
                expiry_text, is_expired = format_expiry(exp, is_active=is_active)
                if meta.get("refresh_text"):
                    expiry_text = f"{expiry_text} • Refresh: {meta['refresh_text']}"
                meta["expiry_text"] = expiry_text
                meta["is_expired"] = is_expired
                meta["exists"] = True
                meta["is_locked"] = True
                return meta
        return {
            "exists": False,
            "is_locked": False,
            "email": "Нет файла",
            "name": "-",
            "is_expired": True,
            "expiry_text": "Файл отсутствует",
        }

    try:
        with open(token_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        id_token = data.get("id_token")
        claims = parse_jwt_claims(id_token)

        email = claims.get("email") or "Без email"
        name = claims.get("name") or claims.get("given_name") or "Пользователь Google"
        token = data.get("token") if isinstance(data.get("token"), dict) else {}
        exp = parse_token_expiry(token.get("expiry")) or claims.get("exp")
        refresh_text = "бессрочный" if token.get("refresh_token") else "отсутствует"
        
        info = {
            "exists": True,
            "is_locked": False,
            "email": email,
            "name": name,
            "exp": exp,
            "refresh_text": refresh_text,
            "auth_method": data.get("auth_method", "oauth")
        }
        token_meta_cache.update_meta("gemini", prof_name, info)

        expiry_text, is_expired = format_expiry(exp, is_active=is_active)
        info["expiry_text"] = f"{expiry_text} • Refresh: {refresh_text}"
        info["is_expired"] = is_expired
        info["id_token"] = id_token
        info["file_path"] = str(token_file)
        return info
    except Exception as e:
        return {
            "exists": True,
            "email": "Ошибка чтения",
            "name": str(e),
            "is_expired": True,
            "expiry_text": "Ошибка JSON",
        }


def is_port_available_for_gemini(port: int) -> bool:
    """Проверяет, доступен ли порт для использования в Gemini (не занят флагом Claude)."""
    try:
        return not proxy_manager.get_proxy_claude_flag(int(port))
    except Exception:
        return True


def get_profile_binding_info(profile_name: str) -> tuple[int, bool]:
    """Возвращает (port, is_manual) для указанного профиля с приоритетом ручного выбора."""
    # 1. Проверяем настройки Windows (наивысший приоритет ручного выбора)
    binding = settings_manager.get_account_proxy_binding(profile_name)
    if binding and isinstance(binding, dict) and binding.get("manual") is True:
        port = binding.get("port")
        if isinstance(port, int) and port >= BASE_SOCKS5_PORT:
            if is_port_available_for_gemini(port):
                return port, True
            else:
                settings_manager.clear_account_proxy_binding(profile_name)

    # 2. Проверяем WSL profile_config.json
    cfg_file = PROFILES_DIR / profile_name / "profile_config.json"
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                if d.get("manual") is True and isinstance(d.get("port"), int) and d["port"] >= BASE_SOCKS5_PORT:
                    if is_port_available_for_gemini(d["port"]):
                        settings_manager.set_account_proxy_binding(profile_name, d["port"], manual=True)
                        return d["port"], True
        except Exception:
            pass

    # 3. Последовательный расчет по умолчанию (account-1 -> 1081, account-2 -> 1082...) с исключением прокси Claude
    port = _calculate_default_sequential_port(profile_name)
    return port, False


def _order_file() -> Path:
    return PROFILES_DIR.parent / "profile_order.json"


def _existing_profile_names() -> list[str]:
    try:
        return sorted(p.name for p in PROFILES_DIR.iterdir() if p.is_dir()) if PROFILES_DIR.exists() else []
    except Exception:
        return []


def get_profile_order() -> list[str]:
    """Возвращает порядок профилей (#1, #2, ...): сохранённый порядок + новые профили в конце."""
    existing = _existing_profile_names()
    saved: list[str] = []
    try:
        with open(_order_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("order"), list):
            saved = [n for n in data["order"] if isinstance(n, str)]
    except Exception:
        pass
    order = [n for n in dict.fromkeys(saved) if n in existing]
    order += [n for n in existing if n not in order]
    return order


def save_profile_order(order: list[str]) -> None:
    data = {"order": list(dict.fromkeys(order))}
    try:
        with open(_order_file(), "r", encoding="utf-8") as f:
            if json.load(f) == data:
                return
    except Exception:
        pass
    try:
        _order_file().parent.mkdir(parents=True, exist_ok=True)
        with open(_order_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def set_profile_position(profile_name: str, position: int) -> tuple[bool, str]:
    """Перемещает профиль на позицию #position (1..N); порты всех профилей пересчитываются по номерам."""
    order = get_profile_order()
    if profile_name not in order:
        return False, f"Профиль '{profile_name}' не найден"
    try:
        position = int(position)
    except (TypeError, ValueError):
        return False, f"Некорректный номер: {position}"
    position = max(1, min(position, len(order)))
    order.remove(profile_name)
    order.insert(position - 1, profile_name)
    save_profile_order(order)
    list_profiles()  # применяет новые порты и обновляет активный туннель
    return True, f"Профиль {profile_name} перемещён на #{position} (SOCKS5 :{get_port_assignments()[profile_name]})"


def _get_manual_port(profile_name: str) -> int | None:
    """Порт, вручную выбранный пользователем для профиля (settings.json или profile_config.json)."""
    binding = settings_manager.get_account_proxy_binding(profile_name)
    if binding and isinstance(binding, dict) and binding.get("manual") is True and isinstance(binding.get("port"), int):
        return binding["port"]

    cfg_file = PROFILES_DIR / profile_name / "profile_config.json"
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("manual") is True and isinstance(d.get("port"), int):
                return d["port"]
        except Exception:
            pass
    return None


def get_port_assignments() -> dict[str, int]:
    """Строит раскладку портов без повторов по номерам профилей в списке.

    Ручные привязки закрепляются первыми; остальные профили по порядку (#1, #2, ...)
    получают порты BASE_SOCKS5_PORT, +1, +2 ... Порты с флагом Claude всегда пропускаются.
    """
    order = get_profile_order()
    assignments: dict[str, int] = {}
    used: set[int] = set()

    for name in order:
        port = _get_manual_port(name)
        if port is not None and port >= BASE_SOCKS5_PORT and port not in used and is_port_available_for_gemini(port):
            assignments[name] = port
            used.add(port)

    for name in order:
        if name in assignments:
            continue
        cand = _next_free_port(used)
        assignments[name] = cand
        used.add(cand)

    return assignments


def _next_free_port(used: set[int]) -> int:
    cand = BASE_SOCKS5_PORT
    while cand in used or not is_port_available_for_gemini(cand):
        cand += 1
    return cand


def get_all_assigned_ports(exclude_profile: str | None = None) -> set[int]:
    """Возвращает множество портов, занятых существующими Gemini-профилями."""
    return {port for name, port in get_port_assignments().items() if name != exclude_profile}


def _calculate_default_sequential_port(profile_name: str) -> int:
    """Возвращает порт профиля по общей раскладке; для нового профиля — следующий свободный порт.

    Пример: 3 профиля на 1081-1083 -> новый 4-й профиль получает 1084 (независимо от имени).
    """
    assignments = get_port_assignments()
    if profile_name in assignments:
        return assignments[profile_name]
    return _next_free_port(set(assignments.values()))


def get_profile_port(profile_name: str) -> int:
    """Определяет персональный SOCKS5-порт для профиля с учетом приоритета ручного выбора."""
    port, is_man = get_profile_binding_info(profile_name)
    save_profile_port(profile_name, port, manual=is_man)
    return port


def reserve_new_profile_port(profile_name: str, port: int) -> None:
    """Создаёт папку нового профиля и записывает в неё назначенный порт до входа через gemini-oauth."""
    order = get_profile_order()
    try:
        (PROFILES_DIR / profile_name).mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    if profile_name not in order:
        save_profile_order(order + [profile_name])
    save_profile_port(profile_name, port, manual=False)


def is_profile_port_manual(profile_name: str) -> bool:
    """Проверяет, был ли порт профиля назначен вручную пользователем."""
    binding = settings_manager.get_account_proxy_binding(profile_name)
    if binding and isinstance(binding, dict):
        return bool(binding.get("manual", False))
    cfg_file = PROFILES_DIR / profile_name / "profile_config.json"
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                return bool(d.get("manual", False))
        except Exception:
            pass
    return False


def save_profile_port(profile_name: str, port: int, manual: bool = False) -> None:
    """Сохраняет персональный порт и признак ручного выбора в конфиг профиля."""
    prof_dir = PROFILES_DIR / profile_name
    if not prof_dir.exists():
        return
    cfg_file = prof_dir / "profile_config.json"
    try:
        data = {
            "profile_name": profile_name,
            "port": int(port),
            "manual": bool(manual),
        }
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def set_profile_manual_port(profile_name: str, port: int) -> tuple[bool, str]:
    """Устанавливает ручной выбор порта для профиля (наивысший приоритет, не сбрасывается при перезапуске)."""
    try:
        p_int = int(port)
        if p_int < 1000 or p_int > 65535:
            return False, f"Некорректный номер порта: {port}"
    except (ValueError, TypeError):
        return False, f"Некорректный номер порта: {port}"

    if not is_port_available_for_gemini(p_int):
        return False, f"Порт {p_int} отмечен флагом Claude и не может использоваться в аккаунтах Gemini."

    # Сохраняем в settings.json Windows
    settings_manager.set_account_proxy_binding(profile_name, p_int, manual=True)
    # Сохраняем в WSL profile_config.json
    save_profile_port(profile_name, p_int, manual=True)

    # Проверяем, является ли профиль активным
    active_email = get_active_profile_email()
    p_info = get_token_info(PROFILES_DIR / profile_name / "antigravity-oauth-token")
    p_email = p_info.get("email", "")

    # Если профиль активен или это единственный профиль, переключаем активный туннель
    if (active_email and active_email == p_email) or profile_name == "account-1":
        write_active_proxy_env(p_int, p_email)

    # Синхронизируем метки прокси
    sync_profiles_with_proxies(list_profiles())
    return True, f"Профиль {profile_name} вручную привязан к SOCKS5 :{p_int}"


def reset_all_profiles_to_sequential() -> tuple[bool, str]:
    """Сбрасывает все ручные привязки и расставляет прокси строго по порядку с исключением прокси Claude."""
    settings_manager.clear_all_account_proxy_bindings()

    profile_dirs = [PROFILES_DIR / n for n in get_profile_order()]

    reassigned = []
    cand_port = BASE_SOCKS5_PORT
    if profile_dirs:
        for idx, p in enumerate(profile_dirs):
            while not is_port_available_for_gemini(cand_port):
                cand_port += 1
            seq_port = cand_port
            cand_port += 1
            save_profile_port(p.name, seq_port, manual=False)
            settings_manager.set_account_proxy_binding(p.name, seq_port, manual=False)
            reassigned.append(f"{p.name} ➔ SOCKS5 :{seq_port}")
    else:
        while not is_port_available_for_gemini(cand_port):
            cand_port += 1
        save_profile_port("account-1", cand_port, manual=False)
        settings_manager.set_account_proxy_binding("account-1", cand_port, manual=False)
        reassigned.append(f"account-1 ➔ SOCKS5 :{cand_port}")

    # Обновляем активный прокси-туннель
    profiles = list_profiles()
    active_p = next((p for p in profiles if p.get("is_active")), None)
    if active_p:
        write_active_proxy_env(active_p.get("port", BASE_SOCKS5_PORT), active_p.get("email", ""))
    elif profiles:
        write_active_proxy_env(profiles[0].get("port", BASE_SOCKS5_PORT), profiles[0].get("email", ""))
    else:
        cand_p = BASE_SOCKS5_PORT
        while not is_port_available_for_gemini(cand_p):
            cand_p += 1
        write_active_proxy_env(cand_p)

    sync_profiles_with_proxies(profiles)
    msg = "Прокси успешно расставлены по порядку:\n" + "\n".join(reassigned)
    return True, msg


def reassign_profiles_using_claude_proxies() -> list[str]:
    """Проверяет все профили Gemini и переназначает порты, если они используют прокси с флагом Claude."""
    changed_profiles = []
    if not PROFILES_DIR.exists():
        return changed_profiles

    try:
        profile_dirs = sorted([p for p in PROFILES_DIR.iterdir() if p.is_dir()])
    except Exception:
        return changed_profiles

    for p in profile_dirs:
        prof_name = p.name
        raw_port = None
        cfg_file = p / "profile_config.json"
        if cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    raw_port = json.load(f).get("port")
            except Exception:
                pass
        if raw_port is None:
            b = settings_manager.get_account_proxy_binding(prof_name)
            if b and isinstance(b, dict):
                raw_port = b.get("port")

        curr_port = raw_port if raw_port is not None else get_profile_port(prof_name)
        if not is_port_available_for_gemini(curr_port):
            settings_manager.clear_account_proxy_binding(prof_name)
            new_port = _calculate_default_sequential_port(prof_name)
            save_profile_port(prof_name, new_port, manual=False)
            changed_profiles.append(f"{prof_name}: :{curr_port} ➔ :{new_port}")

    if changed_profiles:
        profiles = list_profiles(skip_reassign=True)
        active_p = next((p for p in profiles if p.get("is_active")), None)
        if active_p:
            write_active_proxy_env(active_p.get("port", BASE_SOCKS5_PORT), active_p.get("email", ""), profile_name=active_p.get("profile_name", ""))
        sync_profiles_with_proxies(profiles)

    return changed_profiles


def write_active_proxy_env(port: int, email: str = "", profile_name: str = "") -> None:
    """Записывает активный порт в active_proxy.env и active_profile.json в WSL."""
    CLI_DIR.mkdir(parents=True, exist_ok=True)
    try:
        http_port = 10000 + int(port)
        env_content = (
            f'# Auto-generated by Herdr Control Center\n'
            f'export ALL_PROXY="socks5h://127.0.0.1:{port}"\n'
            f'export HTTPS_PROXY="http://127.0.0.1:{http_port}"\n'
            f'export HTTP_PROXY="http://127.0.0.1:{http_port}"\n'
            f'export GEMINI_ACTIVE_PORT="{port}"\n'
            f'export GEMINI_ACTIVE_ACCOUNT="{email}"\n'
        )
        with open(ACTIVE_PROXY_ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write(env_content)

        meta: dict[str, Any] = {
            "port": port,
            "email": email,
            "proxy_url": f"socks5h://127.0.0.1:{port}",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if profile_name:
            meta["profile_name"] = profile_name
        elif ACTIVE_PROFILE_JSON_FILE.exists():
            try:
                with open(ACTIVE_PROFILE_JSON_FILE, "r", encoding="utf-8") as f:
                    old_meta = json.load(f)
                    if "profile_name" in old_meta:
                        meta["profile_name"] = old_meta["profile_name"]
            except Exception:
                pass

        with open(ACTIVE_PROFILE_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception:
        pass


def get_active_proxy_port() -> int:
    """Возвращает текущий активный SOCKS5-порт для Gemini."""
    if ACTIVE_PROFILE_JSON_FILE.exists():
        try:
            with open(ACTIVE_PROFILE_JSON_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d.get("port"), int):
                    return d["port"]
        except Exception:
            pass
    return BASE_SOCKS5_PORT


def get_active_profile_email() -> str:
    """Возвращает email текущего активного Google аккаунта."""
    if ACTIVE_PROFILE_JSON_FILE.exists():
        try:
            with open(ACTIVE_PROFILE_JSON_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d.get("email", "")
        except Exception:
            pass
    return ""


def get_active_profile_name() -> str:
    """Возвращает имя текущего активного профиля (например, 'account-1')."""
    if ACTIVE_PROFILE_JSON_FILE.exists():
        try:
            with open(ACTIVE_PROFILE_JSON_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                pn = d.get("profile_name")
                if pn and (PROFILES_DIR / pn).exists():
                    return pn
        except Exception:
            pass

    active_email = get_active_profile_email()
    profiles = list_profiles()
    for p in profiles:
        if p.get("is_active"):
            return p["profile_name"]
        if active_email and p.get("email") == active_email:
            return p["profile_name"]
    if profiles:
        return profiles[0]["profile_name"]
    return "account-1"


def get_active_bound_port() -> int:
    """Возвращает забинденный (целевой) персональный порт активного профиля."""
    prof_name = get_active_profile_name()
    return get_profile_port(prof_name)


def sync_profiles_with_proxies(profiles: list[dict[str, Any]]) -> None:
    """Синхронизирует список прокси в proxies.json с профилями Gemini."""
    proxies = proxy_manager.load_proxies()
    existing_ports = {p.get("port"): p for p in proxies if isinstance(p.get("port"), int)}
    changed = False

    for prof in profiles:
        port = prof.get("port", BASE_SOCKS5_PORT)
        if not is_port_available_for_gemini(port):
            continue

        email = prof.get("email", "")
        p_name = prof.get("profile_name", "")
        label = f"Gemini: {p_name}"

        if port not in existing_ports:
            max_id = max([p.get("id", 0) for p in proxies if isinstance(p.get("id"), int)], default=0)
            proxies.append({
                "id": max_id + 1,
                "host": "127.0.0.1",
                "port": port,
                "label": label,
                "status": "unknown",
                "ip": "-",
                "country": "undefined",
                "latency_ms": None,
                "last_checked": None,
                "claude": False,
            })
            changed = True
        else:
            # Обновляем метку при необходимости
            curr = existing_ports[port]
            if curr.get("label") != label and "Gemini:" in label:
                curr["label"] = label
                changed = True

    # Очищаем устаревшие метки Gemini с портов, которые больше не привязаны или заняты Claude
    active_profile_ports = {
        prof.get("port") for prof in profiles 
        if isinstance(prof.get("port"), int) and is_port_available_for_gemini(prof.get("port"))
    }
    for p in proxies:
        p_port = p.get("port")
        if (p_port not in active_profile_ports or not is_port_available_for_gemini(p_port)) and str(p.get("label", "")).startswith("Gemini:"):
            p["label"] = f"SOCKS5 :{p_port}"
            changed = True

    if changed:
        proxy_manager.save_proxies(proxies)


def _auto_heal_profiles() -> None:
    """Гарантирует, что существующий активный токен сохранен в профиль, и восстанавливает профили при необходимости."""
    try:
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    if not ACTIVE_TOKEN_FILE.exists():
        return

    active_info = get_token_info(ACTIVE_TOKEN_FILE)
    if not active_info.get("exists"):
        return

    active_email = (active_info.get("email") or "").strip()
    if not active_email or active_email in ("Без email", "Нет файла", "Ошибка чтения", "Неизвестно"):
        return

    try:
        profile_dirs = sorted([p for p in PROFILES_DIR.iterdir() if p.is_dir()])
    except Exception:
        profile_dirs = []

    # Читаем active_profile.json если есть
    active_profile_name = ""
    if ACTIVE_PROFILE_JSON_FILE.exists():
        try:
            with open(ACTIVE_PROFILE_JSON_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                active_profile_name = d.get("profile_name", "")
        except Exception:
            pass

    # 1. Проверяем, есть ли профиль, чей токен содержит active_email
    matched_profile_dir = None
    for p in profile_dirs:
        t_path = p / "antigravity-oauth-token"
        if t_path.exists():
            t_info = get_token_info(t_path)
            if t_info.get("email") == active_email:
                matched_profile_dir = p
                # Если активный файл новее, синхронизируем изменения (refreshed token)
                try:
                    if ACTIVE_TOKEN_FILE.stat().st_mtime > t_path.stat().st_mtime:
                        shutil.copy2(ACTIVE_TOKEN_FILE, t_path)
                except Exception:
                    pass
                break

    # 2. Если ни у одного профиля нет такого токена:
    if not matched_profile_dir:
        # Проверяем совпадение по имени папки (например, папка названа email'ом)
        for p in profile_dirs:
            if p.name.lower() == active_email.lower():
                matched_profile_dir = p
                break

        # Проверяем active_profile_name из active_profile.json
        if not matched_profile_dir and active_profile_name:
            cand = PROFILES_DIR / active_profile_name
            if cand.exists() and cand.is_dir():
                matched_profile_dir = cand

        # Проверяем папки без токена (например, только что созданная папка при добавлении аккаунта)
        if not matched_profile_dir:
            empty_dirs = [p for p in profile_dirs if not (p / "antigravity-oauth-token").exists()]
            if len(empty_dirs) == 1:
                matched_profile_dir = empty_dirs[0]

        # Если так и не нашли подходящую папку — создаем account-1 (или account-N)
        if not matched_profile_dir:
            if not profile_dirs:
                matched_profile_dir = PROFILES_DIR / "account-1"
            else:
                matched_profile_dir = PROFILES_DIR / f"account-{len(profile_dirs) + 1}"
            matched_profile_dir.mkdir(parents=True, exist_ok=True)

        # Копируем активный токен в найденную / созданную папку
        target_token = matched_profile_dir / "antigravity-oauth-token"
        try:
            shutil.copy2(ACTIVE_TOKEN_FILE, target_token)
        except Exception:
            pass

        # Привязываем порт
        port = get_profile_port(matched_profile_dir.name)
        write_active_proxy_env(port, active_email, profile_name=matched_profile_dir.name)


def ensure_initial_profile_backup() -> None:
    """Обеспечивает наличие резервной копии профилей."""
    _auto_heal_profiles()


def list_profiles(skip_reassign: bool = False) -> list[dict[str, Any]]:
    """Возвращает список всех сохраненных профилей Google OAuth с привязанными портами."""
    _auto_heal_profiles()
    if not skip_reassign:
        try:
            reassign_profiles_using_claude_proxies()
        except Exception:
            pass

    active_info = get_token_info(ACTIVE_TOKEN_FILE)
    active_email = active_info.get("email", "")

    active_prof_name = ""
    if ACTIVE_PROFILE_JSON_FILE.exists():
        try:
            with open(ACTIVE_PROFILE_JSON_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                active_prof_name = d.get("profile_name", "")
        except Exception:
            pass

    result: list[dict[str, Any]] = []

    if not PROFILES_DIR.exists():
        return result

    profile_dirs = [PROFILES_DIR / n for n in get_profile_order()]
    save_profile_order([p.name for p in profile_dirs])

    # Загружаем текущие статусы прокси для быстрой индикации
    proxies_map = {p.get("port"): p for p in proxy_manager.load_proxies() if isinstance(p.get("port"), int)}

    # Точно определяем имя активного профиля
    if not active_prof_name or not (PROFILES_DIR / active_prof_name).exists():
        if ACTIVE_TOKEN_FILE.exists() and active_email and active_email not in ("Без email", "Нет файла", "Ошибка чтения"):
            for p in profile_dirs:
                t_path = p / "antigravity-oauth-token"
                if t_path.exists() and get_token_info(t_path).get("email") == active_email:
                    active_prof_name = p.name
                    break

    for position, p in enumerate(profile_dirs, start=1):
        token_path = p / "antigravity-oauth-token"
        token_exists = token_path.exists()

        is_active = (p.name == active_prof_name) if active_prof_name else False

        if is_active and token_exists and ACTIVE_TOKEN_FILE.exists():
            try:
                if ACTIVE_TOKEN_FILE.stat().st_mtime > token_path.stat().st_mtime:
                    shutil.copy2(ACTIVE_TOKEN_FILE, token_path)
            except Exception:
                pass

        info = get_token_info(token_path, is_active=is_active)
        port = get_profile_port(p.name)

        proxy_info = proxies_map.get(port, {})
        is_man = is_profile_port_manual(p.name)
        p_num = (port - BASE_SOCKS5_PORT + 1) if port >= BASE_SOCKS5_PORT else 1

        p_email = info.get("email", "Неизвестно") if token_exists else "Не авторизован"
        p_expiry = info.get("expiry_text", "-") if token_exists else "Авторизация не завершена"

        result.append({
            "position": position,
            "profile_name": p.name,
            "folder_path": str(p),
            "email": p_email,
            "name": info.get("name", "-"),
            "is_active": is_active,
            "is_expired": info.get("is_expired", False),
            "expiry_text": p_expiry,
            "token_exists": token_exists,
            "port": port,
            "is_manual": is_man,
            "proxy_num": p_num,
            "proxy_status": proxy_info.get("status", "unknown"),
            "proxy_ip": proxy_info.get("ip", "-"),
            "proxy_country": proxy_info.get("country", "undefined"),
        })

    # Синхронизируем с proxies.json
    sync_profiles_with_proxies(result)

    # Если активный профиль определен, гарантируем актуальность active_proxy.env
    active_p = next((p for p in result if p.get("is_active")), None)
    if active_p:
        write_active_proxy_env(active_p.get("port", BASE_SOCKS5_PORT), active_p.get("email", ""), profile_name=active_p.get("profile_name", ""))
    elif result and not ACTIVE_PROFILE_JSON_FILE.exists():
        first_valid = next((p for p in result if p.get("token_exists")), result[0])
        write_active_proxy_env(first_valid.get("port", BASE_SOCKS5_PORT), first_valid.get("email", ""), profile_name=first_valid.get("profile_name", ""))

    return result


def switch_profile(profile_name: str) -> tuple[bool, str]:
    """Переключает активный аккаунт и переключает персональный SOCKS5-порт."""
    source = PROFILES_DIR / profile_name / "antigravity-oauth-token"
    if not source.exists():
        return False, f"Файл токена не найден в профиле '{profile_name}'. Авторизуйте этот аккаунт заново."

    try:
        ACTIVE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

        # 1. Перед перезаписью: сохраняем текущий активный токен в его профиль
        if ACTIVE_TOKEN_FILE.exists():
            active_info = get_token_info(ACTIVE_TOKEN_FILE)
            active_email = active_info.get("email", "")
            curr_prof = None
            if PROFILES_DIR.exists():
                for p in PROFILES_DIR.iterdir():
                    if p.is_dir() and p.name != profile_name:
                        t = p / "antigravity-oauth-token"
                        if t.exists() and get_token_info(t).get("email") == active_email:
                            curr_prof = p
                            break
            if not curr_prof and ACTIVE_PROFILE_JSON_FILE.exists():
                try:
                    with open(ACTIVE_PROFILE_JSON_FILE, "r", encoding="utf-8") as f:
                        old_meta = json.load(f)
                        old_pname = old_meta.get("profile_name", "")
                        if old_pname and old_pname != profile_name:
                            old_cand = PROFILES_DIR / old_pname
                            if old_cand.exists() and old_cand.is_dir():
                                curr_prof = old_cand
                except Exception:
                    pass

            if curr_prof:
                try:
                    shutil.copy2(ACTIVE_TOKEN_FILE, curr_prof / "antigravity-oauth-token")
                except Exception:
                    pass

        # 2. Активируем новый токен
        shutil.copy2(source, ACTIVE_TOKEN_FILE)

        port = get_profile_port(profile_name)
        info = get_token_info(ACTIVE_TOKEN_FILE)
        email = info.get("email", "")

        # Записываем активный порт для agy
        write_active_proxy_env(port, email, profile_name=profile_name)

        try:
            proxies = proxy_manager.load_proxies()
            curr_p = next((p for p in proxies if p.get("port") == port), {})
            strategy_manager.log_event(
                account_email=email,
                profile_name=profile_name,
                port=port,
                ip=curr_p.get("ip", "-"),
                country=curr_p.get("country", "undefined"),
                event="profile_switch",
                note=f"Ручное переключение на {profile_name}",
            )
        except Exception:
            pass

        return True, f"Активен: {email} ({profile_name}) ➔ SOCKS5 :{port}"
    except Exception as e:
        return False, f"Ошибка переключения: {e}"


def check_token_live(profile_name: str, timeout: float = 4.0) -> dict[str, Any]:
    """Проверяет токен в Google API через персональный SOCKS5-порт этого профиля."""
    token_file = PROFILES_DIR / profile_name / "antigravity-oauth-token"
    if not token_file.exists():
        return {"valid": False, "error": "Токен не найден"}

    port = get_profile_port(profile_name)
    proxy_url = f"socks5h://127.0.0.1:{port}"

    try:
        with open(token_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        id_token = data.get("id_token")
        if not id_token:
            return {"valid": False, "error": "id_token отсутствует"}

        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}"
        proxies = {"https": proxy_url, "http": proxy_url}

        resp = requests.get(url, proxies=proxies, timeout=timeout)
        if resp.status_code == 200:
            res_data = resp.json()
            return {
                "valid": True,
                "email": res_data.get("email"),
                "email_verified": res_data.get("email_verified"),
                "port": port,
                "error": None,
            }
        else:
            return {
                "valid": False,
                "port": port,
                "error": f"HTTP {resp.status_code}: {resp.text[:100]}",
            }
    except Exception as e:
        return {
            "valid": False,
            "port": port,
            "error": f"Ошибка соединения через SOCKS5 :{port}: {e}",
        }


def launch_add_account_terminal(profile_name: str | None = None) -> bool:
    """Запускает окно терминала с процедурой добавления нового аккаунта Google."""
    cmd_name = profile_name or f"account-{len(list_profiles()) + 1}"
    
    port = get_profile_port(cmd_name)
    reserve_new_profile_port(cmd_name, port)
    
    http_port = 10000 + port
    env_str = f"ALL_PROXY='socks5h://127.0.0.1:{port}' HTTPS_PROXY='http://127.0.0.1:{http_port}' HTTP_PROXY='http://127.0.0.1:{http_port}'"
    
    bash_cmd = (
        f"echo 'Проверка IP через SOCKS5 :{port}...' ; "
        f"env {env_str} curl --max-time 15 -s -4 -x socks5h://127.0.0.1:{port} ifconfig.me ; echo ; echo ; "
        f"env {env_str} gemini-oauth add {cmd_name} ; "
        f"echo; echo 'Готово.'; read -n 1 -s -r"
    )

    try:
        subprocess.Popen(
            ["wsl.exe", "-d", WSL_DISTRO, "-u", WSL_USER, "bash", "-lc", bash_cmd],
            creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 16,
        )
        return True
    except Exception:
        return False


import token_vault_manager

def delete_profile(profile_name: str) -> tuple[bool, str]:
    """Удаляет сохраненный профиль."""
    target = PROFILES_DIR / profile_name
    if not target.exists():
        return False, "Профиль не найден"

    with token_vault_manager.auto_unlock_context():
        try:
            is_active = False
            t_path = target / "antigravity-oauth-token"
            t_enc = Path(str(t_path) + ".enc")
            
            if (t_path.exists() or t_enc.exists()) and (ACTIVE_TOKEN_FILE.exists() or Path(str(ACTIVE_TOKEN_FILE) + ".enc").exists()):
                active_info = get_token_info(ACTIVE_TOKEN_FILE)
                target_info = get_token_info(t_path)
                if active_info.get("email") and active_info.get("email") == target_info.get("email"):
                    is_active = True

            shutil.rmtree(target)
            try:
                settings_manager.clear_account_proxy_binding(profile_name)
            except Exception:
                pass

            if is_active:
                ACTIVE_TOKEN_FILE.unlink(missing_ok=True)
                Path(str(ACTIVE_TOKEN_FILE) + ".enc").unlink(missing_ok=True)

            # Оставшиеся профили сдвигаются вверх по номерам и получают порты своих новых номеров
            remaining = list_profiles()
            if is_active:
                remaining_with_token = [p for p in remaining if p.get("token_exists")]
                if remaining_with_token:
                    switch_profile(remaining_with_token[0]["profile_name"])

            return True, f"Профиль {profile_name} успешно удален"
        except Exception as e:
            return False, f"Ошибка удаления: {e}"


def rename_profile(old_name: str, new_name: str) -> tuple[bool, str]:
    """Переименовывает существующий профиль, обновляя пути, конфигурации, привязки и метки."""
    old_clean = (old_name or "").strip()
    new_clean = (new_name or "").strip()

    if not new_clean:
        return False, "Имя профиля не может быть пустым"

    if any(c in r'\/:*?"<>|' for c in new_clean):
        return False, "Имя содержит недопустимые символы: \\ / : * ? \" < > |"

    if old_clean == new_clean:
        return True, "Без изменений"
    old_dir = PROFILES_DIR / old_clean
    if not old_dir.exists():
        return False, f"Профиль '{old_clean}' не найден"

    new_dir = PROFILES_DIR / new_clean
    if new_dir.exists():
        return False, f"Профиль с именем '{new_clean}' уже существует"

    with token_vault_manager.auto_unlock_context():
        order = get_profile_order()
        try:
            shutil.move(str(old_dir), str(new_dir))
            save_profile_order([new_clean if n == old_clean else n for n in order])

            cfg_file = new_dir / "profile_config.json"
            cfg_data = {"profile_name": new_clean}
            if cfg_file.exists():
                try:
                    with open(cfg_file, "r", encoding="utf-8") as f:
                        cfg_data = json.load(f)
                except Exception:
                    pass
            cfg_data["profile_name"] = new_clean
            try:
                with open(cfg_file, "w", encoding="utf-8") as f:
                    json.dump(cfg_data, f, indent=2)
            except Exception:
                pass

            binding = settings_manager.get_account_proxy_binding(old_clean)
            if binding and isinstance(binding, dict):
                settings_manager.clear_account_proxy_binding(old_clean)
                settings_manager.set_account_proxy_binding(
                    new_clean,
                    binding.get("port"),
                    manual=binding.get("manual", False),
                )

            if ACTIVE_PROFILE_JSON_FILE.exists():
                try:
                    with open(ACTIVE_PROFILE_JSON_FILE, "r", encoding="utf-8") as f:
                        act_meta = json.load(f)
                    if act_meta.get("profile_name") == old_clean:
                        act_meta["profile_name"] = new_clean
                        with open(ACTIVE_PROFILE_JSON_FILE, "w", encoding="utf-8") as f:
                            json.dump(act_meta, f, indent=2)
                except Exception:
                    pass

            try:
                proxies = proxy_manager.load_proxies()
                changed_proxies = False
                for p in proxies:
                    lbl = p.get("label", "")
                    if f"Gemini: {old_clean}" in lbl:
                        p["label"] = lbl.replace(f"Gemini: {old_clean}", f"Gemini: {new_clean}")
                        changed_proxies = True
                if changed_proxies:
                    proxy_manager.save_proxies(proxies)
            except Exception:
                pass

            from backup_manager import log_strategy_change
            log_strategy_change(f"ПРОФИЛЬ ПЕРЕИМЕНОВАН: {old_clean} -> {new_clean}")
            return True, f"Профиль переименован в '{new_clean}'"
        except Exception as e:
            return False, f"Внутренняя ошибка переименования: {e}"

def switch_next_profile() -> tuple[bool, str]:
    """Переключает на следующий по порядку профиль (Round-Robin)."""
    profiles = [p for p in list_profiles() if p.get("token_exists")]
    if not profiles:
        return False, "Нет сохраненных профилей с токенами"
    if len(profiles) == 1:
        return False, "В системе только 1 профиль с токеном. Добавьте второй аккаунт для переключения."

    curr_idx = 0
    for idx, p in enumerate(profiles):
        if p.get("is_active"):
            curr_idx = idx
            break

    next_idx = (curr_idx + 1) % len(profiles)
    next_name = profiles[next_idx]["profile_name"]
    return switch_profile(next_name)


def is_guard_running() -> bool:
    """Проверяет, запущен ли сторож автосмены в WSL."""
    try:
        p = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "-u", WSL_USER, "bash", "-lic", "gemini-oauth guard status"],
            capture_output=True, text=True, timeout=2.5,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000
        )
        return "АКТИВНА" in p.stdout
    except Exception:
        return False


def start_guard() -> tuple[bool, str]:
    """Запускает фоновый сторож автосмены в WSL."""
    try:
        p = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "-u", WSL_USER, "bash", "-lic", "gemini-oauth guard start"],
            capture_output=True, text=True, timeout=3.5,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000
        )
        return True, p.stdout.strip()
    except Exception as e:
        return False, str(e)


def stop_guard() -> tuple[bool, str]:
    """Останавливает фоновый сторож автосмены в WSL."""
    try:
        p = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "-u", WSL_USER, "bash", "-lic", "gemini-oauth guard stop"],
            capture_output=True, text=True, timeout=3.5,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000
        )
        return True, p.stdout.strip()
    except Exception as e:
        return False, str(e)


def handle_proxy_failover(failed_port: int, preferred_country: str | None = None) -> tuple[bool, str, dict[str, Any] | None]:
    """Выполняет автоподбор рабочего SOCKS5-прокси при сбое текущего.
    
    1. Ищет рабочий прокси (с приоритетом той же страны).
    2. При нахождении:
       - Переназначает активный туннель в active_proxy.env и active_profile.json
       - Обновляет привязку активного профиля
    """
    best = proxy_manager.find_best_fallback_proxy(failed_port, preferred_country)
    if not best:
        return False, "Не найден ни один доступный альтернативный SOCKS5-прокси", None

    new_port = best.get("port")
    new_country = best.get("country", "undefined")
    new_ip = best.get("ip", "-")

    active_prof_name = None
    profiles = list_profiles()
    for p in profiles:
        if p.get("is_active"):
            active_prof_name = p["profile_name"]
            write_active_proxy_env(new_port, p.get("email", ""))
            break

    if not active_prof_name:
        write_active_proxy_env(new_port)

    msg = f"Автопереключение SOCKS5 :{failed_port} ➔ :{new_port} ({new_ip} • {new_country})"
    return True, msg, best


