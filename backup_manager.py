"""Модуль создания криптографических снимков исследовательского стенда (Golden Snapshots).

Научно-инженерная цель:
Обеспечение воспроизводимости результатов научных экспериментов.
Позволяет зафиксировать полное состояние экспериментального стенда
(топологии сети, веса комбо-балансировщиков, калиброванные токены узлов)
в детерминированный криптографический архив для развертывания на контрольных машинах.

Безопасность и целостность:
- Стандарт шифрования: AES-256-GCM (Authenticated Encryption with Associated Data)
- Вывод ключа: PBKDF2-HMAC-SHA256 (соль 16 байт, 600 000 итераций)
- Контрольная хэш-сумма пароля: SHA-256 (отображается в UI)
- Пароль сохраняется в файле .env (переменная BACKUP_PASSWORD)

Что входит в снимок стенда:
- Конфигурации сокетов, задержек и топологий (settings.json, proxies.json, .env)
- Журнал телеметрии кумулятивной стабильности каналов (strategy_history.json)
- Сессионные артефакты и профили узлов Gemini в WSL2 (~/.gemini/profiles/, ~/.gemini/antigravity-cli/)
- Сессионные артефакты и профили эталонного узла Claude (~/.claude/.credentials.json, ~/.claude/oauth-profiles/)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SETTINGS_FILE = BASE_DIR / "settings.json"

DEFAULT_BACKUP_DIR = Path.home() / "Documents" / "HerdrBackups"

def get_wsl_user(distro: str = "Ubuntu") -> str:
    """Определяет активного пользователя WSL2 без блокирующих вызовов сети."""
    return (os.environ.get("WSL_USER") or os.environ.get("USERNAME") or "default").strip()


WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")
WSL_USER = get_wsl_user(WSL_DISTRO)


def get_wsl_rootfs_path() -> Path | None:
    """Возвращает путь к WSL2 rootfs через UNC путь \\wsl$\\<distro> или \\wsl.localhost\\<distro>."""
    for prefix in [rf"\\wsl$\{WSL_DISTRO}", rf"\\wsl.localhost\{WSL_DISTRO}"]:
        p = Path(prefix)
        if p.exists():
            return p
    return None


WSL_ROOT = get_wsl_rootfs_path()
WSL_HOME = (WSL_ROOT / f"home/{WSL_USER}") if WSL_ROOT else Path(rf"\\wsl$\{WSL_DISTRO}\home\{WSL_USER}")
WSL_GEMINI_DIR = WSL_HOME / ".gemini"
WSL_CLAUDE_DIR = WSL_HOME / ".claude"
WSL_AIONUI_DIR = WSL_HOME / ".aionui-web"

WIN_CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_OAUTH_PROFILES_DIRNAME = "oauth-profiles"
CLAUDE_OAUTH_PROFILE_FILES = (".credentials.json", ".claude.json", "profile.json")
WIN_AIONUI_DIR = Path.home() / ".aionui-web"

MAGIC_HEADER = b"HBAK\x01"  # Herdr Backup Version 1
PBKDF2_ITERATIONS = 600_000
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32  # 256 bits


def compute_password_fingerprint(password: str) -> str:
    """Вычисляет компактный SHA-256 отпечаток введенного пароля."""
    if not password:
        return ""
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    # Форматируем как группы: a1b2 c3d4 e5f6 7890
    return f"{digest[:4]} {digest[4:8]} {digest[8:12]} {digest[12:16]}".upper()


def load_backup_password() -> str:
    """Считывает пароль бэкапа из .env."""
    if not ENV_FILE.exists():
        return ""
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BACKUP_PASSWORD="):
                    val = line.split("=", 1)[1].strip()
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    elif val.startswith("'") and val.endswith("'"):
                        val = val[1:-1]
                    return val
    except Exception:
        pass
    return ""


def save_backup_password(password: str) -> None:
    """Сохраняет пароль бэкапа в .env."""
    lines = []
    found = False
    if ENV_FILE.exists():
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []

    new_lines = []
    for line in lines:
        if line.strip().startswith("BACKUP_PASSWORD="):
            new_lines.append(f"BACKUP_PASSWORD={password}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"BACKUP_PASSWORD={password}\n")

    try:
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception:
        pass


def get_backup_config() -> dict[str, Any]:
    """Возвращает настройки бэкапа из settings.json."""
    defaults = {
        "backup_dir": str(DEFAULT_BACKUP_DIR),
        "auto_backup_enabled": False,
        "backup_interval_hours": 12,
        "last_backup_time": "-",
    }
    if not SETTINGS_FILE.exists():
        return defaults

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            defaults.update({k: data[k] for k in defaults if k in data})
    except Exception:
        pass
    return defaults


def update_backup_config(key: str, value: Any) -> None:
    """Обновляет параметр бэкапа в settings.json."""
    data = {}
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[key] = value
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _derive_key(password: str, salt: bytes) -> bytes:
    """Генерирует криптостойкий ключ 256-бит через PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def _safe_write_file(dest_p: Path, content: bytes) -> bool:
    """Безопасная запись файла с созданием родительских папок и защитой от блокировок."""
    try:
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        tmp_p = dest_p.with_name(dest_p.name + ".restore_tmp")
        try:
            with open(tmp_p, "wb") as f:
                f.write(content)
            if dest_p.exists():
                try:
                    os.replace(tmp_p, dest_p)
                    return True
                except Exception:
                    pass
            else:
                os.replace(tmp_p, dest_p)
                return True
        except Exception:
            pass
        finally:
            if tmp_p.exists():
                try:
                    tmp_p.unlink()
                except Exception:
                    pass

        # Fallback прямая запись
        with open(dest_p, "wb") as f:
            f.write(content)
        return True
    except Exception:
        return False


