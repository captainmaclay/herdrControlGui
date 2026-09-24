"""Модуль управления сетевым подключением Anthropic Claude Code с поддержкой Killswitch.

Обеспечивает:
- Настройку выделенного прокси для Claude Code (по умолчанию 127.0.0.1:1085).
- Флаг Killswitch (по умолчанию True): при неактивности выбранного прокси Claude
  изолируется через blackhole (127.0.0.1:1), исключая утечку пакетов с оригинального IP.
- Автоматический экспорт и импорт настроек (host, port, killswitch) в бэкапах (.hbak).
- Мониторинг статуса прокси в proxies.json и портов в реальном времени.
- Автоматическое снятие Killswitch и восстановление туннеля, когда прокси становится активным.
- Синхронизацию конфигурации в Windows (~/.claude/settings.json) и WSL2.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import requests
import proxy_manager
import settings_manager

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent

# WSL2 Paths
def get_wsl_user(distro: str = "Ubuntu") -> str:
    """Определяет пользователя WSL2."""
    return (os.environ.get("WSL_USER") or os.environ.get("USERNAME") or "default").strip()

WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu")
WSL_USER = get_wsl_user(WSL_DISTRO)

# Blackhole address to strictly drop packets without leaking ISP IP
BLACKHOLE_HTTP = "http://127.0.0.1:1"
BLACKHOLE_SOCKS = "socks5h://127.0.0.1:1"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com"


def get_claude_settings_files() -> list[Path]:
    """Возвращает список существующих или доступных файлов settings.json для Claude."""
    files: list[Path] = []
    
    # 1. Windows: ~/.claude/settings.json
    try:
        win_claude_dir = Path.home() / ".claude"
        win_claude_file = win_claude_dir / "settings.json"
        files.append(win_claude_file)
    except Exception:
        pass

    # 2. WSL2: \\wsl$\<distro>\home\<user>\.claude\settings.json
    try:
        wsl_claude_dir = Path(rf"\\wsl$\{WSL_DISTRO}\home\{WSL_USER}\.claude")
        wsl_claude_file = wsl_claude_dir / "settings.json"
        files.append(wsl_claude_file)
    except Exception:
        pass

    return files


def check_port_accessible(host: str, port: int, timeout: float = 1.0) -> bool:
    """Быстрая проверка открытости TCP-порта."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def get_claude_config() -> dict[str, Any]:
    """Возвращает текущие настройки подключения Claude из settings_manager."""
    return {
        "host": settings_manager.get_claude_proxy_host(),
        "port": settings_manager.get_claude_proxy_port(),
        "killswitch": settings_manager.get_claude_killswitch(),
    }


def save_claude_config(host: str, port: int, killswitch: bool) -> None:
    """Сохраняет настройки подключения Claude в settings.json и синхронизирует с Claude."""
    settings_manager.set_claude_proxy_settings(host, port, killswitch)
    # Гарантируем регистрацию в списке прокси
    ensure_claude_proxy_in_list(host, port)
    # Немедленно производим проверку и применение состояния
    probe_claude_route()


def ensure_claude_proxy_in_list(host: str, port: int) -> dict[str, Any] | None:
    """Проверяет наличие выбранного прокси в proxies.json; если отсутствует — регистрирует."""
    try:
        proxies = proxy_manager.load_proxies()
        for p in proxies:
            if p.get("port") == port and (
                p.get("host") == host or (host in ("127.0.0.1", "localhost") and p.get("host") in ("127.0.0.1", "localhost"))
            ):
                return p

        # Добавляем выделенный прокси для Claude
        new_entry = proxy_manager.add_proxy(host=host, port=port, label=f"Claude: SOCKS5 :{port}")
        return new_entry
    except Exception:
        return None


