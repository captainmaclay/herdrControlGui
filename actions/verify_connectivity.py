"""Действие сквозной верификации сетевого контура и API Anthropic (Verify Connectivity Action).

Проверяет:
1. Доступность локальных сокетов туннеля (SOCKS5 :1015, HTTP :11015)
2. Выходной IP адрес через туннель из WSL2
3. Доступность эндпоинта Anthropic API (https://api.anthropic.com) без ошибки 400 Location Unsupported
4. Защиту от утечек (Leak Prevention): отсечение прямых обращений в обход прокси
"""

from __future__ import annotations

import logging
import socket
from typing import Any

from actions.base import ActionResult, BaseAction

logger = logging.getLogger("herdr.actions.verify_connectivity")


class VerifyConnectivityAction(BaseAction):
    name = "verify_connectivity"
    description = "Сквозная проверка туннеля, выхода в интернет и Anthropic API из WSL2"

    def __init__(
        self,
        socks_port: int = 1015,
        http_port: int = 11015,
        distro: str = "Ubuntu",
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.socks_port = socks_port
        self.http_port = http_port
        self.distro = distro

    def run(
        self,
        check_anthropic: bool = True,
        check_leak: bool = True,
        **kwargs: Any,
    ) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        try:
            # Шаг 1: Проверка локальных сокетов
            self.execute_step(
                result,
                "check_local_sockets",
                self._check_local_sockets,
            )

            # Шаг 2: Проверка выходного IP через туннель
            self.execute_step(
                result,
                "check_exit_ip",
                self._check_exit_ip,
            )

            # Шаг 3: Проверка доступности Anthropic API
            if check_anthropic:
                self.execute_step(
                    result,
                    "check_anthropic_api",
                    self._check_anthropic_api,
                )

            # Шаг 4: Проверка отсутствия утечек
            if check_leak:
                self.execute_step(
                    result,
                    "check_leak_prevention",
                    self._check_leak_prevention,
                )

        except Exception as exc:
            result.add_error(f"Прерывание проверки соединения: {exc}")

        return result.finish()

    def _is_port_open(self, port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return s.connect_ex((host, port)) == 0
        except Exception:
            return False

    def _check_local_sockets(self) -> tuple[str, str, dict[str, Any]]:
        """Проверяет локальные порты 1015 и 11015 на Windows-хосте."""
        if self.dry_run:
            return "dry_run", f"[dry-run] Сокеты SOCKS5 :{self.socks_port} и HTTP :{self.http_port} симулированы (ОТКРЫТЫ)", {
                "socks_port": self.socks_port,
                "socks_open": True,
                "http_port": self.http_port,
                "http_open": True,
            }

        socks_open = self._is_port_open(self.socks_port)
        http_open = self._is_port_open(self.http_port)

        status = "ok" if (socks_open and http_open) else "failed"
        msg = f"Сокеты: SOCKS5 :{self.socks_port}={'ОТКРЫТ' if socks_open else 'ЗАКРЫТ'}, HTTP :{self.http_port}={'ОТКРЫТ' if http_open else 'ЗАКРЫТ'}"

        if not socks_open and not http_open:
            msg += " (ВНИМАНИЕ: туннель остановлен или заблокирован Killswitch)"

        return status, msg, {
            "socks_port": self.socks_port,
            "socks_open": socks_open,
            "http_port": self.http_port,
            "http_open": http_open,
        }

    def _check_exit_ip(self) -> tuple[str, str, dict[str, Any]]:
        """Запрашивает внешний IP через прокси изнутри WSL2."""
        if self.dry_run:
            return "dry_run", "[dry-run] Выходной IP-адрес симулирован (198.51.100.1)", {"exit_ip": "198.51.100.1"}

        cmd = f"curl -s --connect-timeout 5 -x socks5h://127.0.0.1:{self.socks_port} https://ifconfig.me"
        rc, out, err = self.run_wsl(cmd, distro=self.distro)

        if rc != 0 or not out:
            # Fallback to HTTP proxy
            cmd_http = f"curl -s --connect-timeout 5 -x http://127.0.0.1:{self.http_port} https://ifconfig.me"
            rc2, out2, err2 = self.run_wsl(cmd_http, distro=self.distro)
            if rc2 != 0 or not out2:
                return "failed", f"Не удалось получить выходной IP через прокси: {err or err2}", {}
            out = out2

        ip_clean = out.strip()
        return "ok", f"Выходной IP-адрес через туннель: {ip_clean}", {"exit_ip": ip_clean}

    def _check_anthropic_api(self) -> tuple[str, str, dict[str, Any]]:
        """Проверяет ответ api.anthropic.com на предмет региональной блокировки."""
        if self.dry_run:
            return "dry_run", "[dry-run] Anthropic API симулирован: HTTP/1.1 200 Connection established", {"status_code": "200"}

        cmd = f"curl -s -i --connect-timeout 6 -x http://127.0.0.1:{self.http_port} https://api.anthropic.com"
        rc, out, err = self.run_wsl(cmd, distro=self.distro)

        if "User location is not supported" in out or "FAILED_PRECONDITION" in out:
            return "failed", "ОШИБКА 400: User location is not supported! Трафик идет через недопустимый регион.", {
                "response": out[:300]
            }

        # Анализируем HTTP статус
        status_line = out.splitlines()[0] if out.splitlines() else "Unknown"
        if "HTTP/" in status_line:
            code = status_line.split()[1] if len(status_line.split()) > 1 else ""
            return "ok", f"Anthropic API доступен через туннель: {status_line}", {"status_code": code}

        return "failed", f"Не удалось связаться с api.anthropic.com: {err or out[:200]}", {}

    def _check_leak_prevention(self) -> tuple[str, str, dict[str, Any]]:
        """Проверяет, что прямое соединение без прокси заблокировано."""
        if self.dry_run:
            return "dry_run", "[dry-run] Защита от утечек симулирована (прямой трафик блокируется)", {}

        cmd = "curl -s --connect-timeout 2 http://1.1.1.1"
        rc, out, _ = self.run_wsl(cmd, distro=self.distro)

        if rc == 0:
            return "failed", "ПРЕДУПРЕЖДЕНИЕ: Прямой трафик не заблокирован ядром! Сетевая тюрьма неактивна.", {}

        return "ok", "Защита от утечек активна: прямой интернет-трафик блокируется ядром", {}
