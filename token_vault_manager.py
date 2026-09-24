import os
import json
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import backup_manager
import gemini_manager
import claude_oauth_manager

MAGIC_HEADER_TOKEN = b"HRDRTK01"

def _get_token_paths() -> list[Path]:
    """Возвращает список всех потенциальных путей к токенам (Claude и Gemini),
    ИСКЛЮЧАЯ активные аккаунты, чтобы они всегда оставались расшифрованными."""
    paths = []

    # Claude tokens (Только из профилей OAuth - Vault)
    base_claude = backup_manager.WSL_CLAUDE_DIR
    if base_claude.exists():
        profiles_dir = base_claude / "oauth-profiles"
        if profiles_dir.exists():
            for p_name in os.listdir(profiles_dir):
                p_dir = profiles_dir / p_name
                if p_dir.is_dir():
                    pt = p_dir / ".credentials.json"
                    paths.append(pt)

    # Gemini tokens (Только из профилей - Vault)
    base_gemini = backup_manager.WSL_GEMINI_DIR
    if base_gemini.exists():
        profiles_dir = base_gemini / "profiles"
        if profiles_dir.exists():
            for p_name in os.listdir(profiles_dir):
                p_dir = profiles_dir / p_name
                if p_dir.is_dir():
                    pt = p_dir / "antigravity-oauth-token"
                    paths.append(pt)

    return paths

def is_vault_locked() -> bool:
    """Проверяет, заблокированы ли токены (есть ли зашифрованные файлы и отсутствуют ли расшифрованные)."""
    paths = _get_token_paths()
    has_encrypted = False
    
    for p in paths:
        enc_p = Path(str(p) + ".enc")
        if enc_p.exists():
            has_encrypted = True
        if p.exists() and not str(p).endswith(".enc"):
            # Если есть хотя бы один незашифрованный файл - считаем что хранилище частично или полностью открыто
            return False
            
    return has_encrypted

def lock_tokens(password: str) -> tuple[bool, str]:
    if not password:
        return False, "Пароль не может быть пустым."
        
    paths = _get_token_paths()
    if not paths:
        return True, "Нет токенов для шифрования."
        
    salt = os.urandom(16)
    key = backup_manager._derive_key(password, salt)
    aesgcm = AESGCM(key)
    
    success_count = 0
    enc_paths = []
    
    try:
        for p in paths:
            if not p.exists():
                continue
                
            raw_data = p.read_bytes()
            nonce = os.urandom(12)
            ciphertext = aesgcm.encrypt(nonce, raw_data, associated_data=MAGIC_HEADER_TOKEN)
            
            enc_p = Path(str(p) + ".enc")
            
            # Сохраняем salt + nonce + ciphertext
            final_data = salt + nonce + ciphertext
            with open(enc_p, "wb") as f:
                f.write(final_data)
                
            enc_paths.append((p, enc_p))
            success_count += 1
            
        # Удаляем оригиналы только если всё успешно зашифровалось
        for p, enc_p in enc_paths:
            p.unlink(missing_ok=True)
            
        return True, f"Зашифровано файлов: {success_count}."
        
    except Exception as e:
        return False, f"Ошибка при блокировке: {e}"

def unlock_tokens(password: str) -> tuple[bool, str]:
    try:
        register_vault_interaction()
    except Exception:
        pass
    if not password:
        return False, "Пароль не может быть пустым."
        
    paths = _get_token_paths()
    if not paths:
        return True, "Нет токенов для расшифровки."
        
    success_count = 0
    dec_paths = []
    
    try:
        for p in paths:
            enc_p = Path(str(p) + ".enc")
            if not enc_p.exists():
                continue
                
            data = enc_p.read_bytes()
            if len(data) < 28:
                continue # Слишком короткий файл
                
            salt = data[:16]
            nonce = data[16:28]
            ciphertext = data[28:]
            
            key = backup_manager._derive_key(password, salt)
            aesgcm = AESGCM(key)
            
            try:
                raw_data = aesgcm.decrypt(nonce, ciphertext, associated_data=MAGIC_HEADER_TOKEN)
            except Exception:
                return False, "Неверный пароль или данные повреждены."
                
            with open(p, "wb") as f:
                f.write(raw_data)
                
            dec_paths.append(enc_p)
            success_count += 1
            
        # Удаляем зашифрованные версии после успешной расшифровки
        for enc_p in dec_paths:
            enc_p.unlink(missing_ok=True)
            
        return True, f"Расшифровано файлов: {success_count}."
        
    except Exception as e:
        return False, f"Ошибка при разблокировке: {e}"