def apply_claude_settings_to_file(
    filepath: Path,
    host: str,
    port: int,
    killswitch_engaged: bool,
) -> bool:
    """Записывает настройки прокси / блокировки Killswitch в указанный файл settings.json."""
    try:
        data: dict[str, Any] = {}
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        data = {}
            except Exception:
                data = {}
        else:
            # Создаем директорию, если она существует в родительской иерархии
            filepath.parent.mkdir(parents=True, exist_ok=True)

        if not isinstance(data.get("env"), dict):
            data["env"] = {}

        if killswitch_engaged:
            # Блокировка: заворачиваем трафик на несуществующий blackhole
            data["env"]["HTTPS_PROXY"] = BLACKHOLE_HTTP
            data["env"]["HTTP_PROXY"] = BLACKHOLE_HTTP
            data["env"]["ALL_PROXY"] = BLACKHOLE_SOCKS
        else:
            http_port = 10000 + int(port)
            data["env"]["HTTPS_PROXY"] = f"http://{host}:{http_port}"
            data["env"]["HTTP_PROXY"] = f"http://{host}:{http_port}"
            data["env"]["ALL_PROXY"] = f"socks5h://{host}:{port}"

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def apply_claude_proxy_sync(host: str, port: int, killswitch_engaged: bool) -> None:
    """Синхронизирует настройки прокси или статус Killswitch во всех файлах Claude."""
    for fp in get_claude_settings_files():
        apply_claude_settings_to_file(fp, host, port, killswitch_engaged)


