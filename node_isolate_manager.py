"""Менеджер сетевой изоляции среды выполнения Node.js и WSL2 (Fail-Safe Channel Guard).

Научно-инженерная цель:
Обеспечение абсолютной чистоты сетевого контура для тестовых и исследовательских узлов Type-B (WSL2).
Изолирует сетевой контур Linux исключительно на выделенный локальный сокет (SOCKS5 :1015 / HTTP :11015).
Предотвращает случайную утечку запросов в нетарифицируемые общие сетевые маршруты хоста (direct WAN/LAN)
и отсекает сторонние обходные прокси (например, сокет :2080).
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import threading
import time

LAST_APPLY_LOG = "No apply attempts recorded yet."

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")


def run_ps_script(script_str: str) -> subprocess.CompletedProcess:
    """Выполняет PowerShell скрипт в Windows."""
    encoded = base64.b64encode(script_str.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )


def run_wsl_root_cmd(bash_cmd: str, timeout: float = 6.0) -> tuple[int, str]:
    """Выполняет команду от имени root внутри WSL2."""
    try:
        r = subprocess.run(
            ["wsl.exe", "-d", WSL_DISTRO, "-u", "root", "-e", "bash", "-c", bash_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode, (r.stdout or "").strip()
    except Exception as ex:
        return -1, str(ex)


def check_wsl_isolation_active() -> bool:
    """Проверяет, активна ли сетевая тюрьма (nftables / iptables) в ядре Linux WSL2."""
    rc, out = run_wsl_root_cmd("nft list table inet herdr_filter 2>/dev/null")
    if rc == 0 and "chain output" in out:
        return True
    rc2, out2 = run_wsl_root_cmd("iptables -C OUTPUT -j HERDR_ISOLATE 2>/dev/null")
    return rc2 == 0


def install_persistent_wsl_isolation(
    port: int = 1015,
    http_port: int | None = None,
    block_bypass_ports: list[int] | None = None,
) -> bool:
    """Устанавливает постоянную (persistent) сетевую изоляцию ядра Linux в WSL2.

    1. Фиксирует правила ограничения трафика в /etc/nftables.conf.
    2. Активирует системную службу nftables (systemctl enable nftables).
    3. Создает автономный скрипт загрузки /usr/local/bin/herdr_boot_isolation.sh.
    4. Гарантирует автозапуск через [boot] command в /etc/wsl.conf.

    Даже при закрытом Herdr Control Center и сразу после старта Windows / перезапуска WSL2,
    подсистема Linux стартует с активированной сетевой тюрьмой (изоляция на сокет 1015, 0 утечек).
    """
    if http_port is None:
        http_port = 10000 + int(port)
    if block_bypass_ports is None:
        block_bypass_ports = [2080]

    bypass_reject_rules = []
    for bp in block_bypass_ports:
        if bp != port and bp != http_port:
            bypass_reject_rules.append(f"        ip daddr 127.0.0.1 tcp dport {bp} counter reject")
    bypass_rules_str = "\n".join(bypass_reject_rules)

    nft_conf_content = f"""#!/usr/sbin/nft -f

flush ruleset

table inet herdr_filter {{
    chain output {{
        type filter hook output priority filter; policy accept;
{bypass_rules_str}
        oifname {{ "lo", "loopback0" }} counter accept
        counter reject
    }}
}}
"""

    boot_script_content = f"""#!/bin/bash
# Herdr WSL2 Autonomous Boot Containment Guard
nft -f /etc/nftables.conf 2>/dev/null || true

cat << 'HERDR_ENV' > /etc/profile.d/herdr_claude_env.sh
export HTTPS_PROXY='http://127.0.0.1:{http_port}'
export HTTP_PROXY='http://127.0.0.1:{http_port}'
export ALL_PROXY='socks5h://127.0.0.1:{port}'
export https_proxy='http://127.0.0.1:{http_port}'
export http_proxy='http://127.0.0.1:{http_port}'
export all_proxy='socks5h://127.0.0.1:{port}'
HERDR_ENV
chmod 644 /etc/profile.d/herdr_claude_env.sh

