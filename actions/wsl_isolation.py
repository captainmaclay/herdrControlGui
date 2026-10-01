"""Действие управления сетевой изоляцией Linux-ядра в WSL2 (WSL Network Isolation Action).

Обеспечивает Zero-Trust и Zero-Leak режим:
1. Применение правил nftables (запрет прямых WAN/LAN пакетов, разрешение loopback)
2. Блокировка сторонних обходных портов (например, 2080)
3. Настройка персистентного автозапуска при старте системы (/etc/wsl.conf)
4. Верификация Zero-Leak (контрольная проверка отсечения прямых запросов ядром)
5. Возможность штатного и экстренного снятия ограничений (teardown)
"""

from __future__ import annotations

import logging
from typing import Any

from actions.base import ActionResult, BaseAction

logger = logging.getLogger("herdr.actions.wsl_isolation")


class WslIsolationAction(BaseAction):
    name = "wsl_isolation"
    description = "Управление сетевой изоляцией ядра Linux в WSL2 (Zero-Leak Jail)"

    def __init__(
        self,
        socks_port: int = 1015,
        http_port: int = 11015,
        block_bypass_ports: list[int] | None = None,
        distro: str = "Ubuntu",
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.socks_port = socks_port
        self.http_port = http_port
        self.block_bypass_ports = block_bypass_ports or [2080]
        self.distro = distro

    def run(
        self,
        action_mode: str = "apply",  # "apply", "teardown", "verify"
        killswitch: bool = False,
        force: bool = False,
        install_persistent: bool = True,
        **kwargs: Any,
    ) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        try:
            if action_mode == "teardown":
                self.execute_step(
                    result,
                    "remove_jail",
                    self._remove_jail,
                )
            elif action_mode == "verify":
                self.execute_step(
                    result,
                    "verify_isolation_status",
                    self._verify_isolation_status,
                )
                self.execute_step(
                    result,
                    "verify_zero_leak",
                    self._verify_zero_leak,
                )
            else:  # "apply"
                self.execute_step(
                    result,
                    "apply_kernel_jail",
                    self._apply_kernel_jail,
                    killswitch,
                    force,
                )
                if install_persistent:
                    self.execute_step(
                        result,
                        "install_persistent_boot",
                        self._install_persistent_boot,
                    )
                self.execute_step(
                    result,
                    "verify_isolation_status",
                    self._verify_isolation_status,
                )

        except Exception as exc:
            result.add_error(f"Прерывание выполнения управления изоляцией WSL: {exc}")

        return result.finish()

    def _apply_kernel_jail(
        self,
        killswitch: bool = False,
        force: bool = False,
    ) -> tuple[str, str, dict[str, Any]]:
        """Устанавливает правила nftables (с fallback на iptables) в WSL2."""
        try:
            import node_isolate_manager
            success = node_isolate_manager.apply_wsl_isolation(
                port=self.socks_port,
                http_port=self.http_port,
                block_bypass_ports=self.block_bypass_ports,
                killswitch=killswitch,
                force=force,
            )
            if not success:
                raise RuntimeError("node_isolate_manager.apply_wsl_isolation вернул False")
            return "ok", f"Сетевая изоляция WSL2 успешно применена (killswitch={killswitch})", {
                "socks_port": self.socks_port,
                "http_port": self.http_port,
                "killswitch": killswitch,
            }
        except ImportError:
            # Автономная реализация без прямого импорта
            nft_cmds = [
                "nft add table inet herdr_filter 2>/dev/null || true",
                "nft 'add chain inet herdr_filter output { type filter hook output priority filter; policy accept; }' 2>/dev/null || true",
                "nft flush chain inet herdr_filter",
            ]
            for bp in self.block_bypass_ports:
                if bp not in (self.socks_port, self.http_port):
                    nft_cmds.append(
                        f"nft add rule inet herdr_filter output ip daddr 127.0.0.1 tcp dport {bp} counter reject"
                    )
            nft_cmds.append("nft add rule inet herdr_filter output oifname { \"lo\", \"loopback0\" } counter accept")
            nft_cmds.append("nft add rule inet herdr_filter output counter reject")

            cmd = " && ".join(nft_cmds)
            rc, out, err = self.run_wsl(cmd, user="root", distro=self.distro)
            if rc != 0:
                raise RuntimeError(f"Ошибка применения nftables: {err}")
            return "ok", "Сетевая изоляция применена автономно", {}

    def _install_persistent_boot(self) -> tuple[str, str, dict[str, Any]]:
        """Закрепляет автозапуск правил изоляции при старте системы."""
        try:
            import node_isolate_manager
            success = node_isolate_manager.install_persistent_wsl_isolation(
                port=self.socks_port,
                http_port=self.http_port,
                block_bypass_ports=self.block_bypass_ports,
            )
            if not success:
                return "skipped", "Персистентные правила не удалось зафиксировать", {}
            return "ok", "Персистентный запуск изоляции зарегистрирован в /etc/wsl.conf", {}
        except Exception as ex:
            return "skipped", f"Пропущена регистрация персистентности: {ex}", {}

    def _verify_isolation_status(self) -> tuple[str, str, dict[str, Any]]:
        """Проверяет наличие активных правил изоляции в ядре Linux."""
        if self.dry_run:
            return "dry_run", "[dry-run] Статус изоляции ядра симулирован (АКТИВНА)", {"active": True, "nftables": True, "iptables": True}

        rc, out, _ = self.run_wsl("nft list table inet herdr_filter 2>/dev/null", distro=self.distro)
        nft_active = (rc == 0 and "chain output" in out)

        rc2, out2, _ = self.run_wsl("iptables -C OUTPUT -j HERDR_ISOLATE 2>/dev/null", distro=self.distro)
        iptables_active = (rc2 == 0)

        active = nft_active or iptables_active
        msg = f"Статус изоляции ядра: {'АКТИВНА' if active else 'ОТКЛЮЧЕНА'} (nftables={nft_active}, iptables={iptables_active})"
        return "ok", msg, {"active": active, "nftables": nft_active, "iptables": iptables_active}

    def _verify_zero_leak(self) -> tuple[str, str, dict[str, Any]]:
        """Проверяет утечки: прямой запрос без прокси ДОЛЖЕН блокироваться ядром."""
        if self.dry_run:
            return "dry_run", "[dry-run] Zero-Leak симулирован: прямой несанкционированный трафик отсечен", {"rc": 1}

        direct_cmd = "curl -s --connect-timeout 2 http://1.1.1.1"
        rc, out, err = self.run_wsl(direct_cmd, distro=self.distro)
        blocked = (rc != 0)

        if not blocked:
            return "failed", "ОБНАРУЖЕНА УТЕЧКА (Leak)! Прямой запрос без прокси прошел через ядро!", {"rc": rc, "out": out}

        return "ok", "Zero-Leak подтвержден: прямой несанкционированный трафик физически отсекается ядром", {"rc": rc}

    def _remove_jail(self) -> tuple[str, str, dict[str, Any]]:
        """Снимает ограничения для экстренного прямого доступа."""
        try:
            import node_isolate_manager
            node_isolate_manager.remove_wsl_isolation()
            return "ok", "Сетевая изоляция WSL успешно снята, прямой доступ восстановлен", {}
        except ImportError:
            cmd = (
                "nft flush chain inet herdr_filter 2>/dev/null || true; "
                "nft delete table inet herdr_filter 2>/dev/null || true; "
                "iptables -D OUTPUT -j HERDR_ISOLATE 2>/dev/null || true"
            )
            self.run_wsl(cmd, user="root", distro=self.distro)
            return "ok", "Сетевая изоляция снята автономно", {}
