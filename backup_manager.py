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
        try:
            p = Path(prefix)
            if p.exists():
                return p
        except OSError:
            pass
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


# ── Согласованный снимок базы SQLite (вместо копирования живого файла) ───────────
# Раньше бэкап клал в архив aionui-backend.db как обычный файл через \\wsl$, пока AionUi в него
# писал: без журнала -wal (свежие изменения живут там) и с риском прочитать файл «на середине записи».
# Такая копия могла оказаться несогласованной и при восстановлении ломала базу.
# Теперь снимок делает SQLite Online Backup API (читает и основной файл, и -wal, согласованно),
# снимок проверяется PRAGMA integrity_check, результат пишется в манифест; битый снимок в архив не попадает.
AIONUI_SNAPSHOT_NAME = ".herdr_snapshot.db"
AIONUI_LINUX_DIR: str | None = None   # None → ~/.aionui-web внутри WSL (тесты подменяют на временную папку)
SNAPSHOT_COUNT_TABLES = ("conversations", "messages")

_SNAPSHOT_CODE = r"""
import json, sqlite3, sys, time
from pathlib import Path
src = Path(sys.argv[1]).expanduser(); dst = Path(sys.argv[2]).expanduser()
info = {"method": "sqlite_backup_api", "source": str(src)}
try:
    if not src.exists():
        raise FileNotFoundError(f"нет файла {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        s.execute("select count(*) from sqlite_master").fetchone()
    except sqlite3.OperationalError:
        s = sqlite3.connect(str(src), timeout=30)
    d = sqlite3.connect(str(dst))
    t0 = time.time()
    s.backup(d, pages=256, sleep=0.02)
    d.close(); s.close()
    c = sqlite3.connect(str(dst))
    res = [r[0] for r in c.execute("PRAGMA integrity_check").fetchall()]
    info["integrity"] = "ok" if res == ["ok"] else "; ".join(map(str, res[:5]))
    counts = {}
    for t in TABLES:
        try:
            counts[t] = c.execute(f'select count(*) from "{t}"').fetchone()[0]
        except sqlite3.DatabaseError:
            pass
    info["counts"] = counts
    c.execute("PRAGMA journal_mode=DELETE")
    c.close()
    info["size_bytes"] = dst.stat().st_size
    info["elapsed_ms"] = int((time.time() - t0) * 1000)
except Exception as e:
    info["integrity"] = "error"
    info["error"] = f"{type(e).__name__}: {e}"
print(json.dumps(info, ensure_ascii=False))
sys.exit(0 if info.get("integrity") == "ok" else 2)
""".replace("TABLES", repr(SNAPSHOT_COUNT_TABLES))


