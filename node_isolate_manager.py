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
    """Возвращает актуальное состояние (соответствует ли брандмауэр желаемому enabled)"""
    # Если мы работаем под WSL, мы не можем так просто дергать PowerShell с правами админа.
    # Но Herdr Control Center - это Windows приложение (Pystray/Tkinter).
    try:
        res = run_ps_script("(Get-NetFirewallRule -DisplayName 'Claude Node Isolate' -ErrorAction SilentlyContinue) -ne $null")
        rule_exists = "True" in res.stdout
        return rule_exists == enabled
    except Exception:
        return False

def apply_firewall_rule(host: str, port: int) -> bool:
    node_path = get_node_path()
    # Строим скрипт. Мы блокируем два пула адресов: вне 127.0.0.1, и локальные порты вне target_port.
    # Но для надежности заблокируем просто весь глобальный интернет.
    ps_script = r'''
    $ErrorActionPreference = 'Stop'
    try { Remove-NetFirewallRule -DisplayName 'Claude Node Isolate' -ErrorAction Stop | Out-Null } catch {}

    $allBlocked = @(
        '1.0.0.1-126.255.255.254', 
        '128.0.0.1-223.255.255.254',
        '2000::/3',
        'fc00::/7',
        'fe80::/10'
    )
    $appliedCount = 0

    function Apply-Isolate {
        param([string]$Target)
        if (Test-Path $Target) {
            New-NetFirewallRule -DisplayName 'Claude Node Isolate' -Direction Outbound -Program $Target -Action Block -RemoteAddress $allBlocked -Profile Any -ErrorAction Stop | Out-Null
            $global:appliedCount++
            Write-Output "LOCKED: $Target"
        }
    }

    # 1. Base Node
    Apply-Isolate 'C:\Program Files\nodejs\node.exe'
    Apply-Isolate "${env:ProgramFiles(x86)}\nodejs\node.exe"

    # 2. NVM Node paths
    $nvmPaths = @("$env:APPDATA\nvm\v*\node.exe", "$env:LOCALAPPDATA\nvm\v*\node.exe", "C:\nvm\v*\node.exe")
    foreach ($nPath in $nvmPaths) {
        try {
            $nvmNodes = Resolve-Path $nPath -ErrorAction Stop
            if ($nvmNodes) {
                foreach ($node in $nvmNodes) { Apply-Isolate $node.Path }
            }
        } catch {}
    }

    # 3. Claude Desktop
    $possiblePaths = @(
        "$env:LOCALAPPDATA\Programs\Claude\Claude.exe",
        "$env:LOCALAPPDATA\AnthropicClaude\Claude.exe",
        "$env:PROGRAMFILES\Claude\Claude.exe",
        "${env:ProgramFiles(x86)}\Claude\Claude.exe",
        "$env:LOCALAPPDATA\Claude\Claude.exe",
        "$env:APPDATA\Claude\Claude.exe"
    )
    foreach ($path in $possiblePaths) { Apply-Isolate $path }

    if ($global:appliedCount -eq 0) {
        throw "NO_EXECUTABLES_FOUND: Изоляция не применена, так как ни Node.js, ни Claude.exe не найдены ни по одному из стандартных путей!"
    }
    '''
    global LAST_APPLY_LOG
    try:
        # Для отладки убираем SilentlyContinue
        ps_script = ps_script.replace("$ErrorActionPreference = 'SilentlyContinue'", "")
        res = run_ps_script(ps_script)
        LAST_APPLY_LOG = f"=== COMMAND APPLIED ===\n{ps_script.strip()}\n\n=== RESPONSE CODE: {res.returncode} ===\n=== STDOUT ===\n{res.stdout.strip()}\n=== STDERR ===\n{res.stderr.strip()}"
        logging.info(f"VITAL DEBUG -> {LAST_APPLY_LOG}")
        with open("D:\\My files\\herdrControlGui\\ps_apply_debug.log", "w", encoding="utf-8") as f: f.write(LAST_APPLY_LOG)
        if res.returncode != 0:
            logging.error(f"Firewall apply failed: {res.stderr}")
            if "Access is denied" in res.stderr or "PermissionDenied" in res.stderr:
                 LAST_APPLY_LOG += "\n\n>>> CRITICAL: ACCESS DENIED. YOU MUST RUN AS ADMINISTRATOR! <<<"
            return False
        return True
    except Exception as e:
        LAST_APPLY_LOG = f"Exception executing apply script:\n{e}"
        logging.error(LAST_APPLY_LOG)
        return False

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
    """Применяет или удаляет правило, если текущее состояние не соответствует желаемому."""
    status = check_isolation_status(host, port, enable)
    if not status:
        if enable:
            apply_firewall_rule(host, port)
        else:
            remove_firewall_rule()

class NodeIsolateThread(threading.Thread):
    def __init__(self, get_state_cb):
        super().__init__(daemon=True)
        self.get_state_cb = get_state_cb
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                host, port, enable = self.get_state_cb()
                enforce_isolation(host, port, enable)
            except Exception as e:
                logging.error(f"Node Isolate error: {e}")
            self._stop_event.wait(20.0)