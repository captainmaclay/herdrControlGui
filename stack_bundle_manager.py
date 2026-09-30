#!/usr/bin/env python3
"""
Stack Bundle Manager Proxy.
Импортирует и проксирует функционал из подмодуля Omni_Aion/stack_bundle_manager.py
для полной обратной совместимости с существующим кодом, тестами и GUI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CUR_DIR = Path(__file__).resolve().parent
if str(_CUR_DIR) not in sys.path:
    sys.path.insert(0, str(_CUR_DIR))

import Omni_Aion.stack_bundle_manager as _mod

# Реэкспортируем все функции, константы и приватные методы для совместимости с тестами
for _name in dir(_mod):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_mod, _name)