def _parse_json_tail(out: str) -> dict[str, Any]:
    for line in reversed((out or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {"integrity": "error", "error": (out or "").strip()[-300:] or "нет вывода"}


def snapshot_aionui_db(timeout: float = 300.0) -> tuple[bytes | None, dict[str, Any]]:
    """Согласованный снимок ~/.aionui-web/aionui-backend.db (внутри WSL, Online Backup API).

    Возвращает (байты снимка или None, информация для манифеста). AionUi останавливать не нужно.
    """
    lin_dir = AIONUI_LINUX_DIR or "~/.aionui-web"
    src = f"{lin_dir}/aionui-backend.db"
    dst = f"{lin_dir}/db_backups/{AIONUI_SNAPSHOT_NAME}"
    ok, out = _wsl_python(_SNAPSHOT_CODE, timeout=timeout, args=[src, dst])
    info = _parse_json_tail(out)
    snap = WSL_AIONUI_DIR / "db_backups" / AIONUI_SNAPSHOT_NAME
    try:
        if ok and info.get("integrity") == "ok" and snap.exists():
            return snap.read_bytes(), info
        if ok and info.get("integrity") == "ok":
            info = {**info, "integrity": "error", "error": f"снимок не найден: {snap}"}
        return None, info
    finally:
        try:
            snap.unlink(missing_ok=True)
        except OSError:
            pass


def snapshot_local_sqlite(src: Path) -> tuple[bytes | None, dict[str, Any]]:
    """То же для локального файла (AionUi под Windows): снимок в этом же процессе."""
    import sqlite3
    import tempfile
    info: dict[str, Any] = {"method": "sqlite_backup_api", "source": str(src)}
    tmp = Path(tempfile.mkdtemp()) / "snap.db"
    try:
        s = sqlite3.connect(str(src), timeout=30)
        d = sqlite3.connect(str(tmp))
        s.backup(d, pages=256, sleep=0.02)
        d.close()
        s.close()
        ok, vinfo = validate_sqlite_bytes(tmp.read_bytes())
        info.update(vinfo)
        return (tmp.read_bytes() if ok else None), info
    except Exception as e:
        info.update({"integrity": "error", "error": f"{type(e).__name__}: {e}"})
        return None, info
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def validate_sqlite_bytes(content: bytes) -> tuple[bool, dict[str, Any]]:
    """Проверяет базу SQLite из архива до восстановления: integrity_check и число записей."""
    import sqlite3
    import tempfile
    tmpdir = Path(tempfile.mkdtemp())
    p = tmpdir / "check.db"
    info: dict[str, Any] = {}
    try:
        p.write_bytes(content)
        c = sqlite3.connect(str(p))
        try:
            res = [r[0] for r in c.execute("PRAGMA integrity_check").fetchall()]
            info["integrity"] = "ok" if res == ["ok"] else "; ".join(map(str, res[:5]))
            counts = {}
            for t in SNAPSHOT_COUNT_TABLES:
                try:
                    counts[t] = c.execute(f'select count(*) from "{t}"').fetchone()[0]
                except sqlite3.DatabaseError:
                    pass
            info["counts"] = counts
        finally:
            c.close()
    except Exception as e:
        info["integrity"] = "error"
        info["error"] = f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return info.get("integrity") == "ok", info


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
        ]
        for fn in win_files:
            fp = BASE_DIR / fn
            if fp.exists():
                zf.write(fp, arcname=f"win/{fn}")
                files_packed += 1

        # 1.2 Файлы узлов Type-B (Windows)
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

        # 1.3 Файлы узлов Type-B (WSL2)
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

            # Профили сессий Type-B: только токены и метаданные
            oauth_dir = WSL_CLAUDE_DIR / CLAUDE_OAUTH_PROFILES_DIRNAME
            if oauth_dir.exists():
                for prof_dir in sorted(p for p in oauth_dir.iterdir() if p.is_dir()):
                    for fn in CLAUDE_OAUTH_PROFILE_FILES:
                        fp = prof_dir / fn
                        if fp.is_file():
                            zf.write(fp, arcname=f"claude/wsl/{CLAUDE_OAUTH_PROFILES_DIRNAME}/{prof_dir.name}/{fn}")
                            files_packed += 1

        # 1.4 Файлы AionUi (WSL2: SQLite база данных, настройки расширений, манифест)
        aionui_db_info: dict[str, Any] = {"integrity": "skipped", "error": "папка AionUi в WSL не найдена"}
        if WSL_AIONUI_DIR.exists():
            # База — только согласованным снимком SQLite (никогда не копией живого файла)
            snap_bytes, aionui_db_info = snapshot_aionui_db()
            if snap_bytes is not None:
                zf.writestr("aionui/wsl/aionui-backend.db", snap_bytes)
                files_packed += 1
            for fn in ["extension-states.json", "extension-user-states.json"]:
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
        aionui_win_db_info: dict[str, Any] | None = None
        if WIN_AIONUI_DIR.exists():
            win_db = WIN_AIONUI_DIR / "aionui-backend.db"
            if win_db.exists():
                wb, aionui_win_db_info = snapshot_local_sqlite(win_db)
                if wb is not None:
                    zf.writestr("aionui/win/aionui-backend.db", wb)
                    files_packed += 1
            for fn in ["extension-states.json", "extension-user-states.json"]:
                fp = WIN_AIONUI_DIR / fn
                if fp.exists():
                    try:
                        zf.write(fp, arcname=f"aionui/win/{fn}")
                        files_packed += 1
                    except Exception:
                        pass

        # 1.6 Файлы сессий Type-A в WSL
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
            "aionui_db": aionui_db_info,
            "aionui_win_db": aionui_win_db_info,
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
        if aionui_db_info.get("integrity") == "ok":
            c = aionui_db_info.get("counts", {})
            msg += (f"\nБаза AionUi: согласованный снимок, integrity_check = ok "
                    f"(чатов: {c.get('conversations', '?')}, сообщений: {c.get('messages', '?')}).")
        elif aionui_db_info.get("integrity") != "skipped":
            msg += (f"\n⚠ База AionUi НЕ включена в бэкап: снимок не прошёл проверку "
                    f"({aionui_db_info.get('error') or aionui_db_info.get('integrity')}). "
                    f"Похоже, база повреждена — см. aionUi_helper/docs/REPAIR_AIONUI_DB.md.")
        return True, msg, str(out_path)
    except Exception as e:
        return False, f"Ошибка записи файла бэкапа: {e}", None


# ── Безопасная работа с живой базой AionUi ─────────────────────────────────────
# Раньше восстановление .hbak перезаписывало ~/.aionui-web/aionui-backend.db через \\wsl$
# прямо под работающим AionUi и оставляло старые -wal/-shm. Новый файл базы + чужой журнал WAL
# = «database disk image is malformed»; при следующем запуске aioncore отказывается открыть базу
# (BOOTSTRAP_DATABASE_CORRUPTION_REQUIRES_USER_CONFIRMATION) и уходит в цикл перезапусков.
# Инцидент 27.09.2026 00:53–08:58 МСК. Теперь: флаг обслуживания → остановка с проверкой →
# запись базы → удаление старых -wal/-shm → снятие флага (сторож поднимет AionUi сам).
AIONUI_HELPER_SCRIPTS = "/mnt/d/My files/aionUi_helper/scripts"
AIONUI_DB_MEMBER = "aionui/wsl/aionui-backend.db"


def _wsl_python(code: str, timeout: float = 60.0, args: list[str] | None = None) -> tuple[bool, str]:
    """Выполняет python3-код внутри WSL (на Windows) или локально."""
    cmd = ["python3", "-c", code, *(args or [])]
    if os.name == "nt":
        cmd = ["wsl.exe", "-d", WSL_DISTRO, "--"] + cmd
    try:
        kw: dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout,
                              "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000
        r = subprocess.run(cmd, **kw)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


def aionui_stop_for_maintenance(reason: str = "herdr-restore") -> tuple[bool, str]:
    """Ставит ~/.aionui-web/.maintenance и останавливает AionUi (aionui_maint.stop_aionui)."""
    code = (
        "import sys; sys.path.insert(0, %r); import aionui_maint as m; "
        "f = m.maint_flag(); f.parent.mkdir(parents=True, exist_ok=True); f.write_text(%r); "
        "sys.exit(0 if m.stop_aionui() else 3)"
    ) % (AIONUI_HELPER_SCRIPTS, reason)
    return _wsl_python(code, timeout=90.0)


def aionui_end_maintenance() -> tuple[bool, str]:
    """Снимает флаг обслуживания; встроенный aiWatcher сам запустит AionUi с чистым окружением."""
    code = ("import sys; sys.path.insert(0, %r); import aionui_maint as m; "
            "m.maint_flag().unlink(missing_ok=True)") % AIONUI_HELPER_SCRIPTS
    return _wsl_python(code, timeout=30.0)


def restore_encrypted_backup(
    backup_file_path: str | Path,
    password: str,
    restore_herdr_config: bool = True,
    restore_oauth_profiles: bool = True,
    restore_aionui_db: bool = True,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Расшифровывает и восстанавливает окружение из файла .hbak с поддержкой выборочного восстановления.
    
    Параметры:
      restore_herdr_config: восстанавливать настройки Herdr Center (win/*)
      restore_oauth_profiles: восстанавливать профили и токены Gemini и Claude (wsl/*, claude/*)
      restore_aionui_db: восстанавливать базу данных и состояние AionUi (aionui/*)
    
    Возвращает (success, message, manifest)
    """
    if not (restore_herdr_config or restore_oauth_profiles or restore_aionui_db):
        return False, "Не выбран ни один компонент для восстановления.", None

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

        aionui_note = ""
        with zipfile.ZipFile(zip_buf, "r") as zf:
            namelist = zf.namelist()
            if "manifest.json" in namelist:
                try:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                except Exception:
                    pass

            # База AionUi восстанавливается только при остановленном AionUi (если запрошено восстановление базы)
            skip_aionui_db = not restore_aionui_db
            aionui_stopped = False
            if restore_aionui_db and AIONUI_DB_MEMBER in namelist:
                db_ok, db_info = validate_sqlite_bytes(zf.read(AIONUI_DB_MEMBER))
                if not db_ok:
                    skip_aionui_db = True
                    aionui_note = (" База AionUi из архива НЕ восстановлена: копия повреждена "
                                   f"({db_info.get('error') or db_info.get('integrity')}). Текущая база не тронута.")
            if restore_aionui_db and AIONUI_DB_MEMBER in namelist and not skip_aionui_db:
                aionui_stopped, out = aionui_stop_for_maintenance()
                if not aionui_stopped:
                    skip_aionui_db = True
                    aionui_note = (" База AionUi НЕ восстановлена: не удалось безопасно остановить AionUi "
                                   "(выключите aiWatcher и повторите).")
                    aionui_end_maintenance()

            for name in namelist:
                if name == "manifest.json":
                    continue
                if name == AIONUI_DB_MEMBER and skip_aionui_db:
                    continue

                content = zf.read(name)

                if name.startswith("win/"):
                    if not restore_herdr_config:
                        continue
                    rel_name = name[4:]
                    dest_p = BASE_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("wsl/"):
                    if not restore_oauth_profiles:
                        continue
                    rel_name = name[4:]
                    dest_p = WSL_GEMINI_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("claude/win/"):
                    if not restore_oauth_profiles:
                        continue
                    rel_name = name[len("claude/win/"):]
                    dest_p = WIN_CLAUDE_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("claude/wsl/"):
                    if not restore_oauth_profiles:
                        continue
                    rel_name = name[len("claude/wsl/"):]
                    dest_p = WSL_CLAUDE_DIR / rel_name
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("aionui/wsl/"):
                    if not restore_aionui_db:
                        continue
                    rel_name = name[len("aionui/wsl/"):]
                    dest_p = WSL_AIONUI_DIR / rel_name
                    if name == AIONUI_DB_MEMBER:
                        # старый журнал WAL от другой базы нельзя оставлять рядом с новым файлом
                        for sfx in ("-wal", "-shm"):
                            try:
                                (WSL_AIONUI_DIR / f"aionui-backend.db{sfx}").unlink(missing_ok=True)
                            except OSError:
                                pass
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

                elif name.startswith("aionui/win/"):
                    if not restore_aionui_db:
                        continue
                    rel_name = name[len("aionui/win/"):]
                    dest_p = WIN_AIONUI_DIR / rel_name
                    if rel_name == "aionui-backend.db" and not validate_sqlite_bytes(content)[0]:
                        aionui_note += " База AionUi (Windows) из архива повреждена и НЕ восстановлена."
                        continue
                    if _safe_write_file(dest_p, content):
                        restored_count += 1

        if aionui_stopped:
            aionui_end_maintenance()
            aionui_note = "\n• AionUi был остановлен на время восстановления базы и будет запущен aiWatcher."
        elif not restore_aionui_db:
            aionui_note = "\n• База данных и чаты AionUi: не затрагивались (пропущено пользователем)."

        # Отказоустойчивая валидация и миграция истории стратегий после импорта
        if restore_herdr_config:
            try:
                import strategy_manager
                repaired_history = strategy_manager.load_history()
                strategy_manager.save_history(repaired_history)
            except Exception:
                pass

        # Синхронизация восстановленных настроек узлов Type-B и Killswitch
        if restore_herdr_config or restore_oauth_profiles:
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
        if restore_herdr_config:
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
            f"{aionui_note}"
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
    
    # 2.1 Профили Type-A
    try:
        profiles_dir = WSL_GEMINI_DIR / "profiles"
        if profiles_dir.exists():
            import shutil
            shutil.rmtree(str(profiles_dir), ignore_errors=True)
            cleared_items.append("Профили Type-A")

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
        errors.append(f"Type-A: {e}")

    # 2.2 Профили Type-B
    try:
        if WSL_CLAUDE_DIR.exists():
            import shutil
            shutil.rmtree(str(WSL_CLAUDE_DIR), ignore_errors=True)
            cleared_items.append("Профили Type-B")
    except Exception as e:
        errors.append(f"Type-B: {e}")

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
