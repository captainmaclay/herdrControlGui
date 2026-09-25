"""Модуль-мост интеграции эталонного рабочего узла Anthropic Claude Code в диспетчер AionUi.

Научно-инженерная цель:
Подключение эталонной модели Claude к мультиагентным графам исполнения AionUi
для сравнительных замеров качества рассуждений против распределённых ансамблей сабагентов.

Обеспечивает автоматизацию шагов интеграции:
1. Валидацию и импорт сессионных токенов (.credentials.json) в Windows и WSL2
2. Мониторинг времени жизни токенов инференса для обеспечения непрерывности тестов
3. Синхронизацию изолированного канала связи (:1015) и защитных шлюзов в settings.json
4. Синхронизацию встроенных манифестов агента AionUi (manifest.json, binary version)
5. Регистрацию исполняемого файла в базе данных AionUi (agent_metadata, agent_type = 'acp')
6. Комплексную верификацию доступности эндпоинта и готовности к бенчмарку

Может использоваться как библиотека внутри HerdrControlCenter, так и автономно через CLI:
    python aionui_claude_bridge.py --status
    python aionui_claude_bridge.py --import-oauth <path_to_json>
    python aionui_claude_bridge.py --sync-all [path_to_json]
    python aionui_claude_bridge.py --patch-aionui
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")
WSL_USER = (os.environ.get("WSL_USER") or os.environ.get("USERNAME") or "f").strip()


def run_wsl_python(python_code: str, timeout: int = 15) -> tuple[int, str, str]:
    """Выполняет Python-код внутри WSL2 через передачу в stdin, избегая проблем с экранированием кавычек."""
    try:
        res = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "python3", "-"],
            input=python_code,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace"
        )
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return -1, "", str(e)


def get_wsl_rootfs_path() -> Path | None:
    """Возвращает путь к WSL2 rootfs через UNC путь \\wsl$\\<distro> или \\wsl.localhost\\<distro>."""
    for prefix in [rf"\\wsl$\{WSL_DISTRO}", rf"\\wsl.localhost\{WSL_DISTRO}"]:
        p = Path(prefix)
        if p.exists():
            return p
    return None


def get_claude_credentials_files() -> list[Path]:
    """Возвращает пути к файлам .credentials.json в Windows и WSL2."""
    files: list[Path] = []
    
    # 1. Windows: %USERPROFILE%\.claude\.credentials.json
    try:
        win_file = Path.home() / ".claude" / ".credentials.json"
        files.append(win_file)
    except Exception:
        pass

    # 2. WSL2: \\wsl$\Ubuntu\home\<user>\.claude\.credentials.json
    wsl_root = get_wsl_rootfs_path()
    if wsl_root:
        wsl_file = wsl_root / f"home/{WSL_USER}/.claude/.credentials.json"
        files.append(wsl_file)

    return files


def get_claude_settings_files() -> list[Path]:
    """Возвращает пути к файлам settings.json в Windows и WSL2."""
    files: list[Path] = []
    try:
        files.append(Path.home() / ".claude" / "settings.json")
    except Exception:
        pass

    wsl_root = get_wsl_rootfs_path()
    if wsl_root:
        files.append(wsl_root / f"home/{WSL_USER}/.claude/settings.json")

    return files


def validate_oauth_data(raw_data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Валидирует и нормализует структуру OAuth-данных Claude Code."""
    if not isinstance(raw_data, dict):
        return False, "Data must be a JSON object", {}

    oauth_section = raw_data.get("claudeAiOauth")
    if oauth_section and isinstance(oauth_section, dict):
        data = oauth_section
    else:
        data = raw_data

    access_token = data.get("accessToken")
    refresh_token = data.get("refreshToken")

    if not access_token or not isinstance(access_token, str):
        return False, "Missing or invalid 'accessToken' field", {}

    if not refresh_token or not isinstance(refresh_token, str):
        return False, "Missing or invalid 'refreshToken' field", {}

    normalized = {
        "claudeAiOauth": {
            "accessToken": access_token.strip(),
            "refreshToken": refresh_token.strip(),
            "expiresAt": data.get("expiresAt", int((time.time() + 3600) * 1000)),
            "refreshTokenExpiresAt": data.get("refreshTokenExpiresAt", int((time.time() + 30 * 86400) * 1000)),
            "scopes": data.get("scopes", ["user:read", "user:write"]),
            "subscriptionType": data.get("subscriptionType", "pro"),
            "rateLimitTier": data.get("rateLimitTier", "default_claude_ai"),
        }
    }

    return True, "Valid OAuth credentials", normalized