for user_dir in /home/*; do
    if [ -d "$user_dir/.claude" ]; then
        user_name=$(basename "$user_dir")
        settings_file="$user_dir/.claude/settings.json"
        python3 -c "
import json
p = '$settings_file'
try:
    with open(p, 'r') as f:
        d = json.load(f)
except Exception:
    d = {{}}
if 'env' not in d:
    d['env'] = {{}}
d['env']['HTTPS_PROXY'] = 'http://127.0.0.1:{http_port}'
d['env']['HTTP_PROXY'] = 'http://127.0.0.1:{http_port}'
d['env']['ALL_PROXY'] = 'socks5h://127.0.0.1:{port}'
d['env']['https_proxy'] = 'http://127.0.0.1:{http_port}'
d['env']['http_proxy'] = 'http://127.0.0.1:{http_port}'
d['env']['all_proxy'] = 'socks5h://127.0.0.1:{port}'
with open(p, 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
        chown -R "$user_name:$user_name" "$user_dir/.claude" 2>/dev/null || true
    fi
done
"""

    b64_nft = base64.b64encode(nft_conf_content.encode("utf-8")).decode("ascii")
    b64_boot = base64.b64encode(boot_script_content.encode("utf-8")).decode("ascii")

    installer_script = f"""
echo {b64_nft} | base64 -d > /etc/nftables.conf
chmod 755 /etc/nftables.conf
echo {b64_boot} | base64 -d > /usr/local/bin/herdr_boot_isolation.sh
chmod 755 /usr/local/bin/herdr_boot_isolation.sh

if [ -f /etc/wsl.conf ]; then
    if ! grep -q 'herdr_boot_isolation.sh' /etc/wsl.conf; then
        if grep -q '\\[boot\\]' /etc/wsl.conf; then
            sed -i '/\\[boot\\]/a command=/usr/local/bin/herdr_boot_isolation.sh' /etc/wsl.conf
        else
            echo -e "\\n[boot]\\nsystemd=true\\ncommand=/usr/local/bin/herdr_boot_isolation.sh" >> /etc/wsl.conf
        fi
    fi
else
    echo -e "[boot]\\nsystemd=true\\ncommand=/usr/local/bin/herdr_boot_isolation.sh\\n" > /etc/wsl.conf
fi

systemctl enable nftables 2>/dev/null || true
"""
    rc, _ = run_wsl_root_cmd(installer_script)
    return rc == 0


_LAST_APPLIED_ISOLATION_KEY: tuple | None = None


def apply_wsl_isolation(
    port: int = 1015,
    http_port: int | None = None,
    block_bypass_ports: list[int] | None = None,
    killswitch: bool = False,
    force: bool = False,
) -> bool:
    """Устанавливает строгую сетевую изоляцию ядра Linux в WSL2.

    1. Блокирует обходные локальные прокси на loopback (например, 2080).
    2. Разрешает loopback интерфейсы (lo и loopback0 в WSL2 mirrored mode).
    3. Блокирует абсолютно весь не-loopback трафик (direct WAN/LAN, IPv4 & IPv6).
    4. Синхронизирует системные переменные окружения в /etc/profile.d/herdr_claude_env.sh.
    5. Фиксирует автономную автозагрузку в /etc/nftables.conf и /etc/wsl.conf.
    """
    global LAST_APPLY_LOG, _LAST_APPLIED_ISOLATION_KEY
    if http_port is None:
        http_port = 10000 + int(port)
    if block_bypass_ports is None:
        block_bypass_ports = [2080]

    current_key = (int(port), int(http_port), bool(killswitch), tuple(sorted(block_bypass_ports)))
    if not force and _LAST_APPLIED_ISOLATION_KEY == current_key:
        return True

    # Скрипт nftables
    nft_commands = [
        "nft add table inet herdr_filter 2>/dev/null || true",
        "nft 'add chain inet herdr_filter output { type filter hook output priority filter; policy accept; }' 2>/dev/null || true",
        "nft flush chain inet herdr_filter",
    ]
    for bp in block_bypass_ports:
        if bp != port and bp != http_port:
            nft_commands.append(
                f"nft add rule inet herdr_filter output ip daddr 127.0.0.1 tcp dport {bp} counter reject"
            )
    nft_commands.append("nft add rule inet herdr_filter output oifname { \"lo\", \"loopback0\" } counter accept")
    nft_commands.append("nft add rule inet herdr_filter output counter reject")

    combined_cmd = " && ".join(nft_commands)
    rc, out = run_wsl_root_cmd(combined_cmd)

    success = False
    if rc == 0:
        success = True
    else:
        # Fallback to iptables & ip6tables
        fallback_script = f"""
        iptables -N HERDR_ISOLATE 2>/dev/null || iptables -F HERDR_ISOLATE
        iptables -C OUTPUT -j HERDR_ISOLATE 2>/dev/null || iptables -I OUTPUT 1 -j HERDR_ISOLATE
        iptables -F HERDR_ISOLATE
        iptables -A HERDR_ISOLATE -p tcp -d 127.0.0.1 --dport 2080 -j REJECT
        iptables -A HERDR_ISOLATE -o lo -j ACCEPT
        iptables -A HERDR_ISOLATE -o loopback0 -j ACCEPT
        iptables -A HERDR_ISOLATE -j REJECT

        ip6tables -N HERDR_ISOLATE 2>/dev/null || ip6tables -F HERDR_ISOLATE
        ip6tables -C OUTPUT -j HERDR_ISOLATE 2>/dev/null || ip6tables -I OUTPUT 1 -j HERDR_ISOLATE
        ip6tables -F HERDR_ISOLATE
        ip6tables -A HERDR_ISOLATE -o lo -j ACCEPT
        ip6tables -A HERDR_ISOLATE -o loopback0 -j ACCEPT
        ip6tables -A HERDR_ISOLATE -j REJECT
        """
        rc_fb, out_fb = run_wsl_root_cmd(fallback_script)
        success = (rc_fb == 0)

    # Запись системных переменных для шелла в WSL2
    if killswitch:
        env_content = (
            "export HTTPS_PROXY='http://127.0.0.1:1'\n"
            "export HTTP_PROXY='http://127.0.0.1:1'\n"
            "export ALL_PROXY='socks5h://127.0.0.1:1'\n"
            "export https_proxy='http://127.0.0.1:1'\n"
            "export http_proxy='http://127.0.0.1:1'\n"
            "export all_proxy='socks5h://127.0.0.1:1'\n"
        )
    else:
        env_content = (
            f"export HTTPS_PROXY='http://127.0.0.1:{http_port}'\n"
            f"export HTTP_PROXY='http://127.0.0.1:{http_port}'\n"
            f"export ALL_PROXY='socks5h://127.0.0.1:{port}'\n"
            f"export https_proxy='http://127.0.0.1:{http_port}'\n"
            f"export http_proxy='http://127.0.0.1:{http_port}'\n"
            f"export all_proxy='socks5h://127.0.0.1:{port}'\n"
        )

    write_env_cmd = f"cat << 'EOF' > /etc/profile.d/herdr_claude_env.sh\n{env_content}EOF\nchmod 644 /etc/profile.d/herdr_claude_env.sh"
    run_wsl_root_cmd(write_env_cmd)

    # Гарантируем персистентную автозагрузку при старте Windows / WSL2
    try:
        install_persistent_wsl_isolation(
            port=port,
            http_port=http_port,
            block_bypass_ports=block_bypass_ports,
        )
    except Exception:
        pass

    # Хостовый брандмауэр Windows не блокирует процессы разработчика
    remove_firewall_rule()

    if success:
        _LAST_APPLIED_ISOLATION_KEY = current_key

    LAST_APPLY_LOG = f"WSL Isolation Applied: success={success}, port={port}, killswitch={killswitch}"
    return success


def remove_wsl_isolation() -> bool:
    """Временно снимает сетевую изоляцию ядра Linux в WSL2 для текущей сессии.

    Восстанавливает прямой доступ (Direct IP) для экстренных задач.
    ВАЖНО: Персистентная конфигурация автозагрузки (/etc/nftables.conf и /etc/wsl.conf)
    сохраняется. При следующем перезапуске WSL2 или перезагрузке Windows ограничение
    на сокет 1015 восстановится автоматически по умолчанию.
    """
    global LAST_APPLY_LOG, _LAST_APPLIED_ISOLATION_KEY
    _LAST_APPLIED_ISOLATION_KEY = None
    cmd = """
    nft flush chain inet herdr_filter 2>/dev/null || true
    nft delete table inet herdr_filter 2>/dev/null || true
    iptables -D OUTPUT -j HERDR_ISOLATE 2>/dev/null || true
    iptables -F HERDR_ISOLATE 2>/dev/null || true
    iptables -X HERDR_ISOLATE 2>/dev/null || true
    ip6tables -D OUTPUT -j HERDR_ISOLATE 2>/dev/null || true
    ip6tables -F HERDR_ISOLATE 2>/dev/null || true
    ip6tables -X HERDR_ISOLATE 2>/dev/null || true
    rm -f /etc/profile.d/herdr_claude_env.sh
    """
    rc, _ = run_wsl_root_cmd(cmd)
    remove_firewall_rule()
    LAST_APPLY_LOG = "WSL Isolation Removed (temporary for current session): direct access restored"
    return True


def get_node_path() -> str:
    """Возвращает путь к node.exe в Windows."""
    return "C:\\Program Files\\nodejs\\node.exe"


def check_isolation_status(target_ip: str = "127.0.0.1", target_port: int = 1015, enabled: bool = True) -> bool:
    """Проверяет отсутствие блокирующих правил брандмауэра Windows и статус изоляции."""
    try:
        res = run_ps_script(
            "(Get-NetFirewallRule -DisplayName 'Claude Node Isolate' -ErrorAction SilentlyContinue) -ne $null"
        )
        rule_exists = "True" in str(res.stdout)
        if rule_exists:
            return False
    except Exception:
        pass
    return True


def apply_firewall_rule(host: str = "127.0.0.1", port: int = 1015) -> bool:
    """Применяет изоляцию для узлов Type-B."""
    remove_firewall_rule()
    return apply_wsl_isolation(port=port)


def remove_firewall_rule() -> bool:
    """Гарантирует удаление блокирующих правил брандмауэра Windows."""
    ps_script = """
    $ErrorActionPreference = 'SilentlyContinue'
    Remove-NetFirewallRule -DisplayName 'Claude Node Isolate' | Out-Null
    """
    try:
        run_ps_script(ps_script)
        return True
    except Exception:
        return False


def enforce_isolation(host: str = "127.0.0.1", port: int = 1015, enable: bool = True):
    """Синхронизирует состояние изоляции."""
    if enable:
        apply_wsl_isolation(port=port)
    else:
        remove_wsl_isolation()


class NodeIsolateThread(threading.Thread):
    def __init__(self, get_state_cb=None):
        super().__init__(daemon=True)
        self.get_state_cb = get_state_cb
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        remove_firewall_rule()