def is_proxy_active_in_list(
    host: str,
    port: int,
    timeout: float = 0.5,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Проверяет, активен ли выбранный прокси в списке proxies.json и на живом порту.
    
    Возвращает (is_active, status_reason, proxy_entry).
    - Если порт открыт -> проверяет/обновляет статус в proxies.json до 'online' и возвращает True, "online"
    - Если порт закрыт -> возвращает False, "port_closed"
    """
    proxies = proxy_manager.load_proxies()
    found_proxy = None
    for p in proxies:
        if p.get("port") == port and (
            p.get("host") == host or (host in ("127.0.0.1", "localhost") and p.get("host") in ("127.0.0.1", "localhost"))
        ):
            found_proxy = p
            break

    # Проверяем живую доступность порта
    port_accessible = check_port_accessible(host, port, timeout=timeout)

    if port_accessible:
        if not found_proxy:
            found_proxy = ensure_claude_proxy_in_list(host, port)
        if found_proxy:
            found_proxy["status"] = "online"
            # Сохраняем обновленный статус в proxies.json
            try:
                all_p = proxy_manager.load_proxies()
                updated = False
                for i, ap in enumerate(all_p):
                    if ap.get("port") == port:
                        all_p[i]["status"] = "online"
                        updated = True
                        break
                if not updated:
                    all_p.append(found_proxy)
                proxy_manager.save_proxies(all_p)
            except Exception:
                pass
        return True, "online", found_proxy

    if not found_proxy:
        return False, "not_in_list", None

    status = str(found_proxy.get("status", "unknown")).lower()
    if status == "online":
        found_proxy["status"] = "offline"
        try:
            all_p = proxy_manager.load_proxies()
            for i, ap in enumerate(all_p):
                if ap.get("port") == port:
                    all_p[i]["status"] = "offline"
                    break
            proxy_manager.save_proxies(all_p)
        except Exception:
            pass

    return False, "port_closed", found_proxy


def probe_claude_route(
    host: str | None = None,
    port: int | None = None,
    killswitch: bool | None = None,
    fast: bool = False,
) -> dict[str, Any]:
    """Проверяет состояние подключения Claude и управляет состоянием Killswitch.
    
    Для Claude Code авто-переключение не применяется: если выбранный порт (например, 1085)
    недоступен или сокет закрыт, строго активируется Killswitch (трафик глушится через blackhole),
    предотвращая любые утечки на прямой IP.
    Когда прокси на этом порту поднимается:
      - Killswitch автоматически отключается, восстанавливая нормальную работу.
    """
    if host is None:
        host = settings_manager.get_claude_proxy_host()
    if port is None:
        port = settings_manager.get_claude_proxy_port()
    if killswitch is None:
        killswitch = settings_manager.get_claude_killswitch()

    timeout = 0.2 if fast else 0.5
    is_active, reason, proxy_entry = is_proxy_active_in_list(host, port, timeout=timeout)
    http_port = 10000 + int(port)

    if not is_active:
        if killswitch:
            # Срабатывает Killswitch!
            apply_claude_proxy_sync(host, port, killswitch_engaged=True)
            reason_desc = {
                "not_in_list": f"Прокси {host}:{port} отсутствует в списке прокси",
                "offline": f"Прокси {host}:{port} не в сети (офлайн)",
                "unknown": f"Статус прокси {host}:{port} неизвестен (не проверен)",
                "port_closed": f"Порт {host}:{port} закрыт",
            }.get(reason, f"Прокси {host}:{port} неактивен ({reason})")

            return {
                "online": False,
                "killswitch_engaged": True,
                "killswitch_enabled": True,
                "host": host,
                "port": port,
                "http_port": http_port,
                "status_text": f"🛡️ KILLSWITCH АКТИВЕН: {reason_desc}",
                "route": f"http://{host}:{http_port} (ЗАБЛОКИРОВАН)",
                "ip": proxy_entry.get("ip", "-") if proxy_entry else "-",
                "country": proxy_entry.get("country", "undefined") if proxy_entry else "undefined",
                "latency_ms": None,
                "details": f"Killswitch защищает от утечки IP: Anthropic изолирован до восстановления :{port}.",
                "reason": reason,
                "failover_occurred": False,
            }
        else:
            # Killswitch выключен пользователем
            apply_claude_proxy_sync(host, port, killswitch_engaged=False)
            return {
                "online": False,
                "killswitch_engaged": False,
                "killswitch_enabled": False,
                "host": host,
                "port": port,
                "http_port": http_port,
                "status_text": f"✕ Прокси {host}:{port} не в сети (Killswitch выключен)",
                "route": f"http://{host}:{http_port}",
                "ip": proxy_entry.get("ip", "-") if proxy_entry else "-",
                "country": proxy_entry.get("country", "undefined") if proxy_entry else "undefined",
                "latency_ms": None,
                "details": f"Прокси недоступен, но Killswitch деактивирован пользователем.",
                "reason": reason,
                "failover_occurred": False,
            }

    # Прокси активен! Снимаем Killswitch и восстанавливаем рабочий туннель
    apply_claude_proxy_sync(host, port, killswitch_engaged=False)
    exit_ip = proxy_entry.get("ip", "-") if proxy_entry else "-"
    country = proxy_entry.get("country", "undefined") if proxy_entry else "undefined"
    stored_lat = proxy_entry.get("latency_ms") if proxy_entry else None

    # Быстрый замер связи до API Anthropic через HTTP CONNECT прокси
    t0 = time.time()
    lat = stored_lat
    if not fast:
        try:
            proxy_url = f"http://{host}:{http_port}"
            resp = requests.get(
                ANTHROPIC_ENDPOINT,
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=2.0,
                headers={"User-Agent": "HerdrClaudeRouter/1.0"}
            )
            lat = int((time.time() - t0) * 1000)
        except Exception:
            if lat is None:
                lat = 45
    else:
        if lat is None:
            lat = 45

    return {
        "online": True,
        "killswitch_engaged": False,
        "killswitch_enabled": killswitch,
        "host": host,
        "port": port,
        "http_port": http_port,
        "status_text": f"● Прокси активен ({host}:{port} • HTTP :{http_port})",
        "route": f"http://{host}:{http_port} (SOCKS5 :{port})",
        "ip": exit_ip,
        "country": country,
        "latency_ms": lat,
        "details": f"Трафик Claude Code туннелируется через {host}:{port} (Killswitch вооружен)",
        "reason": "online",
        "failover_occurred": False,
    }


def sync_after_restore() -> None:
    """Вызывается после восстановления бэкапа для немедленного применения настроек."""
    h = settings_manager.get_claude_proxy_host()
    p = settings_manager.get_claude_proxy_port()
    ensure_claude_proxy_in_list(h, p)
    probe_claude_route()


def sync_aionui_bridge(oauth_file: Path | str | None = None) -> dict[str, Any]:
    """Выполняет комплексную синхронизацию Claude Code с AionUi через aionui_claude_bridge."""
    try:
        import aionui_claude_bridge
        return aionui_claude_bridge.sync_all(oauth_file=oauth_file)
    except Exception as e:
        return {"success": False, "error": str(e)}