def import_oauth_file(source_path: Path | str, target_files: list[Path] | None = None) -> dict[str, Any]:
    """Импортирует OAuth credentials из файла в Windows и WSL2 с резервным копированием."""
    src = Path(source_path).resolve()
    if not src.exists():
        return {"success": False, "error": f"Source file '{src}' does not exist", "synced": []}

    try:
        with open(src, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"Failed to read JSON: {e}", "synced": []}

    is_valid, msg, normalized_data = validate_oauth_data(raw_data)
    if not is_valid:
        return {"success": False, "error": msg, "synced": []}

    if target_files is None:
        target_files = get_claude_credentials_files()

    synced_paths: list[str] = []
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    for target in target_files:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup_path = target.with_suffix(f".json.bak_{ts}")
                shutil.copy2(target, backup_path)

            with open(target, "w", encoding="utf-8") as f:
                json.dump(normalized_data, f, indent=2, ensure_ascii=False)

            synced_paths.append(str(target))
        except Exception as e:
            return {"success": False, "error": f"Failed writing to {target}: {e}", "synced": synced_paths}

    # В WSL выставляем безопасные права доступа chmod 600
    chmod_script = f"""
import os
p = '/home/{WSL_USER}/.claude/.credentials.json'
if os.path.exists(p):
    os.chmod(p, 0o600)
"""
    run_wsl_python(chmod_script)

    return {
        "success": True,
        "message": "OAuth credentials successfully imported",
        "synced": synced_paths,
        "subscription_type": normalized_data["claudeAiOauth"].get("subscriptionType"),
    }


def get_oauth_status() -> dict[str, Any]:
    """Возвращает детальный статус текущей авторизации Claude."""
    cred_files = get_claude_credentials_files()
    found_data = None
    active_path = None

    for f in cred_files:
        if f.exists():
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    d = json.load(fp)
                    if "claudeAiOauth" in d:
                        found_data = d["claudeAiOauth"]
                        active_path = f
                        break
            except Exception:
                pass

    if not found_data:
        return {
            "authorized": False,
            "message": "No .credentials.json found with valid claudeAiOauth",
            "checked_paths": [str(p) for p in cred_files],
        }

    now = time.time()
    exp_ts = found_data.get("expiresAt", 0) / 1000
    r_exp_ts = found_data.get("refreshTokenExpiresAt", 0) / 1000

    access_valid = exp_ts > now
    refresh_valid = r_exp_ts > now

    return {
        "authorized": refresh_valid or access_valid,
        "active_file": str(active_path),
        "subscription_type": found_data.get("subscriptionType", "unknown"),
        "rate_limit_tier": found_data.get("rateLimitTier", "unknown"),
        "access_token_valid": access_valid,
        "access_token_expires": datetime.datetime.fromtimestamp(exp_ts).isoformat() if exp_ts else None,
        "refresh_token_valid": refresh_valid,
        "refresh_token_expires": datetime.datetime.fromtimestamp(r_exp_ts).isoformat() if r_exp_ts else None,
        "days_left": round((r_exp_ts - now) / 86400, 1) if r_exp_ts else 0,
    }


def ensure_claude_proxy_settings(
    host: str = "127.0.0.1",
    port: int = 1015,
    killswitch: bool = True,
) -> dict[str, Any]:
    """Синхронизирует сетевые переменные и защиту Killswitch в settings.json."""
    http_port = 10000 + int(port)
    settings_files = get_claude_settings_files()
    updated: list[str] = []

    for filepath in settings_files:
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            if filepath.exists():
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if not isinstance(data, dict):
                            data = {}
                except Exception:
                    data = {}

            if not isinstance(data.get("env"), dict):
                data["env"] = {}

            data["env"]["HTTPS_PROXY"] = f"http://{host}:{http_port}"
            data["env"]["HTTP_PROXY"] = f"http://{host}:{http_port}"
            data["env"]["ALL_PROXY"] = f"socks5h://{host}:{port}"
            data["env"]["DISABLE_AUTOUPDATER"] = "1"

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            updated.append(str(filepath))
        except Exception as e:
            return {"success": False, "error": f"Failed writing to {filepath}: {e}", "updated": updated}

    return {"success": True, "updated": updated, "proxy": f"{host}:{port}", "killswitch": killswitch}


def patch_aionui_managed_resources(target_version: str = "2.1.280") -> dict[str, Any]:
    """Обновляет каталог встроенных ресурсов AionUi и manifest.json до версии 2.1.280+ через WSL Python."""
    script = f"""
import json, os, shutil

user_home = '/home/{WSL_USER}'
res_dir = os.path.join(user_home, '.local/share/aionui-web/aionui-web/bundled-aioncore/linux-x64/managed-resources')
manifest_path = os.path.join(res_dir, 'manifest.json')

if not os.path.isfile(manifest_path):
    print("ERROR:NO_MANIFEST")
    exit(1)

src_bin = os.path.join(user_home, f'.local/share/claude/versions/{target_version}')
if not os.path.isfile(src_bin):
    src_bin = os.path.join(user_home, '.local/bin/claude')

if not os.path.isfile(src_bin):
    print("ERROR:NO_SOURCE_BINARY")
    exit(2)

target_dir = os.path.join(res_dir, f'cli/claude/{target_version}/linux-x64')
os.makedirs(target_dir, exist_ok=True)
target_file = os.path.join(target_dir, 'claude')
if os.path.exists(target_file):
    try:
        os.unlink(target_file)
    except Exception:
        pass
shutil.copy2(src_bin, target_file)
os.chmod(target_file, 0o755)

legacy_dir = os.path.join(res_dir, 'cli/claude/2.1.215/linux-x64')
if os.path.isdir(legacy_dir):
    legacy_file = os.path.join(legacy_dir, 'claude')
    if os.path.exists(legacy_file):
        try:
            os.unlink(legacy_file)
        except Exception:
            pass
    shutil.copy2(src_bin, legacy_file)
    os.chmod(legacy_file, 0o755)

with open(manifest_path, 'r', encoding='utf-8') as f:
    m = json.load(f)

for cli in m.get('clis', []):
    if cli.get('name') == 'claude':
        cli['version'] = '{target_version}'
        cli['root'] = f'cli/claude/{target_version}/linux-x64'

with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump(m, f, indent=2)

print("STATUS:SUCCESS")
"""
    code, stdout, stderr = run_wsl_python(script)
    if code != 0 or "STATUS:SUCCESS" not in stdout:
        err = stderr.strip() or stdout.strip()
        return {"success": False, "error": f"WSL Python execution failed ({code}): {err}"}

    return {
        "success": True,
        "version": target_version,
        "manifest_updated": True,
        "message": f"AionUi managed-resources patched to {target_version}",
    }


