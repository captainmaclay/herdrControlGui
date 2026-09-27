import unittest
from unittest.mock import patch, MagicMock
import node_isolate_manager

class TestNodeIsolateManager(unittest.TestCase):
    @patch('node_isolate_manager.subprocess.run')
    def test_check_isolation_status(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "False"
        mock_run.return_value = mock_result
        
        result = node_isolate_manager.check_isolation_status("127.0.0.1", 1015, True)
        self.assertTrue(result)

    @patch('node_isolate_manager.subprocess.run')
    def test_remove_firewall_rule(self, mock_run):
        result = node_isolate_manager.remove_firewall_rule()
        self.assertTrue(result)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        import base64
        decoded = base64.b64decode(args[5]).decode('utf-16-le')
        self.assertIn("Remove-NetFirewallRule", decoded)

    @patch('node_isolate_manager.remove_firewall_rule')
    def test_enforce_isolation(self, mock_remove):
        node_isolate_manager.enforce_isolation("127.0.0.1", 1015, False)
        mock_remove.assert_called_once()