import time
LAST_GLOBAL_METADATA_UPDATE = 0.0

def update_all_metadata():
    """Обновляет кеш метаданных с открытых токенов и сбрасывает таймер."""
    global LAST_GLOBAL_METADATA_UPDATE
    paths = _get_token_paths()
    if backup_manager.WSL_CLAUDE_DIR.exists():
        paths.append(backup_manager.WSL_CLAUDE_DIR / ".credentials.json")
    if backup_manager.WSL_GEMINI_DIR.exists():
        paths.append(backup_manager.WSL_GEMINI_DIR / "antigravity-cli" / "antigravity-oauth-token")

    for p in paths:
        if p.exists():
            if ".gemini" in str(p):
                gemini_manager.get_token_info(p, is_active=(p.parent.name == "antigravity-cli"))
            elif ".claude" in str(p):
                claude_oauth_manager.get_credentials_info(p)
    LAST_GLOBAL_METADATA_UPDATE = time.time()

from contextlib import contextmanager

@contextmanager
def auto_unlock_context(password: str = None):
    """Контекстный менеджер для временной разблокировки токенов при манипуляциях."""
    if password is None:
        password = backup_manager.load_backup_password()

    was_locked = is_vault_locked()
    if was_locked and password:
        unlock_tokens(password)

    try:
        update_all_metadata()
        yield
    finally:
        if was_locked and password:
            lock_tokens(password)

def ensure_locked():
    """Следит за тем, чтобы 99% времени токены оставались зашифрованными."""
    pw = backup_manager.load_backup_password()
    if pw and not is_vault_locked():
        lock_tokens(pw)

import threading

_vault_watchdog_thread = None
_vault_stop_event = threading.Event()
_last_interaction_time = time.time()
VAULT_AUTO_LOCK_TIMEOUT = 10.0

def register_vault_interaction():
    global _last_interaction_time
    _last_interaction_time = time.time()

def _vault_watchdog_loop():
    while not _vault_stop_event.is_set():
        _vault_stop_event.wait(5.0)
        if time.time() - _last_interaction_time >= VAULT_AUTO_LOCK_TIMEOUT:
            if not is_vault_locked():
                pw = backup_manager.load_backup_password()
                if pw:
                    lock_tokens(pw)

def start_vault_watchdog():
    global _vault_watchdog_thread
    if _vault_watchdog_thread is None or not _vault_watchdog_thread.is_alive():
        _vault_stop_event.clear()
        register_vault_interaction()
        _vault_watchdog_thread = threading.Thread(target=_vault_watchdog_loop, daemon=True)
        _vault_watchdog_thread.start()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CLI для управления Сейфом Токенов")
    parser.add_argument("--lock", type=str, help="Заблокировать токены (пароль)")
    parser.add_argument("--unlock", type=str, help="Разблокировать токены (пароль)")

    args = parser.parse_args()
    if args.lock:
        ok, msg = lock_tokens(args.lock)
        print("SUCCESS" if ok else "ERROR", msg)
    elif args.unlock:
        ok, msg = unlock_tokens(args.unlock)
        print("SUCCESS" if ok else "ERROR", msg)
    else:
        print("Статус хранилища:", "ЗАБЛОКИРОВАНО" if is_vault_locked() else "РАЗБЛОКИРОВАНО")
