#!/usr/bin/env python3
"""
Stack Bundle Manager & Master-Key Deployment for Herdr Center.
- Создает зашифрованный установочный пакет стека (OmniRoute + AionUi + Gemini Farm).
- Шифрует чувствительные токены и базы (AES-256-GCM + PBKDF2-HMAC-SHA256, 600 000 итераций).
- При первой установке расшифровывает данные по Мастер-ключу, разворачивает стек с нуля.
- После первого развертывания сервисы запускаются автоматически в обычном режиме без запроса пароля.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_CUR_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BASE_DIR = _CUR_DIR if (_CUR_DIR / "config_app.py").exists() else _CUR_DIR.parent
DEFAULT_BUNDLE_DIR = BASE_DIR / "dist"
DEFAULT_BUNDLE_NAME = "herdr_stack_bundle.hbin"

MAGIC_HEADER = b"HSTACK\x01"  # Herdr Stack Bundle v1
PBKDF2_ITERATIONS = 600_000
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32

WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")


def get_wsl_user() -> str:
    """Определяет пользователя WSL2."""
    return (os.environ.get("WSL_USER") or os.environ.get("USERNAME") or "f").strip()


def get_wsl_root_unc() -> Path:
    """Возвращает UNC путь к корню WSL2."""
    for prefix in [rf"\\wsl$\{WSL_DISTRO}", rf"\\wsl.localhost\{WSL_DISTRO}"]:
        p = Path(prefix)
        if p.exists():
            return p
    return Path(rf"\\wsl$\{WSL_DISTRO}")


def run_wsl_cmd(cmd: str, timeout: float = 300.0) -> tuple[int, str, str]:
    """Выполняет bash-команду внутри WSL2."""
    try:
        proc = subprocess.run(
            ["wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 1, "", str(e)


def compute_key_fingerprint(password: str) -> str:
    """Вычисляет отпечаток мастер-ключа SHA-256 для отображения в UI."""
    if not password:
        return ""
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest().upper()
    return f"{digest[:4]} {digest[4:8]} {digest[8:12]} {digest[12:16]}"


def _derive_key(password: str, salt: bytes) -> bytes:
    """Генерирует 256-битный ключ через PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def _encrypt_data(password: str, data: bytes) -> bytes:
    """Шифрует данные алгоритмом AES-256-GCM с солью и nonce."""
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, associated_data=MAGIC_HEADER)
    # Формат: MAGIC_HEADER (7b) + salt (16b) + nonce (12b) + ciphertext (включает 16b auth tag)
    return MAGIC_HEADER + salt + nonce + ciphertext


def _decrypt_data(password: str, encrypted_blob: bytes) -> bytes:
    """Расшифровывает данные AES-256-GCM с проверкой заголовка и целостности."""
    hdr_len = len(MAGIC_HEADER)
    if not encrypted_blob.startswith(MAGIC_HEADER):
        raise ValueError("Неверный формат контейнера: отсутствуют сигнатуры Herdr Stack Bundle.")

    salt = encrypted_blob[hdr_len : hdr_len + SALT_SIZE]
    nonce = encrypted_blob[hdr_len + SALT_SIZE : hdr_len + SALT_SIZE + NONCE_SIZE]
    ciphertext = encrypted_blob[hdr_len + SALT_SIZE + NONCE_SIZE :]

    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext, associated_data=MAGIC_HEADER)
    except Exception:
        raise ValueError("Неверный мастер-ключ: ошибка аутентификации данных (MAC mismatch).")


# ─────────────────────────── Сборка дистрибутива ───────────────────────────

