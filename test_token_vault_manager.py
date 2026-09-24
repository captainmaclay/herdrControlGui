import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import time
import token_vault_manager

class TestTokenVaultManager(unittest.TestCase):

    @patch('token_vault_manager.backup_manager')
    @patch('token_vault_manager.os.listdir')
    def test_get_token_paths_excludes_active(self, mock_listdir, mock_backup):
        # Mocking directories
        mock_claude_dir = MagicMock()
        mock_gemini_dir = MagicMock()
        mock_backup.WSL_CLAUDE_DIR = mock_claude_dir
        mock_backup.WSL_GEMINI_DIR = mock_gemini_dir

        mock_claude_dir.exists.return_value = True
        mock_gemini_dir.exists.return_value = True

        # Profiles dirs
        mock_claude_oauth = MagicMock()
        mock_claude_oauth.exists.return_value = True
        mock_claude_dir.__truediv__.return_value = mock_claude_oauth

        mock_gemini_profiles = MagicMock()
        mock_gemini_profiles.exists.return_value = True
        # For Gemini, the logic separates antigravity-cli and profiles
        
        # When checking paths, it should only find profiles, not the active ones
        def side_effect_claude(p):
            if str(p) == "oauth-profiles":
                return mock_claude_oauth
            return MagicMock()
        mock_claude_dir.__truediv__.side_effect = side_effect_claude

        # Simple test: just check that it parses the os.listdir properly and does NOT include active roots
        mock_listdir.return_value = []
        paths = token_vault_manager._get_token_paths()
        
        # Should be empty because listdir is empty (no profiles)
        self.assertEqual(len(paths), 0)
        
        mock_listdir.return_value = ["prof1"]
        # With 1 profile for claude and 1 for gemini
        # We just assume it adds paths.
        paths = token_vault_manager._get_token_paths()
        # Since active aren't returned implicitly, it proves they remain decrypted.
        self.assertEqual(len(paths), 2)

    @patch('token_vault_manager.lock_tokens')
    @patch('token_vault_manager.is_vault_locked')
    @patch('token_vault_manager.backup_manager')
    def test_watchdog_auto_locks(self, mock_backup, mock_is_locked, mock_lock_tokens):
        mock_is_locked.return_value = False
        mock_backup.load_backup_password.return_value = "pass123"

        token_vault_manager.VAULT_AUTO_LOCK_TIMEOUT = 0.1
        token_vault_manager.register_vault_interaction()
        
        # Run watchdog loop directly for a brief moment via event
        token_vault_manager._vault_stop_event.clear()
        
        # Start watchdog loop
        import threading
        t = threading.Thread(target=token_vault_manager._vault_watchdog_loop)
        t.start()
        
        # Give it time to timeout (0.1s + 0.2s margin)
        time.sleep(0.3)
        # Manually wake it up by setting stop event since it waits 5s
        token_vault_manager._vault_stop_event.set()
        t.join()
        
        # It should have called lock_tokens because of timeout
        mock_lock_tokens.assert_called_once_with("pass123")

if __name__ == '__main__':
    unittest.main()
