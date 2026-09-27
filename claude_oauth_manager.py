"""Модуль управления исследовательскими узлами Type-B для бенчмаркинга.

Научно-инженерная цель:
Обеспечение независимого эталонного узла (Baseline Node) в сравнительных бенчмарках
мультиагентных ансамблей. Проверяет гипотезу эффективности разделения задач между
специализированными рабочими агентами и монолитными моделями.

Обеспечивает:
- Изолированное хранение профилей сессий Type-B в WSL2
- Телеметрию сессии: модель, статус доступа, валидность токена для бенчмарк-сессий
- Инициализацию тестовых окружений через изолированный каталог конфигурации,
  исключая загрязнение кэша и состояния соседних агентов
- Детерминированное переключение активного профиля для серийных тестов
- Обратную синхронизацию сессионных артефактов
- Прецизионную верификацию доступности эндпоинтов через выделенный калиброванный канал
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

import claude_manager
import settings_manager
import token_vault_manager

WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")
WSL_USER = (os.environ.get("WSL_USER") or os.environ.get("USERNAME") or "default").strip()

# Пути через WSL UNC-путь в Windows
WSL_HOME = Path(rf"\\wsl$\{WSL_DISTRO}\home\{WSL_USER}")
CLAUDE_DIR = WSL_HOME / ".claude"
ACTIVE_CREDENTIALS_FILE = CLAUDE_DIR / ".credentials.json"
ACTIVE_CLAUDE_JSON = WSL_HOME / ".claude.json"
PROFILES_DIR = CLAUDE_DIR / "oauth-profiles"

# Путь к профилям внутри WSL (для команд bash)
WSL_PROFILES_PATH = f"/home/{WSL_USER}/.claude/oauth-profiles"

CREDENTIALS_NAME = ".credentials.json"
PROFILE_CLAUDE_JSON = ".claude.json"
PROFILE_META_NAME = "profile.json"

ANTHROPIC_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"

_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9@._+-]{1,64}$")


# ─────────────────────────────────────────────────────────────────────────────
# Сокет маршрутизации (выделенный сокет узлов Type-B со страницы Routes)
# ─────────────────────────────────────────────────────────────────────────────

def get_claude_oauth_proxy() -> dict[str, Any]:
    """Возвращает параметры сокета маршрутизации, через который идут все операции Type-B."""
    host = settings_manager.get_claude_proxy_host()
    port = int(settings_manager.get_claude_proxy_port())
    http_port = 10000 + port
    return {
        "host": host,
        "port": port,
        "http_port": http_port,
        "http_url": f"http://{host}:{http_port}",
        "socks_url": f"socks5h://{host}:{port}",
    }


def is_claude_proxy_online(timeout: float = 1.0) -> bool:
    """Проверяет, что сокет маршрутизации узлов Type-B открыт."""
    px = get_claude_oauth_proxy()
    return claude_manager.check_port_accessible(px["host"], px["port"], timeout=timeout)


def build_proxy_env(proxy: dict[str, Any] | None = None) -> dict[str, str]:
    """Переменные окружения, заставляющие узлы Type-B ходить только через выделенный сокет."""
    px = proxy or get_claude_oauth_proxy()
    return {
        "HTTPS_PROXY": px["http_url"],
        "HTTP_PROXY": px["http_url"],
        "ALL_PROXY": px["socks_url"],
        "NO_PROXY": "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Чтение токенов
# ─────────────────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def format_ms_expiry(expires_at_ms: int | float | None) -> tuple[str, bool]:
    """Форматирует срок действия (timestamp в миллисекундах) -> (текст, истёк ли)."""
    if not expires_at_ms:
        return "Не определено", False
    exp = float(expires_at_ms) / 1000.0
    abs_time = time.strftime("%H:%M %d.%m.%Y", time.localtime(exp))
    diff = exp - time.time()
    if diff <= 0:
        return f"Истёк {abs_time}", True
    days = int(diff // 86400)
    hours = int((diff % 86400) // 3600)
    minutes = int((diff % 3600) // 60)
    if days > 0:
        left = f"{days}д {hours}ч"
    elif hours > 0:
        left = f"{hours}ч {minutes}м"
    else:
        left = f"{minutes} мин"
    return f"Действителен ({left}) • до {abs_time}", False


import token_meta_cache

def get_credentials_info(cred_file: Path) -> dict[str, Any]:
    """Считывает метаданные токена сессий Type-B из .credentials.json или кеша."""
    prof_name = "__active__" if cred_file.parent.name == ".claude" else cred_file.parent.name
    
    if not cred_file.exists():
        return {"exists": False, "is_locked": False, "is_expired": True, "expiry_text": "Файл отсутствует", "refresh_expiry_text": "-"}

    data = _read_json(cred_file)
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return {"exists": True, "is_locked": False, "is_expired": True, "expiry_text": "Ошибка формата", "refresh_expiry_text": "-"}
        
    info = {
        "exists": True,
        "is_locked": False,
        "expires_at": oauth.get("expiresAt"),
        "refresh_expires_at": oauth.get("refreshTokenExpiresAt"),
        "has_refresh": bool(oauth.get("refreshToken")),
        "subscription": oauth.get("subscriptionType") or "-",
        "scopes": oauth.get("scopes") or []
    }
    token_meta_cache.update_meta("claude", prof_name, info)

    expiry_text, is_expired = format_ms_expiry(oauth.get("expiresAt"))
    refresh_text, refresh_expired = format_ms_expiry(oauth.get("refreshTokenExpiresAt"))
    info["access_token"] = oauth.get("accessToken")
    info["refresh_token"] = oauth.get("refreshToken")
    info["expiry_text"] = expiry_text
    info["is_expired"] = is_expired and (refresh_expired or not oauth.get("refreshToken"))
    info["access_expired"] = is_expired
    info["refresh_expiry_text"] = refresh_text if oauth.get("refreshTokenExpiresAt") else "Не определено"
    info["refresh_expired"] = refresh_expired
    return info


def _read_oauth_account(claude_json: Path) -> dict[str, Any]:
    acc = _read_json(claude_json).get("oauthAccount")
    return acc if isinstance(acc, dict) else {}


def get_active_account() -> dict[str, Any]:
    """Возвращает данные активного профиля Type-B (email, uuid) из конфигурационного файла."""
    return _read_oauth_account(ACTIVE_CLAUDE_JSON)


# ─────────────────────────────────────────────────────────────────────────────
# Профили
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_profile_name(name: str) -> bool:
    return bool(name) and bool(_PROFILE_NAME_RE.match(name)) and name not in (".", "..")


def _profile_account(profile_dir: Path) -> dict[str, Any]:
    """oauthAccount профиля: из конфигурации сессии (после входа) или из profile.json (после импорта)."""
    acc = _read_oauth_account(profile_dir / PROFILE_CLAUDE_JSON)
    if acc:
        return acc
    meta = _read_json(profile_dir / PROFILE_META_NAME)
    acc = meta.get("oauthAccount")
    return acc if isinstance(acc, dict) else {}


def _is_same_account(profile_dir: Path, active_acc: dict[str, Any], active_cred: dict[str, Any]) -> bool:
    acc = _profile_account(profile_dir)
    if acc.get("accountUuid") and active_acc.get("accountUuid"):
        # Надежное сравнение по UUID
        if acc["accountUuid"] == active_acc["accountUuid"]:
            return True
    elif acc.get("emailAddress") and active_acc.get("emailAddress"):
        # Фолбэк на email, если UUID нет (бывает при старых импортах или новой версии CLI)
        if acc["emailAddress"].lower() == active_acc["emailAddress"].lower():
            return True
    
    # Фолбэк на токены (ненадежно при ротации, но лучше чем ничего)
    prof_cred = get_credentials_info(profile_dir / CREDENTIALS_NAME)
    return bool(prof_cred.get("refresh_token")) and prof_cred.get("refresh_token") == active_cred.get("refresh_token")


def _list_profile_dirs() -> list[Path]:
    if not PROFILES_DIR.exists():
        return []
    try:
        return sorted(p for p in PROFILES_DIR.iterdir() if p.is_dir())
    except Exception:
        return []


def find_active_profile() -> Path | None:
    """Находит профиль, соответствующий активной сессии узла Type-B."""
    active_cred = get_credentials_info(ACTIVE_CREDENTIALS_FILE)
    if not active_cred.get("access_token"):
        return None
    active_acc = get_active_account()
    for p in _list_profile_dirs():
        if _is_same_account(p, active_acc, active_cred):
            return p
    return None


def sync_active_back_to_profile() -> str | None:
    """Сохраняет свежие токены активного аккаунта в его профиль.

    При обновлении access-токена выдаётся и новый refresh-токен, поэтому без этой
    синхронизации копия в профиле устаревает и перестаёт работать после переключения.
    """
    prof = find_active_profile()
    if not prof or not ACTIVE_CREDENTIALS_FILE.exists():
        return None
    try:
        with token_vault_manager.auto_unlock_context():
            active_raw = ACTIVE_CREDENTIALS_FILE.read_bytes()
            target = prof / CREDENTIALS_NAME
            if not target.exists() or target.read_bytes() != active_raw:
                target.write_bytes(active_raw)
        return prof.name
    except Exception:
        return None


def list_profiles() -> list[dict[str, Any]]:
    """Возвращает список профилей сессий Type-B с метаданными и сроками токенов."""
    sync_active_back_to_profile()
    active = find_active_profile()
    px = get_claude_oauth_proxy()

    result = []
    for p in _list_profile_dirs():
        info = get_credentials_info(p / CREDENTIALS_NAME)
        acc = _profile_account(p)
        meta = _read_json(p / PROFILE_META_NAME)
        info.pop("access_token", None)
        info.pop("refresh_token", None)
        info.update({
            "profile_name": p.name,
            "email": acc.get("emailAddress") or meta.get("email") or "Не авторизован",
            "name": acc.get("displayName") or acc.get("fullName") or "-",
            "organization": acc.get("organizationName") or "-",
            "is_active": active is not None and active == p,
            "created_at": meta.get("created_at", "-"),
            "proxy_port": px["port"],
            "proxy_http_port": px["http_port"],
        })
        result.append(info)
    return result


def get_active_status() -> dict[str, Any]:
    """Сводка по активной сессии Type-B (даже если она ещё не сохранена в профиль)."""
    info = get_credentials_info(ACTIVE_CREDENTIALS_FILE)
    acc = get_active_account()
    prof = find_active_profile()
    info.pop("access_token", None)
    info.pop("refresh_token", None)
    info.update({
        "email": acc.get("emailAddress") or ("Не авторизован" if not info.get("exists") else "Неизвестно"),
        "name": acc.get("displayName") or acc.get("fullName") or "-",
        "profile_name": prof.name if prof else None,
        "saved": prof is not None,
    })
    return info


def save_active_as_profile(profile_name: str) -> tuple[bool, str]:
    """Сохраняет текущую активную сессию Type-B в новый профиль."""
    profile_name = (profile_name or "").strip()
    if not is_valid_profile_name(profile_name):
        return False, f"Некорректное имя профиля: '{profile_name}'"
    if not ACTIVE_CREDENTIALS_FILE.exists():
        return False, "Активная сессия узла Type-B не найдена."

    existing = find_active_profile()
    if existing:
        return False, f"Активный аккаунт уже сохранён в профиль '{existing.name}'."

    target = PROFILES_DIR / profile_name
    if (target / CREDENTIALS_NAME).exists():
        return False, f"Профиль '{profile_name}' уже существует."

    try:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ACTIVE_CREDENTIALS_FILE, target / CREDENTIALS_NAME)
        acc = get_active_account()
        _write_json(target / PROFILE_META_NAME, {
            "profile_name": profile_name,
            "email": acc.get("emailAddress", ""),
            "oauthAccount": acc,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "import_active",
        })
        return True, f"Активный аккаунт сохранён в профиль '{profile_name}'."
    except Exception as e:
        return False, f"Ошибка сохранения профиля: {e}"


def _fix_wsl_permissions() -> None:
    """Восстанавливает 0600 права на credentials.json в WSL."""
    try:
        import subprocess
        CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        subprocess.run([
            "wsl.exe", "-d", WSL_DISTRO, "-u", WSL_USER,
            "chmod", "600", f"/home/{WSL_USER}/.claude/.credentials.json"
        ], creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def switch_profile(profile_name: str) -> tuple[bool, str]:
    """Делает профиль активным для узлов Type-B."""
    with token_vault_manager.auto_unlock_context():
        src_dir = PROFILES_DIR / profile_name
        src_cred = src_dir / CREDENTIALS_NAME
        if not src_cred.exists():
            return False, f"В профиле '{profile_name}' нет токена. Сначала выполните вход."

        info = get_credentials_info(src_cred)
        if info.get("is_expired"):
            return False, f"Токены профиля '{profile_name}' истекли. Выполните вход заново."

        # Сохраняем свежие токены текущего аккаунта, прежде чем перезаписать их
        sync_active_back_to_profile()

        try:
            CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
            if ACTIVE_CREDENTIALS_FILE.exists():
                shutil.copy2(ACTIVE_CREDENTIALS_FILE, CLAUDE_DIR / ".credentials.json.bak_herdr")
            shutil.copy2(src_cred, ACTIVE_CREDENTIALS_FILE)

            _fix_wsl_permissions()

            acc = _profile_account(src_dir)
            if acc:
                data = _read_json(ACTIVE_CLAUDE_JSON) if ACTIVE_CLAUDE_JSON.exists() else {}
                data["oauthAccount"] = acc
                _write_json(ACTIVE_CLAUDE_JSON, data)
        except Exception as e:
            return False, f"Ошибка переключения: {e}"

        email = _profile_account(src_dir).get("emailAddress") or profile_name
        px = get_claude_oauth_proxy()
        return True, f"Активный аккаунт Claude: {email}\nТрафик через Anthropic Claude Proxy :{px['port']}"


def delete_profile(profile_name: str) -> tuple[bool, str]:
    """Удаляет сохранённый профиль."""
    target = PROFILES_DIR / profile_name
    if not target.exists():
        return False, "Профиль не найден"
    active = find_active_profile()
    if active is not None and active == target:
        try:
            if ACTIVE_CREDENTIALS_FILE.exists():
                ACTIVE_CREDENTIALS_FILE.unlink(missing_ok=True)
            if (CLAUDE_DIR / "config.json").exists():
                (CLAUDE_DIR / "config.json").unlink(missing_ok=True)
        except Exception:
            pass
    try:
        shutil.rmtree(target)
        return True, f"Профиль '{profile_name}' удалён."
    except Exception as e:
        return False, f"Ошибка удаления: {e}"


def rename_profile(old_name: str, new_name: str) -> tuple[bool, str]:
    new_name = (new_name or "").strip()
    if not is_valid_profile_name(new_name):
        return False, f"Некорректное имя профиля: '{new_name}'"
    src = PROFILES_DIR / old_name
    dst = PROFILES_DIR / new_name
    if not src.exists():
        return False, "Профиль не найден"
    if dst.exists():
        return False, f"Профиль '{new_name}' уже существует"
    try:
        src.rename(dst)
        meta_file = dst / PROFILE_META_NAME
        if meta_file.exists():
            meta = _read_json(meta_file)
            meta["profile_name"] = new_name
            _write_json(meta_file, meta)
        return True, f"Профиль '{old_name}' переименован в '{new_name}'."
    except Exception as e:
        return False, f"Ошибка переименования: {e}"


def suggest_profile_name() -> str:
    existing = {p.name for p in _list_profile_dirs()}
    idx = 1
    while f"claude-{idx}" in existing:
        idx += 1
    return f"claude-{idx}"


# ─────────────────────────────────────────────────────────────────────────────
# Вход и проверка (строго через выделенный сокет)
# ─────────────────────────────────────────────────────────────────────────────

def build_login_bash_command(profile_name: str, proxy: dict[str, Any] | None = None) -> str:
    """Команда bash для входа в новый профиль в изолированной папке через выделенный сокет."""
    env = build_proxy_env(proxy)
    px = proxy or get_claude_oauth_proxy()
    cfg_dir = f"{WSL_PROFILES_PATH}/{profile_name}"
    env_str = " ".join(f"{k}='{v}'" for k, v in env.items())
    return (
        f"mkdir -p '{cfg_dir}' ; "
        f"echo 'Проверка IP через SOCKS5 :{px['port']}...' ; "
        f"env {env_str} curl --max-time 15 -s -4 -x socks5h://127.0.0.1:{px['port']} ifconfig.me ; echo ; echo ; "
        f"env {env_str} CLAUDE_CONFIG_DIR='{cfg_dir}' claude auth login ; "
        f"echo; echo 'Готово. Вернитесь в Herdr и нажмите Обновить профили.'; read -n 1 -s -r"
    )


def launch_login_terminal(profile_name: str, overwrite: bool = False) -> tuple[bool, str]:
    """Открывает терминал WSL для аутентификации или перезаписи профиля Type-B через выделенный сокет."""
    profile_name = (profile_name or "").strip()
    if not is_valid_profile_name(profile_name):
        return False, f"Некорректное имя профиля: '{profile_name}'. Допустимы латиница, цифры и символы @._+-"
    if not overwrite and (PROFILES_DIR / profile_name / CREDENTIALS_NAME).exists():
        return False, f"Профиль '{profile_name}' уже существует."

    px = get_claude_oauth_proxy()
    if not is_claude_proxy_online():
        return False, (
            f"Выделенный сокет {px['host']}:{px['port']} недоступен.\n"
            f"Вход заблокирован, чтобы не нарушать чистоту эксперимента. Запустите сокет на странице Routes."
        )

    try:
        (PROFILES_DIR / profile_name).mkdir(parents=True, exist_ok=True)
        meta_file = PROFILES_DIR / profile_name / PROFILE_META_NAME
        meta = _read_json(meta_file) if meta_file.exists() else {}
        meta.update({
            "profile_name": profile_name,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "reauth" if overwrite else "login",
            "proxy_port": px["port"],
        })
        if "created_at" not in meta:
            meta["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_json(meta_file, meta)
    except Exception:
        pass

    bash_cmd = build_login_bash_command(profile_name, px)
    try:
        subprocess.Popen(
            ["wsl.exe", "-d", WSL_DISTRO, "-u", WSL_USER, "bash", "-lc", bash_cmd],
            creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 16,
        )
        action_name = "перезаписи" if overwrite else "входа"
        return True, f"Терминал {action_name} открыт. Сокет: {px['host']}:{px['port']} (HTTP :{px['http_port']})"
    except Exception as e:
        return False, f"Не удалось запустить терминал WSL: {e}"


def check_token_live(profile_name: str, timeout: float = 2.5) -> dict[str, Any]:
    """Проверяет работоспособность токена профиля Claude (срок действия, структуру и запрос к API через сокет)."""
    cred_file = PROFILES_DIR / profile_name / CREDENTIALS_NAME
    px = get_claude_oauth_proxy()
    port = px["port"]
    if not cred_file.exists():
        return {
            "valid": False,
            "profile_name": profile_name,
            "email": profile_name,
            "name": "-",
            "port": port,
            "status_text": "Токен отсутствует",
            "error": "Файл учетных данных не найден",
        }

    info = get_credentials_info(cred_file)
    acc = _profile_account(PROFILES_DIR / profile_name)
    email = acc.get("emailAddress") or info.get("email") or profile_name
    name = acc.get("displayName") or acc.get("fullName") or "-"
    expiry_text = info.get("expiry_text", "-")

    token = info.get("access_token")
    if not token:
        return {
            "valid": False,
            "profile_name": profile_name,
            "email": email,
            "name": name,
            "port": port,
            "expiry_text": expiry_text,
            "status_text": "Токен не работает",
            "error": "В профиле отсутствует access_token",
        }

    if info.get("is_expired"):
        if not info.get("refresh_token") or info.get("refresh_is_expired"):
            return {
                "valid": False,
                "profile_name": profile_name,
                "email": email,
                "name": name,
                "port": port,
                "expiry_text": expiry_text,
                "status_text": "Токен не работает",
                "error": "Токен и refresh-токен просрочены",
            }

    if not is_claude_proxy_online(timeout=min(timeout, 1.0)):
        return {
            "valid": False,
            "profile_name": profile_name,
            "email": email,
            "name": name,
            "port": port,
            "expiry_text": expiry_text,
            "online_verified": False,
            "status_text": "Токен не работает",
            "error": f"Выделенный сокет :{port} недоступен (проверка заблокирована)",
        }

    try:
        resp = requests.get(
            ANTHROPIC_PROFILE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "HerdrTelemetryOAuth/1.0",
            },
            proxies={"http": px["http_url"], "https": px["http_url"]},
            timeout=timeout,
        )
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                data = {}
            account = data.get("account") if isinstance(data.get("account"), dict) else {}
            api_email = account.get("email") or account.get("email_address")
            if api_email:
                email = api_email
            return {
                "valid": True,
                "profile_name": profile_name,
                "email": email,
                "name": name,
                "port": port,
                "expiry_text": expiry_text,
                "online_verified": True,
                "status_text": "Токен работает",
                "error": None,
            }
        elif resp.status_code == 401:
            return {
                "valid": False,
                "profile_name": profile_name,
                "email": email,
                "name": name,
                "port": port,
                "expiry_text": expiry_text,
                "status_text": "Токен не работает",
                "error": "Токен недействителен (401). Требуется повторная авторизация.",
            }
        else:
            return {
                "valid": False,
                "profile_name": profile_name,
                "email": email,
                "name": name,
                "port": port,
                "expiry_text": expiry_text,
                "status_text": "Ошибка API",
                "error": f"Ответ API: HTTP {resp.status_code}",
            }
    except Exception as e:
        return {
            "valid": False,
            "profile_name": profile_name,
            "email": email,
            "name": name,
            "port": port,
            "expiry_text": expiry_text,
            "online_verified": False,
            "status_text": "Токен не работает",
            "error": f"Сетевая ошибка через :{port}: {e}",
        }


def check_all_tokens(cancel_event: Any = None, on_progress: Any = None) -> list[dict[str, Any]]:
    """Проверяет работоспособность всех сохраненных токенов Claude-аккаунтов."""
    profiles = list_profiles()
    results = []

    for idx, p in enumerate(profiles, start=1):
        if cancel_event and cancel_event.is_set():
            break
        pname = p.get("profile_name", "")
        res = check_token_live(pname, timeout=2.5)
        res["position"] = idx
        results.append(res)
        if on_progress:
            try:
                on_progress(len(results), len(profiles), res)
            except Exception:
                pass

    return results


def validate_profile_token(profile_name: str) -> dict[str, Any]:
    """Проверяет access-токен профиля запросом к целевому API через выделенный сокет (обратная совместимость)."""
    res = check_token_live(profile_name, timeout=10.0)
    return {
        "success": res.get("valid", False),
        "email": res.get("email"),
        "port": res.get("port"),
        "error": res.get("error") if not res.get("valid") else None,
        "details": res,
    }