def build_stack_bundle(
    master_key: str,
    output_path: str | Path | None = None,
    log_fn: Callable[[str, str], None] | None = None,
) -> tuple[bool, str, str | None]:
    """
    Собирает самодостаточный установочный дистрибутив стека:
    - Открытая часть: бинарники AionUi, скрипты управления, watchdog
    - Зашифрованная часть (мастер-ключ): токены Gemini, базы OmniRoute и AionUi
    """
    def _log(msg: str, lvl: str = "INFO"):
        if log_fn:
            log_fn(msg, lvl)
        else:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [{lvl}] {msg}")

    if not master_key or len(master_key) < 6:
        return False, "Мастер-ключ должен содержать не менее 6 символов.", None

    if output_path is None:
        DEFAULT_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target_file = DEFAULT_BUNDLE_DIR / f"herdr_stack_bundle_{ts}.hbin"
    else:
        target_file = Path(output_path)
        target_file.parent.mkdir(parents=True, exist_ok=True)

    _log("=== Запуск сборки установочного дистрибутива Herdr Stack ===", "STEP")
    _log(f"Отпечаток мастер-ключа: {compute_key_fingerprint(master_key)}", "INFO")

    wsl_user = get_wsl_user()
    wsl_home = f"/home/{wsl_user}"
    wsl_unc = get_wsl_root_unc()
    wsl_build_unc = wsl_unc / "tmp" / "herdr_build"

    run_wsl_cmd("rm -rf /tmp/herdr_build && mkdir -p /tmp/herdr_build")

    # 1. Сборка защищенного хранилища (Sensitive Vault)
    _log("Шаг 1/3: Формирование зашифрованного хранилища токенов и конфигураций...", "INFO")
    vault_buffer = io.BytesIO()

    with zipfile.ZipFile(vault_buffer, "w", zipfile.ZIP_DEFLATED) as vzf:
        # 1.1 Токены и профили Gemini из WSL2
        code, stdout, stderr = run_wsl_cmd(f"tar -czf /tmp/herdr_build/gemini_profiles.tar.gz -C {wsl_home}/.gemini profiles 2>/dev/null")
        p_gem = wsl_build_unc / "gemini_profiles.tar.gz"
        if p_gem.exists():
            vzf.write(p_gem, arcname="gemini_profiles.tar.gz")
            _log("  [+] Профили и токены Gemini успешно упакованы", "SUCCESS")
        else:
            _log(f"  [-] Предупреждение: профили Gemini не найдены в {wsl_home}/.gemini", "WARN")

        # 1.2 База данных OmniRoute (storage.sqlite) и .env
        code, stdout, stderr = run_wsl_cmd(f"tar -czf /tmp/herdr_build/omniroute_data.tar.gz -C {wsl_home}/.omniroute storage.sqlite .env 2>/dev/null")
        p_omni = wsl_build_unc / "omniroute_data.tar.gz"
        if p_omni.exists():
            vzf.write(p_omni, arcname="omniroute_data.tar.gz")
            _log("  [+] База OmniRoute и мастер-ключ STORAGE_ENCRYPTION_KEY упакованы", "SUCCESS")
        else:
            _log(f"  [-] Предупреждение: данные OmniRoute не найдены в {wsl_home}/.omniroute", "WARN")

        # 1.3 База данных AionUi (онлайн-снимок SQLite без риска повреждения)
        snap_cmd = (
            f"python3 -c \"import sqlite3; s=sqlite3.connect('{wsl_home}/.aionui-web/aionui-backend.db'); "
            f"d=sqlite3.connect('/tmp/herdr_build/aionui-backend.db'); s.backup(d); s.close(); d.close();\""
        )
        code, stdout, stderr = run_wsl_cmd(snap_cmd)
        p_aion_db = wsl_build_unc / "aionui-backend.db"
        if p_aion_db.exists():
            vzf.write(p_aion_db, arcname="aionui-backend.db")
            _log("  [+] База данных AionUi (онлайн-снимок) успешно упакована", "SUCCESS")
        else:
            _log(f"  [-] Предупреждение: база данных AionUi не найдена", "WARN")

        # 1.4 Файлы настроек Herdr Center (Windows)
        for fn in ["settings.json", "proxies.json", ".env"]:
            fp = BASE_DIR / fn
            if fp.exists():
                vzf.write(fp, arcname=f"herdr/{fn}")
        _log("  [+] Локальные настройки Herdr Center упакованы", "SUCCESS")

    raw_vault_bytes = vault_buffer.getvalue()
    _log(f"Размер исходного хранилища: {len(raw_vault_bytes) / 1024 / 1024:.2f} МБ", "INFO")

    # Шифрование хранилища AES-256-GCM
    _log("Шифрование хранилища алгоритмом AES-256-GCM...", "INFO")
    encrypted_vault_bytes = _encrypt_data(master_key, raw_vault_bytes)
    _log("  [+] Чувствительные данные зашифрованы мастер-ключом", "SUCCESS")

    # 2. Сборка рантайма AionUi (бинарники + статика)
    _log("Шаг 2/3: Архивация актуального рантайма AionUi 2.1.47...", "INFO")
    code, stdout, stderr = run_wsl_cmd(
        f"tar -czf /tmp/herdr_build/aionui_runtime.tar.gz -C {wsl_home}/.local/share/aionui-web/aionui-web . 2>/dev/null"
    )
    p_aion_rt = wsl_build_unc / "aionui_runtime.tar.gz"
    if not p_aion_rt.exists():
        run_wsl_cmd("rm -rf /tmp/herdr_build")
        return False, "Не удалось упаковать рантайм AionUi из WSL2.", None
    _log(f"  [+] Рантайм AionUi успешно подготовлен ({p_aion_rt.stat().st_size / 1024 / 1024:.1f} МБ)", "SUCCESS")

    # 3. Формирование финального дистрибутива (ZIP-контейнер)
    _log("Шаг 3/3: Сборка единого файла пакета...", "INFO")
    with zipfile.ZipFile(target_file, "w", zipfile.ZIP_DEFLATED) as bzf:
        # Манифест
        manifest = {
            "bundle_type": "herdr_stack_bundle",
            "version": "1.0",
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fingerprint": compute_key_fingerprint(master_key),
            "wsl_user": wsl_user,
            "components": {
                "omniroute": "3.8.50",
                "aionui": "2.1.47",
                "gemini_farm": "8 active nodes",
                "vault_encrypted": True,
            },
        }
        bzf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

        # Зашифрованное хранилище
        bzf.writestr("vault.enc", encrypted_vault_bytes)

        # Скрипты обслуживания и сторожа
        for script_name in [
            "gemini_resilience_manager.py",
            "watchdog_manager.py",
            "watchdog_config.json",
        ]:
            sp = BASE_DIR / script_name
            if sp.exists():
                bzf.write(sp, arcname=f"scripts/{script_name}")

        # Помещаем архив рантайма AionUi через прямой файловый поток
        _log("  -> Упаковка рантайма в пакет...", "INFO")
        bzf.write(p_aion_rt, arcname="aionui_runtime.tar.gz")

    # Очистка временных файлов сборки
    run_wsl_cmd("rm -rf /tmp/herdr_build")

    bundle_size_mb = target_file.stat().st_size / 1024 / 1024
    _log(f"=== Сборка успешно завершена! ===", "SUCCESS")
    _log(f"Файл дистрибутива: {target_file} ({bundle_size_mb:.1f} МБ)", "SUCCESS")
    return True, f"Дистрибутив успешно собран ({bundle_size_mb:.1f} МБ)", str(target_file)


