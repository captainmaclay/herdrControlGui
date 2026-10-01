"""Действие диагностики и автоисцеления мигания Killswitch (Killswitch Auto-Heal Action).

Устраняет циклические перезапуски (мигание каждые 1.5 секунды):
1. Аудит процессов и владельцев портов (Get-NetTCPConnection / netstat)
2. Очистка устаревших общих конфигураций (runtime/xray-config.json)
3. Завершение зомби-процессов xray.exe и осиротевших супервизоров
4. Сброс кэша состояния изоляции node_isolate_manager
5. Контрольный замер стабильности (детектор дребезга сокета)
"""

from __future__ import annotations

import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

from actions.base import ActionResult, BaseAction

logger = logging.getLogger("herdr.actions.killswitch_heal")

VLESS2SOCKS_DIR = Path(r"C:\MyFiles\vless2socks")


class KillswitchHealAction(BaseAction):
    name = "killswitch_heal"
    description = "Диагностика и автоисцеление циклического мигания Killswitch"

    def __init__(
        self,
        target_port: int = 1015,
        vless_dir: Path | str = VLESS2SOCKS_DIR,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.target_port = target_port
        self.vless_dir = Path(vless_dir)

    def run(
        self,
        force_kill_rogues: bool = False,
        remove_legacy_configs: bool = True,
        stability_samples: int = 5,
        sample_interval: float = 0.3,
        **kwargs: Any,
    ) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        try:
            # Шаг 1: Проверка владельца сокета
            self.execute_step(
                result,
                "inspect_port_owner",
                self._inspect_port_owner,
            )

            # Шаг 2: Очистка опасных несегментированных конфигов
            if remove_legacy_configs:
                self.execute_step(
                    result,
                    "clean_legacy_configs",
                    self._clean_legacy_configs,
                )

            # Шаг 3: Проверка начального дребезга сокета
            is_flapping, initial_samples = self._check_flapping(stability_samples, sample_interval)
            
            # Шаг 4: Ликвидация зомби-процессов если обнаружен дребезг или задан force_kill_rogues
            if is_flapping or force_kill_rogues:
                self.execute_step(
                    result,
                    "kill_rogue_processes",
                    self._kill_rogue_processes,
                )

            # Шаг 5: Сброс кэша изоляции
            self.execute_step(
                result,
                "reset_isolation_cache",
                self._reset_isolation_cache,
            )

            # Шаг 6: Контрольный замер стабильности (flapping verification)
            self.execute_step(
                result,
                "verify_flapping_stability",
                self._verify_flapping_stability,
                stability_samples,
                sample_interval,
            )

        except Exception as exc:
            result.add_error(f"Прерывание выполнения исцеления Killswitch: {exc}")

        return result.finish()

    def _is_port_open(self, port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
        """Быстрая проверка открытости TCP-порта."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return s.connect_ex((host, port)) == 0
        except Exception:
            return False

    def _check_flapping(self, samples: int, interval: float) -> tuple[bool, list[bool]]:
        """Проверяет наличие дребезга: если значения чередуются (есть и True, и False)."""
        history: list[bool] = []
        for _ in range(samples):
            history.append(self._is_port_open(self.target_port))
            time.sleep(interval)
        is_flapping = (True in history and False in history)
        return is_flapping, history

    def _inspect_port_owner(self) -> tuple[str, str, dict[str, Any]]:
        """Определяет, какой процесс занимает сокет."""
        is_open = self._is_port_open(self.target_port)
        details: dict[str, Any] = {"port": self.target_port, "is_open": is_open, "owners": []}

        ps_script = f"""
        Get-NetTCPConnection -LocalPort {self.target_port} -ErrorAction SilentlyContinue | 
        ForEach-Object {{
            $pid_val = $_.OwningProcess
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pid_val" -ErrorAction SilentlyContinue
            [PSCustomObject]@{{
                PID = $pid_val
                Name = $proc.Name
                ParentPID = $proc.ParentProcessId
                Cmd = $proc.CommandLine
            }}
        }} | ConvertTo-Json -Compress
        """
        rc, out, _ = self.run_powershell(ps_script)
        if rc == 0 and out:
            import json
            try:
                parsed = json.loads(out)
                if isinstance(parsed, dict):
                    parsed = [parsed]
                details["owners"] = parsed
            except Exception:
                details["raw_owners"] = out

        msg = f"Порт {self.target_port}: {'ОТКРЫТ' if is_open else 'ЗАКРЫТ'}"
        if details["owners"]:
            msg += f", владельцы: {[o.get('Name') + ' (PID ' + str(o.get('PID')) + ')' for o in details['owners']]}"
        return "ok", msg, details

    def _clean_legacy_configs(self) -> tuple[str, str, dict[str, Any]]:
        """Удаляет старый общий файл xray-config.json из runtime, провоцирующий перехват порта."""
        runtime_dir = self.vless_dir / "runtime"
        removed_files: list[str] = []

        # Общий несегментированный файл конфига - главная причина мигания!
        legacy_config = runtime_dir / "xray-config.json"
        if legacy_config.exists():
            if not self.dry_run:
                try:
                    legacy_config.unlink()
                    removed_files.append(str(legacy_config))
                except Exception as ex:
                    logger.warning(f"Не удалось удалить {legacy_config}: {ex}")
            else:
                removed_files.append(f"[dry-run] {legacy_config}")

        msg = (
            f"Удален устаревший несегментированный конфиг {legacy_config.name}"
            if removed_files
            else "Опасных несегментированных конфигов не обнаружено"
        )
        return "ok", msg, {"removed": removed_files}

    def _kill_rogue_processes(self) -> tuple[str, str, dict[str, Any]]:
        """Находит и завершает зомби-процессы xray.exe, удерживающие сокет."""
        terminated: list[dict[str, Any]] = []

        ps_find_and_kill = f"""
        $pidsToKill = @()
        Get-NetTCPConnection -LocalPort {self.target_port} -ErrorAction SilentlyContinue |
        ForEach-Object {{
            $pid_val = $_.OwningProcess
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pid_val" -ErrorAction SilentlyContinue
            if ($proc -and ($proc.Name -like "*xray*" -or $proc.CommandLine -like "*xray*")) {{
                $pidsToKill += $pid_val
            }}
        }}
        $pidsToKill = $pidsToKill | Select-Object -Unique
        foreach ($p in $pidsToKill) {{
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            Write-Output "KILLED:$p"
        }}
        """
        if not self.dry_run:
            rc, out, _ = self.run_powershell(ps_find_and_kill)
            if rc == 0 and out:
                for line in out.splitlines():
                    if "KILLED:" in line:
                        kpid = line.split("KILLED:")[1].strip()
                        terminated.append({"pid": kpid, "reason": "rogue_xray_on_target_port"})
        else:
            terminated.append({"pid": "dry_run", "reason": "planned_termination"})

        msg = f"Завершено {len(terminated)} зомби-процессов xray" if terminated else "Зомби-процессов xray не обнаружено"
        return "ok", msg, {"terminated": terminated}

    def _reset_isolation_cache(self) -> tuple[str, str, dict[str, Any]]:
        """Сбрасывает кэш изоляции в node_isolate_manager."""
        try:
            import node_isolate_manager
            node_isolate_manager._LAST_APPLIED_ISOLATION_KEY = None
            return "ok", "Кэш _LAST_APPLIED_ISOLATION_KEY успешно сброшен", {}
        except ImportError:
            return "skipped", "Модуль node_isolate_manager не в sys.path (пропущено)", {}
        except Exception as ex:
            return "failed", f"Ошибка сброса кэша изоляции: {ex}", {}

    def _verify_flapping_stability(
        self,
        samples: int,
        interval: float,
    ) -> tuple[str, str, dict[str, Any]]:
        """Проверяет стабильность состояния сокета: состояние не должно меняться во время замера."""
        history: list[bool] = []
        for _ in range(samples):
            history.append(self._is_port_open(self.target_port))
            time.sleep(interval)

        # Стабильно, если все значения True либо все False
        is_stable = (all(history) or not any(history))
        final_state = "ОТКРЫТ (Online)" if history[-1] else "ЗАКРЫТ (Killswitch Active)"

        if not is_stable:
            return "failed", f"ОБНАРУЖЕНО МИГАНИЕ СОКЕТА! Замеры: {history}", {"history": history, "stable": False}

        return "ok", f"Сокет стабилен ({final_state}), мигание отсутствует. Замеры: {history}", {
            "history": history,
            "stable": True,
            "final_state": final_state,
        }
