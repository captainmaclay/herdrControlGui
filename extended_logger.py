"""Модуль централизованного журналирования событий бенчмарка и перехвата телеметрии.

Научно-инженерная цель:
Сбор полных дампов выполнения, исключений и метрик задержки для аудита
воспроизводимости экспериментов по сравнительному анализу сабагентов.
"""

import os
import sys
import datetime
import traceback
from pathlib import Path
import backup_manager

EXTENDED_LOG_FILE = backup_manager.BASE_DIR / "extended.log"
MAX_LOG_BYTES = int(1.5 * 1024 * 1024)  # 1.5 MB

def _rotate_extended_log():
    if not EXTENDED_LOG_FILE.exists():
        return
    try:
        if EXTENDED_LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            with open(EXTENDED_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            keep_lines = lines[len(lines) // 2 :]
            with open(EXTENDED_LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [SYSTEM] --- Ротация: очищены старые логи ---\n")
                f.writelines(keep_lines)
    except Exception:
        pass

def write_ext_log(level, msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}\n"
    try:
        with open(EXTENDED_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        _rotate_extended_log()
    except Exception:
        pass

class StreamRedirector:
    def __init__(self, original, level="INFO"):
        self.original = original
        self.level = level
        self.buffer = ""

    def write(self, msg):
        if self.original is not None:
            try:
                self.original.write(msg)
            except Exception:
                pass
        self.buffer += msg
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                write_ext_log(self.level, line.strip())

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass

def excepthook(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
    write_ext_log("ERROR", "".join(lines).strip())
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def _hook_tkinter_events():
    try:
        import tkinter as tk
        if hasattr(tk, 'CallWrapper'):
            original_call = tk.CallWrapper.__call__
            def hooked_call(self, *args):
                try:
                    func = self.func
                    name = getattr(func, "__name__", None)
                    if not name or name == "<lambda>":
                        # Try to get method string if it's a bound method
                        if hasattr(func, "__self__"):
                            name = f"{func.__self__.__class__.__name__}.{func.__name__}"
                        else:
                            name = str(func)
                    write_ext_log("UI_EVENT", f"Вызов функции или кнопки: {name}")
                except Exception:
                    pass
                return original_call(self, *args)
            tk.CallWrapper.__call__ = hooked_call
    except ImportError:
        pass

def init_extended_logging():
    sys.stdout = StreamRedirector(sys.stdout, "INFO")
    sys.stderr = StreamRedirector(sys.stderr, "ERROR")
    sys.excepthook = excepthook
    write_ext_log("SYSTEM", "--- Приложение запущено ---")
    _hook_tkinter_events()