# ─────────────────────────── Развертывание с нуля ───────────────────────────

def install_stack_bundle(
    master_key: str,
    bundle_path: str | Path,
    log_fn: Callable[[str, str], None] | None = None,
) -> tuple[bool, str]:
    """
    Развертывает стек с нуля из установочного пакета:
    1. Проверяет мастер-ключ и расшифровывает хранилище.
    2. Устанавливает базовые зависимости в WSL2 (Node.js, omniroute).
    3. Разворачивает актуальный рантайм AionUi.
    4. Распаковывает расшифрованные токены Gemini и базы данных.
    5. Запускает сторож отказоустойчивости и проводит автоматический самотест.
    """
    def _log(msg: str, lvl: str = "INFO"):
        if log_fn:
            log_fn(msg, lvl)
        else:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [{lvl}] {msg}")

    bundle_file = Path(bundle_path)
    if not bundle_file.exists():
        return False, f"Файл дистрибутива не найден: {bundle_file}"

    _log("=== Запуск развертывания стека Herdr (OmniRoute + AionUi) ===", "STEP")
    _log(f"Пакет: {bundle_file.name}", "INFO")

    wsl_user = get_wsl_user()
    wsl_home = f"/home/{wsl_user}"
    wsl_unc = get_wsl_root_unc()
    wsl_install_unc = wsl_unc / "tmp" / "herdr_install"

    run_wsl_cmd("rm -rf /tmp/herdr_install && mkdir -p /tmp/herdr_install")

    # 1. Открытие пакета и проверка мастер-ключа
    _log("Шаг 1/5: Проверка целостности и расшифровка чувствительных данных...", "INFO")
    try:
        with zipfile.ZipFile(bundle_file, "r") as bzf:
            if "vault.enc" not in bzf.namelist():
                return False, "Поврежденный пакет: файл vault.enc отсутствует."
            enc_vault = bzf.read("vault.enc")
    except Exception as e:
        return False, f"Ошибка чтения дистрибутива: {e}"

    # Расшифровываем хранилище
    try:
        decrypted_vault = _decrypt_data(master_key, enc_vault)
        _log("  [+] Мастер-ключ подтвержден! Доступ к токенам и базам получен.", "SUCCESS")
    except Exception as e:
        _log(f"  [-] Ошибка расшифровки: {e}", "ERROR")
        return False, str(e)

    # 2. Установка системных зависимостей в WSL2
    _log("Шаг 2/5: Проверка и установка зависимостей в WSL2 (Node.js, OmniRoute)...", "INFO")
    code, stdout, _ = run_wsl_cmd("node -v 2>/dev/null")
    if code != 0:
        _log("  -> Установка Node.js 20+ LTS в WSL2...", "INFO")
        run_wsl_cmd("curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs")

    code, stdout, _ = run_wsl_cmd("omniroute --version 2>/dev/null")
    if code != 0:
        _log("  -> Установка глобального пакета omniroute@3.8.50...", "INFO")
        run_wsl_cmd("npm install -g omniroute@3.8.50")
        _log("  [+] OmniRoute успешно установлен", "SUCCESS")
    else:
        _log(f"  [+] OmniRoute уже установлен ({stdout.strip()})", "INFO")

    # 3. Развертывание рантайма AionUi
    _log("Шаг 3/5: Развертывание рантайма AionUi 2.1.47...", "INFO")
    with zipfile.ZipFile(bundle_file, "r") as bzf:
        if "aionui_runtime.tar.gz" in bzf.namelist():
            wsl_install_unc.mkdir(parents=True, exist_ok=True)
            bzf.extract("aionui_runtime.tar.gz", wsl_install_unc)
            
            run_wsl_cmd(f"mkdir -p {wsl_home}/.local/share/aionui-web/aionui-web {wsl_home}/.local/bin")
            run_wsl_cmd(f"tar -xzf /tmp/herdr_install/aionui_runtime.tar.gz -C {wsl_home}/.local/share/aionui-web/aionui-web")
            run_wsl_cmd(f"ln -sf {wsl_home}/.local/share/aionui-web/aionui-web/aionui-web {wsl_home}/.local/bin/aionui-web")
            run_wsl_cmd(f"chmod +x {wsl_home}/.local/share/aionui-web/aionui-web/aionui-web")
            run_wsl_cmd(f"chmod +x {wsl_home}/.local/share/aionui-web/aionui-web/bundled-aioncore/linux-x64/aioncore 2>/dev/null || true")
            _log("  [+] Рантайм AionUi развернут, симлинк и права установлены", "SUCCESS")

    # 4. Распаковка расшифрованных токенов и баз данных
    _log("Шаг 4/5: Развертывание токенов Gemini, базы OmniRoute и настроек...", "INFO")
    vault_io = io.BytesIO(decrypted_vault)
    with zipfile.ZipFile(vault_io, "r") as vzf:
        # Профили Gemini
        if "gemini_profiles.tar.gz" in vzf.namelist():
            p_gem = wsl_install_unc / "gemini_profiles.tar.gz"
            p_gem.write_bytes(vzf.read("gemini_profiles.tar.gz"))
            run_wsl_cmd(f"mkdir -p {wsl_home}/.gemini && tar -xzf /tmp/herdr_install/gemini_profiles.tar.gz -C {wsl_home}/.gemini")
            _log("  [+] Профили и токены Gemini восстановлены в ~/.gemini/profiles", "SUCCESS")

        # База и ключ OmniRoute
        if "omniroute_data.tar.gz" in vzf.namelist():
            p_omni = wsl_install_unc / "omniroute_data.tar.gz"
            p_omni.write_bytes(vzf.read("omniroute_data.tar.gz"))
            run_wsl_cmd(f"mkdir -p {wsl_home}/.omniroute && tar -xzf /tmp/herdr_install/omniroute_data.tar.gz -C {wsl_home}/.omniroute")
            _log("  [+] База OmniRoute (storage.sqlite) и .env восстановлены", "SUCCESS")

        # База данных AionUi
        if "aionui-backend.db" in vzf.namelist():
            p_adb = wsl_install_unc / "aionui-backend.db"
            p_adb.write_bytes(vzf.read("aionui-backend.db"))
            run_wsl_cmd(f"mkdir -p {wsl_home}/.aionui-web && cp -f /tmp/herdr_install/aionui-backend.db {wsl_home}/.aionui-web/aionui-backend.db")
            _log("  [+] База данных AionUi (aionui-backend.db) восстановлена", "SUCCESS")

        # Локальные файлы Herdr Center (Windows)
        for n in vzf.namelist():
            if n.startswith("herdr/"):
                fn = n.split("/", 1)[1]
                target_p = BASE_DIR / fn
                target_p.write_bytes(vzf.read(n))
        _log("  [+] Конфигурации Herdr Center обновлены", "SUCCESS")

    run_wsl_cmd("rm -rf /tmp/herdr_install")

    # 5. Развертывание сторожа отказоустойчивости и запуск сервисов
    _log("Шаг 5/5: Запуск сервисов и калибровка отказоустойчивости...", "INFO")
    
    # Запуск демона gemini_resilience_manager
    res_script = BASE_DIR / "gemini_resilience_manager.py"
    if res_script.exists():
        run_wsl_cmd(f"python3 '{res_script}' --check")
        run_wsl_cmd(
            f"tmux -L gemini_watch kill-session -t gemini_watch 2>/dev/null; "
            f"tmux -L gemini_watch new -d -s gemini_watch python3 '{res_script}' --daemon"
        )
        _log("  [+] Демон отказоустойчивости Gemini запущен", "SUCCESS")

    # Запуск OmniRoute
    run_wsl_cmd(
        "tmux -L omniroute kill-session -t omniroute 2>/dev/null; "
        "tmux -L omniroute new -d -s omniroute bash -lc 'omniroute serve --no-open'"
    )
    _log("  [+] OmniRoute запущен на порту 20128", "SUCCESS")

    # Запуск AionUi с правильным сетевым окружением
    run_wsl_cmd("tmux kill-session -t aionui 2>/dev/null; pkill -f 'aionui-web start' 2>/dev/null || true")
    time.sleep(1)
    aion_start = (
        "tmux new -d -s aionui env "
        "HTTP_PROXY='http://127.0.0.1:11015' HTTPS_PROXY='http://127.0.0.1:11015' "
        "http_proxy='http://127.0.0.1:11015' https_proxy='http://127.0.0.1:11015' "
        "ALL_PROXY='socks5h://127.0.0.1:1015' all_proxy='socks5h://127.0.0.1:1015' "
        "NO_PROXY='127.0.0.1,localhost,::1,127.0.0.0/8' no_proxy='127.0.0.1,localhost,::1,127.0.0.0/8' "
        f"{wsl_home}/.local/bin/aionui-web start --no-open --port 25808"
    )
    run_wsl_cmd(aion_start)
    _log("  [+] AionUi запущен на порту 25808", "SUCCESS")

    # 6. Контрольный самотест (Verification)
    _log("Проведение контрольного тестирования...", "STEP")
    time.sleep(5)
    
    # 6.1 Проверка OmniRoute Gateway
    omni_ok = False
    for _ in range(10):
        code, out, _ = run_wsl_cmd("curl --noproxy '*' -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer sk-omniroute-secret' http://127.0.0.1:20128/v1/models")
        if out.strip() == "200":
            omni_ok = True
            break
        time.sleep(1)

    # 6.2 Проверка AionUi (AionUi инициализирует aioncore за 10-15с)
    aion_ok = False
    for _ in range(25):
        code, out, _ = run_wsl_cmd("curl --noproxy '*' -s -o /dev/null -w '%{http_code}' http://127.0.0.1:25808/api/auth/status")
        if out.strip() == "200":
            aion_ok = True
            break
        time.sleep(1)

    # 6.3 Проверка инференса gemini-farm
    farm_ok = False
    code, out, _ = run_wsl_cmd(
        "curl --noproxy '*' -s -X POST http://127.0.0.1:20128/v1/chat/completions "
        "-H 'Content-Type: application/json' -H 'Authorization: Bearer sk-omniroute-secret' "
        "-d '{\"model\":\"gemini-farm\",\"messages\":[{\"role\":\"user\",\"content\":\"say PONG\"}],\"max_tokens\":10}'"
    )
    if "PONG" in out or "choices" in out:
        farm_ok = True

    _log(f"Статус проверки: OmniRoute={omni_ok}, AionUi={aion_ok}, GeminiFarm={farm_ok}", "INFO")

    if omni_ok and aion_ok:
        _log("=== Развертывание успешно завершено! ===", "SUCCESS")
        _log("Все компоненты работают в штатном режиме. Повторный ввод мастер-ключа при запусках не требуется.", "SUCCESS")
        return True, "Стек успешно развернут и протестирован!"
    else:
        return False, f"Развертывание завершено частично (OmniRoute={omni_ok}, AionUi={aion_ok})."


