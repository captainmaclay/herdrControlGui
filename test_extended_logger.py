import unittest
import sys
import types
from pathlib import Path

# Mock tkinter before importing logger
sys.modules['tkinter'] = types.ModuleType('tkinter')
sys.modules['tkinter'].CallWrapper = type('CallWrapper', (), {'__call__': lambda self, *args: "original_result"})

import extended_logger

class TestExtendedLogger(unittest.TestCase):
    def setUp(self):
        # Override log file to temp
        self.old_log = extended_logger.EXTENDED_LOG_FILE
        self.temp_log = Path("/tmp/test_extended.log")
        extended_logger.EXTENDED_LOG_FILE = self.temp_log
        if self.temp_log.exists():
            self.temp_log.unlink()
            
    def tearDown(self):
        if self.temp_log.exists():
            self.temp_log.unlink()
        extended_logger.EXTENDED_LOG_FILE = self.old_log

    def test_tk_hook_triggers_log(self):
        extended_logger._hook_tkinter_events()
        import tkinter as tk
        
        # Simulate tk.CallWrapper usage
        cw = tk.CallWrapper()
        
        # mock a function
        def mock_ui_click():
            pass
            
        cw.func = mock_ui_click
        cw.__call__()
        
        # Verify log has UI_EVENT
        content = self.temp_log.read_text(encoding="utf-8")
        self.assertIn("[UI_EVENT]", content)
        self.assertIn("mock_ui_click", content)
        
    def test_log_rotation_size(self):
        self.assertEqual(extended_logger.MAX_LOG_BYTES, 1572864) # 1.5MB
        
        # Create a file exactly larger than MAX
        big_content = ("A" * 100 + "\n") * (extended_logger.MAX_LOG_BYTES // 100 + 1)
        self.temp_log.write_text(big_content, encoding="utf-8")
        
        # Trigger any log
        extended_logger.write_ext_log("INFO", "trigger rotation")
        
        # Verify size went down
        new_size = self.temp_log.stat().st_size
        self.assertTrue(new_size < extended_logger.MAX_LOG_BYTES)

if __name__ == '__main__':
    unittest.main()
