import unittest
import token_vault_manager


class TestTokenVaultManager(unittest.TestCase):
    def test_vault_is_unlocked(self):
        self.assertFalse(token_vault_manager.is_vault_locked())

    def test_watchdog_does_not_lock(self):
        token_vault_manager.start_vault_watchdog()
        self.assertFalse(token_vault_manager.is_vault_locked())


if __name__ == '__main__':
    unittest.main()