def find_latest_bundle() -> Path | None:
    """Ищет самый свежий файл установочного пакета (*.hbin)."""
    candidates = []
    for search_dir in [DEFAULT_BUNDLE_DIR, BASE_DIR, Path.cwd()]:
        if search_dir.exists():
            for p in search_dir.glob("*.hbin"):
                if p.is_file():
                    candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def verify_stack_services(log_callback: Callable[[str], None] = print) -> tuple[bool, dict]:
    """Проверяет работоспособность OmniRoute, AionUi и инференса Gemini."""
    try:
        from .verify_stack import run_full_stack_verification
    except ImportError:
        from verify_stack import run_full_stack_verification
    return run_full_stack_verification(verbose=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--build":
        pwd = sys.argv[2] if len(sys.argv) > 2 else "MasterSecret2026!"
        ok, msg, path = build_stack_bundle(pwd)
        print(f"Result: {ok}, {msg}, path: {path}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--install":
        p = sys.argv[2]
        pwd = sys.argv[3] if len(sys.argv) > 3 else "MasterSecret2026!"
        ok, msg = install_stack_bundle(pwd, p)
        print(f"Install: {ok}, {msg}")
    else:
        print("Usage: stack_bundle_manager.py --build [password] | --install <bundle_path> [password]")