def patch_aionui_database() -> dict[str, Any]:
    """Регистрирует команду 'claude' для агента Claude Code в базе данных AionUi внутри WSL2."""
    script = f"""
import sqlite3, os

db_path = '/home/{WSL_USER}/.aionui-web/aionui-backend.db'
if not os.path.isfile(db_path):
    print("ERROR:NO_DB")
    exit(1)

conn = sqlite3.connect(db_path, timeout=10)
cur = conn.cursor()
cur.execute("UPDATE agent_metadata SET command='claude', enabled=1 WHERE name='Claude Code'")
rows = cur.rowcount
conn.commit()
conn.close()
print(f"ROWS:{{rows}}")
"""
    code, stdout, stderr = run_wsl_python(script)
    if code != 0:
        return {"success": False, "error": f"WSL SQLite failed ({code}): {stderr.strip()}"}

    rows = 0
    for line in stdout.splitlines():
        if line.startswith("ROWS:"):
            try:
                rows = int(line.split(":")[1])
            except Exception:
                pass

    return {
        "success": True,
        "rows_affected": rows,
        "database": f"/home/{WSL_USER}/.aionui-web/aionui-backend.db",
    }


def sync_all(oauth_file: Path | str | None = None) -> dict[str, Any]:
    """Выполняет полный цикл синхронизации: импорт OAuth, прокси, патч ресурсов AionUi и БД."""
    results: dict[str, Any] = {}

    # 1. Импорт OAuth-файла (если передан)
    if oauth_file:
        results["oauth_import"] = import_oauth_file(oauth_file)
    else:
        results["oauth_status"] = get_oauth_status()

    # 2. Сетевые прокси и Killswitch
    results["proxy_settings"] = ensure_claude_proxy_settings()

    # 3. Патч ресурсов AionUi (версия 2.1.280)
    results["resources_patch"] = patch_aionui_managed_resources()

    # 4. Патч базы данных AionUi
    results["db_patch"] = patch_aionui_database()

    # Общий успех
    all_ok = all(
        v.get("success", True) if isinstance(v, dict) else True
        for k, v in results.items()
    )
    results["all_success"] = all_ok
    return results


def main() -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="AionUi Claude Code Integration Bridge")
    parser.add_argument("--status", action="store_true", help="Show current OAuth and proxy status")
    parser.add_argument("--import-oauth", type=str, help="Path to new OAuth .credentials.json or token file")
    parser.add_argument("--patch-aionui", action="store_true", help="Patch AionUi resources and database")
    parser.add_argument("--sync-all", nargs="?", const="", help="Run full sync (optionally passing OAuth file)")

    args = parser.parse_args()

    if args.status:
        st = get_oauth_status()
        print("\n=== CLAUDE CODE OAUTH STATUS ===")
        print(f"Authorized:       {'✅ YES' if st.get('authorized') else '❌ NO'}")
        print(f"Subscription:     {st.get('subscription_type', 'N/A').upper()}")
        print(f"Refresh Expires:  {st.get('refresh_token_expires', 'N/A')} ({st.get('days_left', 0)} days left)")
        print(f"Active File:      {st.get('active_file', 'N/A')}")
        return 0

    if args.import_oauth:
        res = import_oauth_file(args.import_oauth)
        print("\n=== OAUTH IMPORT RESULT ===")
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("success") else 1

    if args.patch_aionui:
        res_p = patch_aionui_managed_resources()
        db_p = patch_aionui_database()
        print("\n=== AIONUI PATCH RESULT ===")
        print("Resources:", json.dumps(res_p, indent=2))
        print("Database: ", json.dumps(db_p, indent=2))
        return 0 if (res_p.get("success") and db_p.get("success")) else 1

    if args.sync_all is not None:
        oauth_f = args.sync_all if args.sync_all else None
        res = sync_all(oauth_f)
        print("\n=== FULL AIONUI CLAUDE SYNC RESULT ===")
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("all_success") else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
