import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os
import sys

# Добавляем путь для импортов
sys.path.append(str(Path("/mnt/d/My files/herdrControlGui")))

import claude_oauth_manager

class TestClaudeOauthManager(unittest.TestCase):

    @patch("claude_oauth_manager.shutil.copy2")
    @patch("claude_oauth_manager._fix_wsl_permissions")
    @patch("claude_oauth_manager.get_credentials_info")
    @patch("claude_oauth_manager.sync_active_back_to_profile")
    @patch("claude_oauth_manager._profile_account")
    @patch("claude_oauth_manager._read_json")
    @patch("claude_oauth_manager._write_json")
    @patch("claude_oauth_manager.ACTIVE_CREDENTIALS_FILE")
    @patch("claude_oauth_manager.PROFILES_DIR")
    @patch("claude_oauth_manager.CLAUDE_DIR")
    def test_switch_profile_applies_permissions_and_writes_active_json(
        self, mock_claude_dir, mock_prof_dir, mock_active_cred,
        mock_write_json, mock_read_json, mock_profile_acct,
        mock_sync, mock_get_cred, mock_fix_perms, mock_copy2
    ):
        mock_src_cred = MagicMock()
        mock_src_cred.exists.return_value = True
        mock_src_dir = MagicMock()
        mock_src_dir.__truediv__.return_value = mock_src_cred
        mock_prof_dir.__truediv__.return_value = mock_src_dir
        
        mock_active_cred.exists.return_value = True
        mock_get_cred.return_value = {"is_expired": False}
        mock_profile_acct.return_value = {"emailAddress": "test@test.com", "accountUuid": "123"}
        mock_read_json.return_value = {}

        ok, msg = claude_oauth_manager.switch_profile("test_profile")
        
        self.assertTrue(ok)
        self.assertEqual(mock_copy2.call_count, 2)
        mock_fix_perms.assert_called_once()
        mock_write_json.assert_called_once()


    @patch("claude_oauth_manager._profile_account")
    @patch("claude_oauth_manager.get_credentials_info")
    def test_is_same_account_uuid(self, mock_get_cred, mock_profile_acct):
        # UUID match
        mock_profile_acct.return_value = {"accountUuid": "123"}
        active_acc = {"accountUuid": "123", "emailAddress": "other@a.com"}
        self.assertTrue(claude_oauth_manager._is_same_account(Path("."), active_acc, {}))

    @patch("claude_oauth_manager._profile_account")
    @patch("claude_oauth_manager.get_credentials_info")
    def test_is_same_account_email(self, mock_get_cred, mock_profile_acct):
        # Email match fallback (UUID missing)
        mock_profile_acct.return_value = {"emailAddress": "test@domain.com"}
        active_acc = {"emailAddress": "TEST@domain.com"}
        self.assertTrue(claude_oauth_manager._is_same_account(Path("."), active_acc, {}))

    @patch("claude_oauth_manager._profile_account")
    @patch("claude_oauth_manager.get_credentials_info")
    def test_is_same_account_token_fallback(self, mock_get_cred, mock_profile_acct):
        # Token match fallback
        mock_profile_acct.return_value = {}
        active_acc = {}
        mock_get_cred.return_value = {"refresh_token": "token123"}
        self.assertTrue(claude_oauth_manager._is_same_account(Path("."), active_acc, {"refresh_token": "token123"}))


    @patch("claude_oauth_manager.subprocess.run")
    def test_fix_wsl_permissions(self, mock_run):
        claude_oauth_manager._fix_wsl_permissions()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "wsl.exe")
        self.assertEqual(args[5], "chmod")
        self.assertEqual(args[6], "600")

if __name__ == "__main__":
    unittest.main()
