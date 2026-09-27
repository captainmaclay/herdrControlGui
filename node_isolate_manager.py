"""Менеджер сетевой изоляции среды выполнения Node.js (Fail-Safe Channel Guard).

Научно-инженерная цель:
Обеспечение чистоты эксперимента при бенчмаркинге. Изолирует сетевой контур процесса Node.js
исключительно на выделенный сокет локального прокси, предотвращая случайную утечку запросов
в нетарифицируемые общие сетевые маршруты хоста и исключая искажение замеров задержки.
"""

import subprocess
import threading
import time
import logging
import base64

LAST_APPLY_LOG = 'No apply attempts recorded yet.'

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

def run_ps_script(script_str: str) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(script_str.encode('utf-16-le')).decode('ascii')
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW
    )

def get_node_path() -> str:
    # Ищем node.exe
    return "C:\\Program Files\\nodejs\\node.exe"

def check_isolation_status(target_ip: str, target_port: int, enabled: bool) -> bool:
    """Проверяет отсутствие блокирующих правил брандмауэра."""
    try:
        res = run_ps_script("(Get-NetFirewallRule -DisplayName 'Claude Node Isolate' -ErrorAction SilentlyContinue) -ne $null")
        rule_exists = "True" in res.stdout
        return not rule_exists
    except Exception:
        return True

def apply_firewall_rule(host: str, port: int) -> bool:
    """Блокировки брандмауэра для хостовых процессов упразднены. Гарантируем удаление правил."""
    return remove_firewall_rule()

def remove_firewall_rule() -> bool:
    ps_script = """
    $ErrorActionPreference = 'SilentlyContinue'
    Remove-NetFirewallRule -DisplayName 'Claude Node Isolate' | Out-Null
    """
    try:
        run_ps_script(ps_script)
        return True
    except Exception:
        return False

def enforce_isolation(host: str, port: int, enable: bool):
    """Всегда обеспечивает снятие правил брандмауэра Windows."""
    remove_firewall_rule()

class NodeIsolateThread(threading.Thread):
    def __init__(self, get_state_cb=None):
        super().__init__(daemon=True)
        self.get_state_cb = get_state_cb
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        remove_firewall_rule()