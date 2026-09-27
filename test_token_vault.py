import unittest
from pathlib import Path
import token_vault_manager


class VaultDisabledTests(unittest.TestCase):
    def test_vault_is_always_unlocked(self):
        """Шифрование отключено — хранилище всегда разблокировано."""
        self.assertFalse(token_vault_manager.is_vault_locked())

    def test_lock_and_unlock_are_safe_noops(self):
        """lock_tokens и unlock_tokens работают как безопасные no-op."""
        ok_lock, msg_lock = token_vault_manager.lock_tokens("dummy_password")
        self.assertTrue(ok_lock)
        self.assertFalse(token_vault_manager.is_vault_locked())

        ok_unlock, msg_unlock = token_vault_manager.unlock_tokens("dummy_password")
        self.assertTrue(ok_unlock)
        self.assertFalse(token_vault_manager.is_vault_locked())

    def test_auto_unlock_context_is_noop(self):
        """auto_unlock_context выполняется без блокировок и исключений."""
        with token_vault_manager.auto_unlock_context():
            self.assertFalse(token_vault_manager.is_vault_locked())


if __name__ == "__main__":
    unittest.main()
