import os
import json
from pathlib import Path
import unittest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import token_vault_manager
import token_meta_cache
import backup_manager

# Мокаем пути, чтобы не сломать боевые токены при тестировании
class VaultTests(unittest.TestCase):
    def setUp(self):
        # Настраиваем тестовую директорию
        self.test_dir = Path("/tmp/herdr_test_vault")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        
        # Подменяем пути в менеджерах
        self.old_gemini_dir = backup_manager.WSL_GEMINI_DIR
        self.old_claude_dir = backup_manager.WSL_CLAUDE_DIR
        self.old_meta_file = token_meta_cache.META_FILE
        
        backup_manager.WSL_GEMINI_DIR = self.test_dir / ".gemini"
        backup_manager.WSL_CLAUDE_DIR = self.test_dir / ".claude"
        token_meta_cache.META_FILE = self.test_dir / "meta.json"
        
        # Создаем фальшивые токены
        self.claude_cred = backup_manager.WSL_CLAUDE_DIR / ".credentials.json"
        self.claude_cred.parent.mkdir(parents=True, exist_ok=True)
        self.claude_cred.write_text('{"token": "claude-dummy"}', encoding="utf-8")
        
        self.gemini_tok = backup_manager.WSL_GEMINI_DIR / "antigravity-cli" / "antigravity-oauth-token"
        self.gemini_tok.parent.mkdir(parents=True, exist_ok=True)
        self.gemini_tok.write_text('{"token": "gemini-dummy"}', encoding="utf-8")
        
        self.password = "test_super_password_123"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)
        # Восстанавливаем оригинальные пути
        backup_manager.WSL_GEMINI_DIR = self.old_gemini_dir
        backup_manager.WSL_CLAUDE_DIR = self.old_claude_dir
        token_meta_cache.META_FILE = self.old_meta_file

    def test_vault_flow(self):
        # Исходное состояние: не заблокировано
        self.assertFalse(token_vault_manager.is_vault_locked())
        
        # 1. LOCK
        ok, msg = token_vault_manager.lock_tokens(self.password)
        self.assertTrue(ok, msg)
        
        # Проверяем, что оригиналы исчезли, а .enc появились
        self.assertFalse(self.claude_cred.exists())
        self.assertTrue(Path(str(self.claude_cred) + ".enc").exists())
        
        self.assertFalse(self.gemini_tok.exists())
        self.assertTrue(Path(str(self.gemini_tok) + ".enc").exists())
        
        self.assertTrue(token_vault_manager.is_vault_locked())
        
        # 2. UNLOCK
        ok, msg = token_vault_manager.unlock_tokens(self.password)
        self.assertTrue(ok, msg)
        
        # Проверяем, что оригиналы вернулись, а .enc пропали
        self.assertTrue(self.claude_cred.exists())
        self.assertFalse(Path(str(self.claude_cred) + ".enc").exists())
        
        self.assertEqual(self.claude_cred.read_text("utf-8"), '{"token": "claude-dummy"}')
        self.assertFalse(token_vault_manager.is_vault_locked())

    def test_auto_unlock_context(self):
        # Сначала лочим
        token_vault_manager.lock_tokens(self.password)
        self.assertTrue(token_vault_manager.is_vault_locked())
        
        # Заходим в контекст, передав пароль принудительно для тестов (т.к. .env может быть пустым)
        with token_vault_manager.auto_unlock_context(password=self.password):
            # Внутри контекста всё должно быть раслочено
            self.assertFalse(token_vault_manager.is_vault_locked())
            self.assertTrue(self.claude_cred.exists())
            
        # По выходу - автоматически залочено снова
        self.assertTrue(token_vault_manager.is_vault_locked())
        self.assertFalse(self.claude_cred.exists())



    def test_global_metadata_update_trigger(self):
        import time
        from token_vault_manager import LAST_GLOBAL_METADATA_UPDATE
        
        # lock first
        token_vault_manager.lock_tokens(self.password)
        old_time = token_vault_manager.LAST_GLOBAL_METADATA_UPDATE
        
        time.sleep(0.1) # tiny sleep to ensure time difference
        with token_vault_manager.auto_unlock_context(password=self.password):
            pass # update should trigger
            
        new_time = token_vault_manager.LAST_GLOBAL_METADATA_UPDATE
        self.assertGreater(new_time, old_time)