def create_encrypted_backup(
    password: str,
    target_dir: str | Path | None = None,
) -> tuple[bool, str, str | None]:
    """Создает зашифрованный AES-256-GCM архив со всеми файлами и токенами.
    
    Возвращает (success, message, file_path)
    """
    if not password:
        return False, "Пароль для шифрования не может быть пустым.", None

    if target_dir is None:
        cfg = get_backup_config()
        target_dir = Path(cfg.get("backup_dir", DEFAULT_BACKUP_DIR))
    else:
        target_dir = Path(target_dir)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f"Не удалось создать папку бэкапа {target_dir}: {e}", None

    # 1. Формируем ZIP-архив в памяти
    zip_buffer = io.BytesIO()
    files_packed = 0

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1.1 Файлы Windows Herdr (настройки, прокси, история, лог интеграций)
        win_files = [
            "settings.json",
            "proxies.json",
            ".env",
            "strategy_history.json",
            "integrations.log",
            ".herdr_vault_meta.json",
        ]
        for fn in win_files:
            fp = BASE_DIR / fn
            if fp.exists():
                zf.write(fp, arcname=f"win/{fn}")
                files_packed += 1

        # 1.2 Файлы Claude Code (Windows)
        if WIN_CLAUDE_DIR.exists():
            for fn in [".credentials.json", "settings.json"]:
                fp = WIN_CLAUDE_DIR / fn
                if fp.exists():
                    zf.write(fp, arcname=f"claude/win/{fn}")
                    files_packed += 1
            for bak in WIN_CLAUDE_DIR.glob(".credentials.json.bak_*"):
                if bak.is_file():
                    zf.write(bak, arcname=f"claude/win/{bak.name}")
                    files_packed += 1

        # 1.3 Файлы Claude Code (WSL2)
        if WSL_CLAUDE_DIR.exists():
            for fn in [".credentials.json", "settings.json"]:
                fp = WSL_CLAUDE_DIR / fn
                if fp.exists():
                    zf.write(fp, arcname=f"claude/wsl/{fn}")
                    files_packed += 1
            for bak in WSL_CLAUDE_DIR.glob(".credentials.json.bak_*"):
                if bak.is_file():
                    zf.write(bak, arcname=f"claude/wsl/{bak.name}")
                    files_packed += 1

            # Профили Claude OAuth (~/.claude/oauth-profiles/<имя>/): только токены и метаданные
            oauth_dir = WSL_CLAUDE_DIR / CLAUDE_OAUTH_PROFILES_DIRNAME
            if oauth_dir.exists():
                for prof_dir in sorted(p for p in oauth_dir.iterdir() if p.is_dir()):
                    for fn in CLAUDE_OAUTH_PROFILE_FILES:
                        fp = prof_dir / fn
                        if fp.is_file():
                            zf.write(fp, arcname=f"claude/wsl/{CLAUDE_OAUTH_PROFILES_DIRNAME}/{prof_dir.name}/{fn}")
                            files_packed += 1

        # 1.4 Файлы AionUi (WSL2: SQLite база данных, настройки расширений, манифест)
        if WSL_AIONUI_DIR.exists():
            for fn in ["aionui-backend.db", "extension-states.json", "extension-user-states.json"]:
                fp = WSL_AIONUI_DIR / fn
                if fp.exists():
                    try:
                        zf.write(fp, arcname=f"aionui/wsl/{fn}")
                        files_packed += 1
                    except Exception:
                        pass
            fp_man = WSL_AIONUI_DIR / "resources" / "manifest.json"
            if fp_man.exists():
                try:
                    zf.write(fp_man, arcname="aionui/wsl/resources/manifest.json")
                    files_packed += 1
                except Exception:
                    pass

        # 1.5 Файлы AionUi (Windows)
        if WIN_AIONUI_DIR.exists():
            for fn in ["aionui-backend.db", "extension-states.json", "extension-user-states.json"]:
                fp = WIN_AIONUI_DIR / fn
                if fp.exists():
                    try:
                        zf.write(fp, arcname=f"aionui/win/{fn}")
                        files_packed += 1
                    except Exception:
                        pass

        # 1.6 Файлы WSL Gemini (~/.gemini/)
        if WSL_GEMINI_DIR.exists():
            for root, dirs, files in os.walk(WSL_GEMINI_DIR):
                root_path = Path(root)
                rel_root = root_path.relative_to(WSL_GEMINI_DIR)
                # Игнорируем временные сокеты и лишние тяжелые логи
                for f in files:
                    if f.endswith((".sock", ".tmp")):
                        continue
                    full_p = root_path / f
                    arc_p = f"wsl/{rel_root / f}".replace("\\", "/")
                    try:
                        zf.write(full_p, arcname=arc_p)
                        files_packed += 1
                    except Exception:
                        pass

        # 1.7 Метаданные бэкапа
        b_cfg = get_backup_config()
        manifest = {
            "version": "1.2",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files_count": files_packed,
            "host_os": "Windows / WSL2 Ubuntu",
            "password_fingerprint": compute_password_fingerprint(password),
            "auto_backup_enabled": b_cfg.get("auto_backup_enabled", False),
            "backup_interval_hours": b_cfg.get("backup_interval_hours", 12),
            "includes_integrations": True,
            "includes_claude": True,
            "includes_claude_oauth_profiles": True,
            "includes_aionui": True,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    raw_zip_data = zip_buffer.getvalue()

    # 2. Шифруем AES-256-GCM
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_key(password, salt)

    aesgcm = AESGCM(key)
    # Используем magic header как associated data для гарантированной аутентификации заголовка
    ciphertext = aesgcm.encrypt(nonce, raw_zip_data, associated_data=MAGIC_HEADER)

    # 3. Записываем файл .hbak
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    out_filename = f"herdr_backup_{timestamp_str}.hbak"
    out_path = target_dir / out_filename

    try:
        with open(out_path, "wb") as f_out:
            f_out.write(MAGIC_HEADER)
            f_out.write(salt)
            f_out.write(nonce)
            f_out.write(ciphertext)

        update_backup_config("last_backup_time", time.strftime("%Y-%m-%d %H:%M:%S"))
        msg = f"Бэкап успешно создан ({files_packed} файлов, {len(ciphertext) // 1024 + 1} КБ): {out_filename}"
        return True, msg, str(out_path)
    except Exception as e:
        return False, f"Ошибка записи файла бэкапа: {e}", None


def restore_encrypted_backup(
    backup_file_path: str | Path,
    password: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Расшифровывает и восстанавливает всё окружение из файла .hbak.
    
    Возвращает (success, message, manifest)
    """
    if not password:
        return False, "Сначала введите пароль для расшифровки бэкапа!", None

    bp = Path(backup_file_path)
    if not bp.exists():
        return False, f"Файл бэкапа не найден: {bp}", None

    try:
        with open(bp, "rb") as f_in:
            header = f_in.read(len(MAGIC_HEADER))
            if header != MAGIC_HEADER:
                return False, "Файл поврежден или имеет неверный формат (не является бэкапом Herdr).", None

            salt = f_in.read(SALT_SIZE)
            nonce = f_in.read(NONCE_SIZE)
            ciphertext = f_in.read()

        if len(salt) != SALT_SIZE or len(nonce) != NONCE_SIZE or not ciphertext:
            return False, "Файл бэкапа имеет неполную структуру.", None

        key = _derive_key(password, salt)
        aesgcm = AESGCM(key)

        try:
            decrypted_data = aesgcm.decrypt(nonce, ciphertext, associated_data=MAGIC_HEADER)
        except Exception:
            return False, "Неверный пароль! Аутентификация AES-256-GCM не пройдена или файл был поврежден.", None

        # 2. Распаковываем ZIP
        zip_buf = io.BytesIO(decrypted_data)
        restored_count = 0
        manifest = {}

        with zipfile.ZipFile(zip_buf, "r") as zf:
            namelist = zf.namelist()
            if "manifest.json" in namelist:
                try:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                except Exception:
                    pass

            for name in namelist:
                if name == "manifest.json":
                    continue

                content = zf.read(name)

                if name.startswith("win/"):
                    rel_name = name[4:]
                    dest_p = BASE_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("wsl/"):
                    rel_name = name[4:]
                    dest_p = WSL_GEMINI_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("claude/win/"):
                    rel_name = name[len("claude/win/"):]
                    dest_p = WIN_CLAUDE_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("claude/wsl/"):
                    rel_name = name[len("claude/wsl/"):]
                    dest_p = WSL_CLAUDE_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("aionui/wsl/"):
                    rel_name = name[len("aionui/wsl/"):]
                    dest_p = WSL_AIONUI_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("aionui/win/"):
                    rel_name = name[len("aionui/win/"):]
                    dest_p = WIN_AIONUI_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

        # Отказоустойчивая валидация и миграция истории стратегий после импорта
        try:
            import strategy_manager
            repaired_history = strategy_manager.load_history()
            strategy_manager.save_history(repaired_history)
        except Exception:
            pass

        # Синхронизация восстановленных настроек Claude Code и Killswitch
        try:
            import claude_manager
            claude_manager.sync_after_restore()
        except Exception:
            pass

        # Уведомление в integrations.log о восстановлении
        try:
            import integrations_manager
            integrations_manager.logger.log("Система успешно восстановлена из зашифрованной резервной копии .hbak", "SUCCESS")
        except Exception:
            pass

        # Восстановление параметров автобэкапа из манифеста
        try:
            if "auto_backup_enabled" in manifest:
                update_backup_config("auto_backup_enabled", bool(manifest["auto_backup_enabled"]))
            if "backup_interval_hours" in manifest:
                update_backup_config("backup_interval_hours", int(manifest["backup_interval_hours"]))
        except Exception:
            pass

        msg = (
            f"✓ Бэкап успешно восстановлен!\n"
            f"Восстановлено файлов: {restored_count}\n"
            f"Дата создания бэкапа: {manifest.get('created_at', 'Неизвестно')}"
        )
        return True, msg, manifest

    except Exception as e:
        return False, f"Ошибка при восстановлении бэкапа: {e}", None


def list_backups_in_dir(target_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Возвращает список обнаруженных файлов .hbak с метаданными."""
    if target_dir is None:
        cfg = get_backup_config()
        target_dir = Path(cfg.get("backup_dir", DEFAULT_BACKUP_DIR))
    else:
        target_dir = Path(target_dir)

    if not target_dir.exists():
        return []

    results = []
    try:
        for f in target_dir.glob("*.hbak"):
            st = f.stat()
            size_kb = round(st.st_size / 1024, 1)
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
            results.append({
                "filename": f.name,
                "filepath": str(f),
                "size_kb": size_kb,
                "modified": mtime,
            })
    except Exception:
        pass

    results.sort(key=lambda x: x["modified"], reverse=True)
    return results


def check_and_run_auto_backup() -> tuple[bool, str]:
    """Проверяет необходимость выполнения периодического автобэкапа."""
    cfg = get_backup_config()
    if not cfg.get("auto_backup_enabled", False):
        return False, "Автобэкап выключен"

    password = load_backup_password()
    if not password:
        return False, "Пароль бэкапа не сохранен"

    interval_hours = cfg.get("backup_interval_hours", 12)
    interval_sec = interval_hours * 3600

    last_str = cfg.get("last_backup_time", "-")
    if last_str and last_str != "-":
        try:
            t_last = time.mktime(time.strptime(last_str, "%Y-%m-%d %H:%M:%S"))
            if time.time() - t_last < interval_sec:
                return False, "Интервал еще не истек"
        except Exception:
            pass

    ok, msg, _ = create_encrypted_backup(password, cfg.get("backup_dir"))
    return ok, msg


def wipe_all_data() -> tuple[bool, str]:
    """Полностью очищает все локальные данные, токены, профили и логи программы (Factory Reset)."""
    cleared_items = []
    errors = []

    # 1. Остановка сторожа WSL2
    try:
        subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "-u", WSL_USER, "bash", "-lic", "gemini-oauth guard stop"],
            capture_output=True, text=True, timeout=3.5,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000 if os.name == "nt" else 0
        )
    except Exception:
        pass

    # 2. Очистка WSL токенов, профилей и метаданных
    import token_meta_cache
    
    # 2.1 Gemini
    try:
        profiles_dir = WSL_GEMINI_DIR / "profiles"
        if profiles_dir.exists():
            import shutil
            shutil.rmtree(str(profiles_dir), ignore_errors=True)
            cleared_items.append("Gemini Аккаунты")

        cli_dir = WSL_GEMINI_DIR / "antigravity-cli"
        if cli_dir.exists():
            import os
            for filename in os.listdir(str(cli_dir)):
                fpath = cli_dir / filename
                if fpath.is_file():
                    try:
                        fpath.unlink(missing_ok=True)
                    except Exception:
                        pass
    except Exception as e:
        errors.append(f"Gemini: {e}")

    # 2.2 Claude
    try:
        if WSL_CLAUDE_DIR.exists():
            import shutil
            shutil.rmtree(str(WSL_CLAUDE_DIR), ignore_errors=True)
            cleared_items.append("Claude Аккаунты")
    except Exception as e:
        errors.append(f"Claude: {e}")

    # 2.3 Метаданные хранилища
    try:
        meta_file = token_meta_cache.META_FILE
        if meta_file.exists():
            meta_file.unlink(missing_ok=True)
    except Exception:
        pass

    # 3. Очистка локальных файлов Windows
    # 3.1 strategy_history.json
    try:
        strat_file = BASE_DIR / "strategy_history.json"
        if strat_file.exists():
            strat_file.unlink(missing_ok=True)
            cleared_items.append("История стратегии")
    except Exception as e:
        errors.append(f"Strategy: {e}")

    # 3.2 .env
    try:
        if ENV_FILE.exists():
            template = (
                "# Herdr Configuration (Reset to defaults)\n"
                "ANTHROPIC_API_KEY=\n"
                "GEMINI_API_KEY_1=\n"
                "BACKUP_PASSWORD=\n"
            )
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.write(template)
            cleared_items.append(".env (ключи и пароли)")
    except Exception as e:
        errors.append(f".env: {e}")

    # 3.3 proxies.json -> сброс к дефолтному базовому виду
    try:
        proxies_file = BASE_DIR / "proxies.json"
        default_proxies = [
            {
                "id": 1,
                "host": "127.0.0.1",
                "port": 1081,
                "label": "SOCKS5 :1081",
                "status": "unknown",
                "ip": "-",
                "country": "undefined",
                "latency_ms": None,
                "last_checked": None,
            }
        ]
        with open(proxies_file, "w", encoding="utf-8") as f:
            json.dump(default_proxies, f, indent=2, ensure_ascii=False)
        cleared_items.append("proxies.json (дефолт)")
    except Exception as e:
        errors.append(f"proxies.json: {e}")

    # 3.4 settings.json -> сброс к дефолтам
    try:
        default_settings = {
            "auto_proxy_failover": True,
            "auto_refresh_routes": True,
            "backup_dir": str(DEFAULT_BACKUP_DIR),
            "auto_backup_enabled": False,
            "backup_interval_hours": 12,
            "last_backup_time": "-",
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_settings, f, indent=2, ensure_ascii=False)
        cleared_items.append("settings.json (дефолт)")
    except Exception as e:
        errors.append(f"settings.json: {e}")

    # 3.5 Локальные лог-файлы
    for log_name in ["debug.log", "stdout.log", "stderr.log", "integrations.log", "extended.log"]:
        lp = BASE_DIR / log_name
        if lp.exists():
            try:
                lp.unlink(missing_ok=True)
            except Exception:
                pass

    if errors:
        return False, f"Очистка выполнена с замечаниями: {'; '.join(errors)}"
    return True, f"Все данные программы успешно очищены ({', '.join(cleared_items)})!"
