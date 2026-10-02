"""Действие по установке и настройке Claude Code CLI и Claude Desktop Linux GUI в WSL2.

Реализует пошаговый протокол с обходом всех известных подводных камней:
1. Конфигурация %USERPROFILE%/.wslconfig (mirrored mode, autoProxy=false)
2. Настройка сетевых переменных окружения прокси (/etc/profile.d/herdr_claude_env.sh)
3. Мост xdg-open для автоматического открытия OAuth в браузере Windows
4. Проверка и установка Node.js LTS и Claude Code CLI (@anthropic-ai/claude-code)
5. Создание Linux GUI обертки claude-gui (--no-sandbox)
6. Создание бесшумных Windows ярлыков на Рабочем столе (.bat, .vbs, .lnk)
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

from actions.base import ActionResult, BaseAction

logger = logging.getLogger("herdr.actions.install_claude_wsl")


class InstallClaudeWslAction(BaseAction):
    name = "install_claude_wsl"
    description = "Установка и настройка Claude Code CLI и Claude Desktop GUI в WSL2"

    def __init__(
        self,
        socks_port: int = 1015,
        http_port: int = 11015,
        distro: str = "Ubuntu",
        wsl_user: str = "default",
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.socks_port = socks_port
        self.http_port = http_port
        self.distro = distro
        self.wsl_user = wsl_user

    def run(
        self,
        install_node: bool = True,
        install_npm_cli: bool = True,
        create_shortcuts: bool = True,
        shutdown_wsl_on_config_change: bool = False,
        **kwargs: Any,
    ) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        try:
            # Шаг 1: Конфигурация .wslconfig
            self.execute_step(
                result,
                "configure_wslconfig",
                self._configure_wslconfig,
                shutdown_wsl_on_config_change,
            )

            # Шаг 2: Системный прокси в WSL2
            self.execute_step(
                result,
                "configure_wsl_proxy_env",
                self._configure_wsl_proxy_env,
            )

            # Шаг 3: Мост xdg-open для OAuth
            self.execute_step(
                result,
                "setup_xdg_open_bridge",
                self._setup_xdg_open_bridge,
            )

            # Шаг 4: Node.js и Claude Code CLI
            if install_node or install_npm_cli:
                self.execute_step(
                    result,
                    "install_node_and_cli",
                    self._install_node_and_cli,
                    install_node,
                    install_npm_cli,
                )

            # Шаг 5: Обертка GUI с --no-sandbox
            self.execute_step(
                result,
                "setup_claude_gui_wrapper",
                self._setup_claude_gui_wrapper,
            )

            # Шаг 6: Ярлыки на Рабочем столе
            if create_shortcuts:
                self.execute_step(
                    result,
                    "create_desktop_shortcuts",
                    self._create_desktop_shortcuts,
                )

        except Exception as exc:
            result.add_error(f"Прерывание выполнения действия: {exc}")

        return result.finish()

    def _configure_wslconfig(self, shutdown_wsl: bool = False) -> tuple[str, str, dict[str, Any]]:
        """Проверяет и обновляет %USERPROFILE%/.wslconfig."""
        user_profile = os.environ.get("USERPROFILE", str(Path.home()))
        wslconfig_path = Path(user_profile) / ".wslconfig"

        required_section_mirrored = "networkingMode=mirrored"
        required_autoproxy_false = "autoProxy=false"

        current_content = ""
        if wslconfig_path.exists():
            try:
                current_content = wslconfig_path.read_text(encoding="ascii", errors="ignore")
            except Exception:
                current_content = ""

        needs_update = (
            required_section_mirrored not in current_content
            or required_autoproxy_false not in current_content
        )

        if not needs_update:
            return "skipped", "Файл .wslconfig уже содержит корректные параметры", {"path": str(wslconfig_path)}

        new_content = (
            "[wsl2]\n"
            "networkingMode=mirrored\n\n"
            "[experimental]\n"
            "autoProxy=false\n"
        ).replace("\n", "\r\n")

        if self.dry_run:
            return "dry_run", f"Планируется запись правильной конфигурации в {wslconfig_path}", {"path": str(wslconfig_path)}

        wslconfig_path.write_text(new_content, encoding="ascii")

        restart_note = ""
        if shutdown_wsl:
            self.run_cmd(["wsl.exe", "--shutdown"])
            restart_note = " (WSL перезапущен через wsl --shutdown)"

        return "ok", f"Конфигурация .wslconfig обновлена{restart_note}", {"path": str(wslconfig_path)}

    def _configure_wsl_proxy_env(self) -> tuple[str, str, dict[str, Any]]:
        """Записывает системные переменные прокси в /etc/profile.d/herdr_claude_env.sh."""
        env_script = (
            f"export HTTP_PROXY='http://127.0.0.1:{self.http_port}'\n"
            f"export HTTPS_PROXY='http://127.0.0.1:{self.http_port}'\n"
            f"export ALL_PROXY='socks5h://127.0.0.1:{self.socks_port}'\n"
            f"export http_proxy='http://127.0.0.1:{self.http_port}'\n"
            f"export https_proxy='http://127.0.0.1:{self.http_port}'\n"
            f"export all_proxy='socks5h://127.0.0.1:{self.socks_port}'\n"
            f"export ELECTRON_OZONE_PLATFORM_HINT='auto'\n"
            f"export GDK_SCALE=2\n"
            f"export GDK_DPI_SCALE=0.5\n"
            f"export QT_SCALE_FACTOR=2\n"
            f"export XCURSOR_SIZE=48\n"
        )
        cmd = f"cat << 'EOF' > /etc/profile.d/herdr_claude_env.sh\n{env_script}EOF\nchmod 644 /etc/profile.d/herdr_claude_env.sh"

        rc, out, err = self.run_wsl(cmd, user="root", distro=self.distro)
        if rc != 0:
            raise RuntimeError(f"Ошибка записи переменных прокси в WSL: {err}")

        return "ok", f"Системные переменные прокси настроены (:SOCKS={self.socks_port}, :HTTP={self.http_port})", {
            "socks_port": self.socks_port,
            "http_port": self.http_port,
        }

    def _setup_xdg_open_bridge(self) -> tuple[str, str, dict[str, Any]]:
        """Создает мост xdg-open в /usr/local/bin/xdg-open для открытия Windows-браузера."""
        bridge_script = (
            "#!/bin/bash\n"
            'TARGET="$1"\n'
            'if [ -z "$TARGET" ]; then\n'
            "    exit 0\n"
            "fi\n"
            'powershell.exe -NoProfile -NonInteractive -Command "Start-Process \'$TARGET\'" >/dev/null 2>&1 &\n'
            "exit 0\n"
        )
        cmd = (
            f"cat << 'EOF' > /usr/local/bin/xdg-open\n"
            f"{bridge_script}"
            f"EOF\n"
            f"chmod +x /usr/local/bin/xdg-open"
        )
        rc, out, err = self.run_wsl(cmd, user="root", distro=self.distro)
        if rc != 0:
            raise RuntimeError(f"Не удалось установить мост xdg-open: {err}")

        return "ok", "Мост xdg-open успешно установлен в /usr/local/bin/xdg-open", {}

    def _install_node_and_cli(
        self,
        install_node: bool,
        install_npm_cli: bool,
    ) -> tuple[str, str, dict[str, Any]]:
        """Проверяет и при необходимости устанавливает Node.js и Claude Code CLI."""
        details: dict[str, Any] = {}

        # 1. Проверяем node
        rc_node, node_ver, _ = self.run_wsl("node -v 2>/dev/null", distro=self.distro)
        details["node_version"] = node_ver if rc_node == 0 else "not installed"

        if (rc_node != 0 or not node_ver.startswith("v")) and install_node:
            logger.info("Установка Node.js LTS в WSL2...")
            install_cmd = (
                "apt-get update -y && "
                "apt-get install -y curl ca-certificates build-essential && "
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && "
                "apt-get install -y nodejs"
            )
            rc, out, err = self.run_wsl(install_cmd, user="root", distro=self.distro, timeout=120.0)
            if rc != 0:
                raise RuntimeError(f"Ошибка установки Node.js в WSL: {err}")
            rc_node, node_ver, _ = self.run_wsl("node -v", distro=self.distro)
            details["node_version"] = node_ver

        # 2. Проверяем npm claude
        rc_claude, claude_ver, _ = self.run_wsl("claude --version 2>/dev/null", distro=self.distro)
        details["claude_version"] = claude_ver if rc_claude == 0 else "not installed"

        if rc_claude != 0 and install_npm_cli:
            logger.info("Установка @anthropic-ai/claude-code через npm...")
            install_npm_cmd = "npm install -g @anthropic-ai/claude-code"
            rc, out, err = self.run_wsl(install_npm_cmd, user="root", distro=self.distro, timeout=90.0)
            if rc != 0:
                raise RuntimeError(f"Ошибка установки @anthropic-ai/claude-code: {err}")
            rc_claude, claude_ver, _ = self.run_wsl("claude --version 2>/dev/null", distro=self.distro)
            details["claude_version"] = claude_ver

        return "ok", f"Node.js ({details.get('node_version')}) и Claude CLI ({details.get('claude_version')}) готовы", details

    def _setup_claude_gui_wrapper(self) -> tuple[str, str, dict[str, Any]]:
        """Устанавливает /usr/local/bin/claude-gui со стабильным X11 рендерингом и очисткой замков."""
        wrapper_script = (
            "#!/bin/bash\n"
            "source /etc/profile.d/herdr_claude_env.sh 2>/dev/null\n"
            "\n"
            "# Clean stale SingletonLock if process is dead\n"
            'LOCK_FILE="/home/79251/.config/Claude/SingletonLock"\n'
            'if [ -L "$LOCK_FILE" ]; then\n'
            '    TARGET=$(readlink "$LOCK_FILE")\n'
            '    PID=$(echo "$TARGET" | grep -oE "[0-9]+$")\n'
            '    if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then\n'
            "        rm -f /home/79251/.config/Claude/Singleton* 2>/dev/null\n"
            "    fi\n"
            "fi\n"
            "\n"
            'if command -v claude-desktop >/dev/null 2>&1; then\n'
            '    exec claude-desktop --no-sandbox --ozone-platform=x11 --password-store=basic "$@"\n'
            "else\n"
            '    echo "claude-desktop не найден в PATH. Убедитесь, что deb-пакет установлен." >&2\n'
            "    exit 1\n"
            "fi\n"
        )
        b64_content = base64.b64encode(wrapper_script.encode("utf-8")).decode("ascii")
        cmd = f"echo '{b64_content}' | base64 -d > /usr/local/bin/claude-gui && chmod +x /usr/local/bin/claude-gui"
        rc, out, err = self.run_wsl(cmd, user="root", distro=self.distro)
        if rc != 0:
            raise RuntimeError(f"Не удалось установить обертку claude-gui: {err}")

        return "ok", "Обертка claude-gui (--no-sandbox, --ozone-platform=x11) установлена в /usr/local/bin/claude-gui", {}


    def _create_desktop_shortcuts(self) -> tuple[str, str, dict[str, Any]]:
        """Создает надежные Windows ярлыки на рабочем столе."""
        user_profile = os.environ.get("USERPROFILE", str(Path.home()))
        app_dir = Path(__file__).resolve().parent.parent
        desktop_dir = Path(user_profile) / "Desktop"
        if not desktop_dir.exists():
            desktop_dir = Path.home() / "Desktop"

        created_files = []

        # 1. Launcher для CLI (ASCII/CRLF) в рабочей папке
        cli_bat = app_dir / "run_claude_cli.bat"
        cli_bat_content = (
            "@echo off\r\n"
            "title Claude Code (WSL)\r\n"
            "where wt.exe >nul 2>&1\r\n"
            "if %ERRORLEVEL% equ 0 (\r\n"
            f"    wt.exe --title \"Claude Code (WSL)\" wsl.exe -d {self.distro} bash -lic \"claude %*\"\r\n"
            "    exit /b\r\n"
            ")\r\n"
            f"wsl.exe -d {self.distro} bash -lic \"claude %*\"\r\n"
            "if %ERRORLEVEL% neq 0 pause\r\n"
        )
        if not self.dry_run:
            cli_bat.write_text(cli_bat_content, encoding="ascii")
        created_files.append(str(cli_bat))

        # 2. VBScript для бесшумного запуска GUI (Zero-Flash)
        vbs_launcher = app_dir / "run_claude_gui.vbs"
        vbs_content = (
            "Set WshShell = CreateObject(\"WScript.Shell\")\r\n"
            f"WshShell.Run \"wsl.exe -d {self.distro} bash -lic \"\"claude-gui\"\"\", 0, False\r\n"
        )
        if not self.dry_run:
            vbs_launcher.write_text(vbs_content, encoding="ascii")
        created_files.append(str(vbs_launcher))

        # 3. Поиск иконки приложения
        icon_path = app_dir / "claude.ico"
        if not icon_path.exists():
            icon_path = Path(user_profile) / "AppData" / "Local" / "AnthropicClaude" / "claude.ico"

        # 4. Создание ярлыков на рабочем столе (.lnk)
        ps_shortcut_script = f"""
        $wsh = New-Object -ComObject WScript.Shell
        
        # GUI Ярлык
        $lnkGui = "{desktop_dir}\\Claude Desktop (WSL).lnk"
        $scG = $wsh.CreateShortcut($lnkGui)
        $scG.TargetPath = "$env:SystemRoot\\System32\\wscript.exe"
        $scG.Arguments = "`"{vbs_launcher}`""
        $scG.WorkingDirectory = "{app_dir}"
        $scG.WindowStyle = 7
        $scG.Description = "Claude Desktop Linux GUI (WSL2 / WSLg)"
        if (Test-Path "{icon_path}") {{
            $scG.IconLocation = "{icon_path},0"
        }}
        $scG.Save()

        # CLI Ярлык
        $lnkCli = "{desktop_dir}\\Claude Code (WSL).lnk"
        $scC = $wsh.CreateShortcut($lnkCli)
        $scC.TargetPath = "$env:SystemRoot\\System32\\cmd.exe"
        $scC.Arguments = "/c `"`"{cli_bat}`"`""
        $scC.WorkingDirectory = "{app_dir}"
        $scC.WindowStyle = 1
        $scC.Description = "Claude Code CLI Terminal (WSL2)"
        if (Test-Path "{icon_path}") {{
            $scC.IconLocation = "{icon_path},0"
        }}
        $scC.Save()
        """
        self.run_powershell(ps_shortcut_script)
        created_files.append(str(desktop_dir / "Claude Desktop (WSL).lnk"))
        created_files.append(str(desktop_dir / "Claude Code (WSL).lnk"))

        return "ok", f"Созданы надежные ярлыки на рабочем столе ({len(created_files)} шт.)", {"files": created_files}

