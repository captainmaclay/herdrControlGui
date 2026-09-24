import unittest
from unittest.mock import patch, MagicMock
import node_isolate_manager

class TestNodeIsolateManager(unittest.TestCase):
    @patch('node_isolate_manager.subprocess.run')
    def test_check_isolation_status_true(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "True"
        mock_run.return_value = mock_result
        
        # When enabled is True and rule exists
        result = node_isolate_manager.check_isolation_status("127.0.0.1", 1015, True)
        self.assertTrue(result)
        
    @patch('node_isolate_manager.subprocess.run')
    def test_check_isolation_status_false(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "True"
        mock_run.return_value = mock_result
        
        # When enabled is False but rule exists (needs update)
        result = node_isolate_manager.check_isolation_status("127.0.0.1", 1015, False)
        self.assertFalse(result)

    @patch('node_isolate_manager.subprocess.run')
    def test_apply_firewall_rule(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        result = node_isolate_manager.apply_firewall_rule("127.0.0.1", 1015)
        self.assertTrue(result)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("powershell", args)
        self.assertIn("New-NetFirewallRule -DisplayName 'Claude Node Isolate'", args[3])

    @patch('node_isolate_manager.subprocess.run')
    def test_remove_firewall_rule(self, mock_run):
        result = node_isolate_manager.remove_firewall_rule()
        self.assertTrue(result)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("Remove-NetFirewallRule", args[3])

    @patch('node_isolate_manager.apply_firewall_rule')
    @patch('node_isolate_manager.remove_firewall_rule')
    @patch('node_isolate_manager.check_isolation_status')
    def test_enforce_isolation(self, mock_check, mock_remove, mock_apply):
        # Case 1: Status mismatch, want enable
        mock_check.return_value = False
        node_isolate_manager.enforce_isolation("127.0.0.1", 1015, True)
        mock_apply.assert_called_once_with("127.0.0.1", 1015)
        mock_remove.assert_not_called()
        
        mock_apply.reset_mock()
        
        # Case 2: Status mismatch, want disable
        mock_check.return_value = False
        node_isolate_manager.enforce_isolation("127.0.0.1", 1015, False)
        mock_remove.assert_called_once()
        mock_apply.assert_not_called()

        mock_remove.reset_mock()

        # Case 3: Status matches, do nothing
        mock_check.return_value = True
        node_isolate_manager.enforce_isolation("127.0.0.1", 1015, True)
        mock_apply.assert_not_called()
        mock_remove.assert_not_called()


