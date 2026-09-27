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

    @patch('node_isolate_manager.run_wsl_root_cmd')
    def test_apply_wsl_isolation(self, mock_wsl):
        mock_wsl.return_value = (0, "")
        result = node_isolate_manager.apply_wsl_isolation(port=1015, http_port=11015)
        self.assertTrue(result)
        self.assertGreaterEqual(mock_wsl.call_count, 1)

    @patch('node_isolate_manager.run_wsl_root_cmd')
    def test_remove_wsl_isolation(self, mock_wsl):
        mock_wsl.return_value = (0, "")
        result = node_isolate_manager.remove_wsl_isolation()
        self.assertTrue(result)
        mock_wsl.assert_called_once()

    @patch('node_isolate_manager.run_wsl_root_cmd')
    def test_check_wsl_isolation_active(self, mock_wsl):
        mock_wsl.return_value = (0, "table inet herdr_filter {\n chain output {\n } }")
        self.assertTrue(node_isolate_manager.check_wsl_isolation_active())
        mock_wsl.return_value = (1, "no such table")
        self.assertFalse(node_isolate_manager.check_wsl_isolation_active())

    @patch('node_isolate_manager.run_wsl_root_cmd')
    def test_install_persistent_wsl_isolation(self, mock_wsl):
        mock_wsl.return_value = (0, "")
        result = node_isolate_manager.install_persistent_wsl_isolation(port=1015, http_port=11015)
        self.assertTrue(result)
        mock_wsl.assert_called_once()
        cmd = mock_wsl.call_args[0][0]
        self.assertIn("/etc/nftables.conf", cmd)
        self.assertIn("herdr_boot_isolation.sh", cmd)
        self.assertIn("systemctl enable nftables", cmd)

    @patch('node_isolate_manager.run_wsl_root_cmd')
    def test_remove_wsl_isolation_preserves_boot_files(self, mock_wsl):
        mock_wsl.return_value = (0, "")
        result = node_isolate_manager.remove_wsl_isolation()
        self.assertTrue(result)
        cmd = mock_wsl.call_args[0][0]
        # Only runtime table and session env are removed
        self.assertIn("nft delete table inet herdr_filter", cmd)
        self.assertNotIn("rm -f /etc/nftables.conf", cmd)
        self.assertNotIn("rm -f /usr/local/bin/herdr_boot_isolation.sh", cmd)


if __name__ == "__main__":
    unittest.main()


