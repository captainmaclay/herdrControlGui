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
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import requests
import proxy_manager
import strategy_manager

def get_wsl_user(distro: str = "Ubuntu") -> str:
    """Определяет активного пользователя WSL2."""
    env_user = os.environ.get("WSL_USER")
    if env_user:
        return env_user
    try:
        res = subprocess.run(["wsl", "-d", distro, "whoami"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return os.environ.get("USERNAME", "default")


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
    """Форматирует время истечения токена в человекочитаемый вид."""
    if not exp_timestamp:
        return "Не определено", False

    now = time.time()
    diff = exp_timestamp - now
    if diff <= 0:
        if is_active:
            return "Активен (Авто-refresh в agy)", False
        else:
            return "В резерве (Готов к работе)", False

    hours = int(diff // 3600)
    minutes = int((diff % 3600) // 60)
    if hours > 0:
        return f"Действителен ({hours}ч {minutes}м)", False
    else:
        return f"Действителен ({minutes} мин)", False


def get_token_info(token_file: Path, is_active: bool = False) -> dict[str, Any]:
    """Считывает метаданные из файла antigravity-oauth-token."""
    if not token_file.exists():
        return {
            "exists": False,
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
        exp = claims.get("exp")
        expiry_text, is_expired = format_expiry(exp, is_active=is_active)

        return {
            "exists": True,
            "email": email,
            "name": name,
            "exp": exp,
            "expiry_text": expiry_text,
            "is_expired": is_expired,
            "id_token": id_token,
            "auth_method": data.get("auth_method", "oauth"),
            "file_path": str(token_file),
        }
    except Exception as e:
        return {
            "exists": True,
            "email": "Ошибка чтения",
            "name": str(e),
            "is_expired": True,
            "expiry_text": "Ошибка JSON",
        }


def get_profile_port(profile_name: str) -> int:
    """Определяет персональный SOCKS5-порт для профиля (1081, 1082, 1083...)."""
    cfg_file = PROFILES_DIR / profile_name / "profile_config.json"
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d.get("port"), int):
                    return d["port"]
        except Exception:
            pass

    # Расчет по имени профиля или порядку
    port = BASE_SOCKS5_PORT
    if profile_name.startswith("account-"):
        part = profile_name.split("-", 1)[1]
        if part.isdigit():
            port = BASE_SOCKS5_PORT + (int(part) - 1)
    else:
        try:
            profiles = sorted([p.name for p in PROFILES_DIR.iterdir() if p.is_dir()])
            if profile_name in profiles:
                port = BASE_SOCKS5_PORT + profiles.index(profile_name)
            else:
                port = BASE_SOCKS5_PORT + len(profiles)
        except Exception:
            port = BASE_SOCKS5_PORT

    save_profile_port(profile_name, port)
    return port


def save_profile_port(profile_name: str, port: int) -> None:
    """Сохраняет персональный порт в конфиг профиля."""
    cfg_file = PROFILES_DIR / profile_name / "profile_config.json"
    try:
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump({"profile_name": profile_name, "port": port}, f, indent=2)
    except Exception:
        pass


def write_active_proxy_env(port: int, email: str = "") -> None:
    """Записывает активный порт в active_proxy.env и active_profile.json в WSL."""
    CLI_DIR.mkdir(parents=True, exist_ok=True)
    try:
        env_content = (
            f'# Auto-generated by Herdr Control Center\n'
            f'export ALL_PROXY="socks5h://127.0.0.1:{port}"\n'
            f'export HTTPS_PROXY="http://127.0.0.1:{port}"\n'
            f'export HTTP_PROXY="http://127.0.0.1:{port}"\n'
            f'export GEMINI_ACTIVE_PORT="{port}"\n'
            f'export GEMINI_ACTIVE_ACCOUNT="{email}"\n'
        )
        with open(ACTIVE_PROXY_ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write(env_content)

        meta = {
            "port": port,
            "email": email,
            "proxy_url": f"socks5h://127.0.0.1:{port}",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
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


def sync_profiles_with_proxies(profiles: list[dict[str, Any]]) -> None:
    """Синхронизирует список прокси в proxies.json с профилями Gemini."""
    proxies = proxy_manager.load_proxies()
    existing_ports = {p.get("port"): p for p in proxies if isinstance(p.get("port"), int)}
    changed = False

    for prof in profiles:
        port = prof.get("port", BASE_SOCKS5_PORT)
        email = prof.get("email", "")
        p_name = prof.get("profile_name", "")
        label = f"Gemini: {p_name} ({email})" if email and email != "Без email" else f"Gemini: {p_name}"

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
            })
            changed = True
        else:
            # Обновляем метку при необходимости
            curr = existing_ports[port]
            if curr.get("label") != label and "Gemini:" in label:
                curr["label"] = label
                changed = True

    if changed:
        proxy_manager.save_proxies(proxies)


def ensure_initial_profile_backup() -> None:
    """Если профилей еще нет, но активный токен есть — создает профиль account-1."""
    try:
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        profiles = [p for p in PROFILES_DIR.iterdir() if p.is_dir()]
        if not profiles and ACTIVE_TOKEN_FILE.exists():
            p1 = PROFILES_DIR / "account-1"
            p1.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ACTIVE_TOKEN_FILE, p1 / "antigravity-oauth-token")
            save_profile_port("account-1", BASE_SOCKS5_PORT)
            write_active_proxy_env(BASE_SOCKS5_PORT)
    except Exception:
        pass


def list_profiles() -> list[dict[str, Any]]:
    """Возвращает список всех сохраненных профилей Google OAuth с привязанными портами."""
    ensure_initial_profile_backup()

    active_info = get_token_info(ACTIVE_TOKEN_FILE)
    active_email = active_info.get("email", "")

    result: list[dict[str, Any]] = []

    if not PROFILES_DIR.exists():
        return result

    try:
        profile_dirs = sorted([p for p in PROFILES_DIR.iterdir() if p.is_dir()])
    except Exception:
        return result

    # Загружаем текущие статусы прокси для быстрой индикации
    proxies_map = {p.get("port"): p for p in proxy_manager.load_proxies() if isinstance(p.get("port"), int)}

    for p in profile_dirs:
        token_path = p / "antigravity-oauth-token"
        raw_info = get_token_info(token_path)
        is_active = (
            raw_info.get("email") == active_email
            and active_email != ""
            and active_email != "Без email"
        )

        if is_active and ACTIVE_TOKEN_FILE.exists():
            try:
                if ACTIVE_TOKEN_FILE.stat().st_mtime > token_path.stat().st_mtime:
                    shutil.copy2(ACTIVE_TOKEN_FILE, token_path)
            except Exception:
                pass

        info = get_token_info(token_path, is_active=is_active)
        port = get_profile_port(p.name)

        proxy_info = proxies_map.get(port, {})
        result.append({
            "profile_name": p.name,
            "folder_path": str(p),
            "email": info.get("email", "Неизвестно"),
            "name": info.get("name", "Пользователь Google"),
            "is_active": is_active,
            "is_expired": info.get("is_expired", False),
            "expiry_text": info.get("expiry_text", "-"),
            "token_exists": info.get("exists", False),
            "port": port,
            "proxy_status": proxy_info.get("status", "unknown"),
            "proxy_ip": proxy_info.get("ip", "-"),
            "proxy_country": proxy_info.get("country", "undefined"),
        })

    # Синхронизируем с proxies.json
    sync_profiles_with_proxies(result)

    # Если активный профиль определен, гарантируем актуальность active_proxy.env
    active_p = next((p for p in result if p.get("is_active")), None)
    if active_p:
        write_active_proxy_env(active_p.get("port", BASE_SOCKS5_PORT), active_p.get("email", ""))

    return result


def switch_profile(profile_name: str) -> tuple[bool, str]:
    """Переключает активный аккаунт и переключает персональный SOCKS5-порт."""
    source = PROFILES_DIR / profile_name / "antigravity-oauth-token"
    if not source.exists():
        return False, f"Файл токена не найден в профиле {profile_name}"

    try:
        ACTIVE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, ACTIVE_TOKEN_FILE)

        port = get_profile_port(profile_name)
        info = get_token_info(ACTIVE_TOKEN_FILE)
        email = info.get("email", "")

        # Записываем активный порт для agy
        write_active_proxy_env(port, email)

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
    bash_cmd = f"gemini-oauth add {cmd_name}"

    try:
        subprocess.Popen(
            ["wt.exe", "-w", "0", "nt", "wsl.exe", "-d", WSL_DISTRO, "-u", WSL_USER, "bash", "-lic", bash_cmd],
            creationflags=0,
        )
        return True
    except Exception:
        pass

    try:
        win_cmd = f'cmd.exe /c start "Herdr — Вход в Google Gemini OAuth" wsl.exe -d {WSL_DISTRO} -u {WSL_USER} bash -lic "{bash_cmd}"'
        subprocess.Popen(win_cmd, shell=True)
        return True
    except Exception:
        return False


def delete_profile(profile_name: str) -> tuple[bool, str]:
    """Удаляет сохраненный профиль."""
    target = PROFILES_DIR / profile_name
    if not target.exists():
        return False, "Профиль не найден"

    try:
        shutil.rmtree(target)
        return True, f"Профиль {profile_name} успешно удален"
    except Exception as e:
        return False, f"Ошибка удаления: {e}"


def switch_next_profile() -> tuple[bool, str]:
    """Переключает на следующий по порядку профиль (Round-Robin)."""
    profiles = list_profiles()
    if not profiles:
        return False, "Нет сохраненных профилей"
    if len(profiles) == 1:
        return False, "В системе только 1 профиль. Добавьте account-2 для переключения."

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
            save_profile_port(active_prof_name, new_port)
            write_active_proxy_env(new_port, p.get("email", ""))
            break

    if not active_prof_name:
        write_active_proxy_env(new_port)

    msg = f"Автопереключение SOCKS5 :{failed_port} ➔ :{new_port} ({new_ip} • {new_country})"
    return True, msg, best


