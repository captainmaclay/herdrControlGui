#!/usr/bin/env python3
"""
CLI Установщик стека Herdr Stack (OmniRoute + AionUi + Gemini Farm).
Проксирует запуск в подмодуль Omni_Aion/install_herdr_stack.py.
"""

import sys
from pathlib import Path

_CUR_DIR = Path(__file__).resolve().parent
if str(_CUR_DIR) not in sys.path:
    sys.path.insert(0, str(_CUR_DIR))

from Omni_Aion.install_herdr_stack import main

if __name__ == "__main__":
    main()
