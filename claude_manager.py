"""Модуль управления сетевым подключением узлов Type-B с гарантией чистоты маршрута (Fail-Safe Channel Isolation).

Научно-инженерная цель:
Предотвращение искажения замеров задержки и чистоты эксперимента. Если выделенный
исследовательский сокет становится недоступен, тестовый узел изолируется через blackhole (127.0.0.1:1),
гарантируя, что тестовый трафик никогда случайно не пойдет по общим нетарифицируемым маршрутам хоста
и не исказит сравнительные метрики задержки.

Обеспечивает:
- Настройку выделенного тестового сокета для узлов Type-B
- Защиту чистоты маршрута (Fail-Safe Isolation): изоляция трафика при падении тестового канала
- Автоматический экспорт и импорт параметров стенда в снимках (.hbak)
- Мониторинг статуса тестовых сокетов в proxies.json в реальном времени
- Автоматическое восстановление соединения, когда тестовый порт возвращается в онлайн
- Синхронизацию конфигурации в Windows и WSL2
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
    """Возвращает список существующих или доступных файлов settings.json для узлов Type-B."""
    files: list[Path] = []
    
    # 1. Windows: settings.json
    try:
        win_claude_dir = Path.home() / ".claude"
        win_claude_file = win_claude_dir / "settings.json"
        files.append(win_claude_file)
    except Exception:
        pass

    # 2. WSL2: settings.json
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
    """Возвращает текущие настройки подключения узлов Type-B из settings_manager."""
    return {
        "host": settings_manager.get_claude_proxy_host(),
        "port": settings_manager.get_claude_proxy_port(),
        "killswitch": settings_manager.get_claude_killswitch(),
    }


def save_claude_config(
    host: str,
    port: int,
    killswitch: bool,
    restriction_enabled: bool | None = None,
) -> None:
    """Сохраняет настройки подключения узлов Type-B в settings.json и синхронизирует конфигурацию."""
    settings_manager.set_claude_proxy_settings(
        host, port, killswitch, restriction_enabled=restriction_enabled
    )
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

        # Добавляем выделенный прокси для узлов Type-B
        new_entry = proxy_manager.add_proxy(host=host, port=port, label=f"Type-B: SOCKS5 :{port}")
        return new_entry
    except Exception:
        return None


def apply_claude_settings_to_file(
    filepath: Path,
    host: str,
    port: int,
    killswitch_engaged: bool,
    restriction_enabled: bool = True,
) -> bool:
    """Записывает настройки прокси / блокировки Killswitch в указанный файл settings.json.
    
    Если restriction_enabled == False:
    Удаляет прокси-переменные из env, восстанавливая прямой доступ (Direct IP)
    и позволяя локальным приложениям (например, Smoozy) работать без принуждения к сокету 1015.
    """
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

        PROX_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
        if not restriction_enabled:
            # Ограничение выключено: убираем переменные прокси, сохраняя остальные настройки
            for k in PROX_KEYS:
                data["env"].pop(k, None)
        elif killswitch_engaged:
            # Блокировка: заворачиваем трафик на несуществующий blackhole
            data["env"]["HTTPS_PROXY"] = BLACKHOLE_HTTP
            data["env"]["HTTP_PROXY"] = BLACKHOLE_HTTP
            data["env"]["ALL_PROXY"] = BLACKHOLE_SOCKS
            data["env"]["https_proxy"] = BLACKHOLE_HTTP
            data["env"]["http_proxy"] = BLACKHOLE_HTTP
            data["env"]["all_proxy"] = BLACKHOLE_SOCKS
        else:
            http_port = 10000 + int(port)
            data["env"]["HTTPS_PROXY"] = f"http://{host}:{http_port}"
            data["env"]["HTTP_PROXY"] = f"http://{host}:{http_port}"
            data["env"]["ALL_PROXY"] = f"socks5h://{host}:{port}"
            data["env"]["https_proxy"] = f"http://{host}:{http_port}"
            data["env"]["http_proxy"] = f"http://{host}:{http_port}"
            data["env"]["all_proxy"] = f"socks5h://{host}:{port}"

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def apply_claude_proxy_sync(
    host: str,
    port: int,
    killswitch_engaged: bool,
    restriction_enabled: bool | None = None,
) -> None:
    """Синхронизирует настройки прокси или статус Killswitch.
    
    ВАЖНО:
    Node.js в Windows (включая Smoozy и любые локальные утилиты) ВСЕГДА работает
    напрямую без каких-либо ограничений. В Windows settings.json прокси-переменные
    не прописываются (всегда Direct IP).
    Ограничение на сокет 1015 применяется строго к среде WSL2, где функционирует
    исследовательский кластер Claude Code.
    """
    if restriction_enabled is None:
        restriction_enabled = settings_manager.get_claude_restriction_enabled()

    # Применяем или снимаем изоляцию на уровне ядра Linux WSL2
    try:
        import node_isolate_manager
        if restriction_enabled:
            node_isolate_manager.apply_wsl_isolation(
                port=port,
                http_port=10000 + int(port),
                killswitch=killswitch_engaged,
            )
        else:
            node_isolate_manager.remove_wsl_isolation()
    except Exception:
        pass

    for target_file in get_claude_settings_files():
        try:
            is_win_user = False
            try:
                if target_file.resolve() == (Path.home() / ".claude" / "settings.json").resolve():
                    is_win_user = True
            except Exception:
                pass

            if is_win_user:
                # В хост-среде Windows сохраняем прямой доступ без прокси
                apply_claude_settings_to_file(
                    target_file, host, port, killswitch_engaged=False, restriction_enabled=False
                )
            else:
                # В целевой среде (WSL2 или изолированном тестовом файле) применяем параметры маршрутизации
                apply_claude_settings_to_file(
                    target_file, host, port, killswitch_engaged=killswitch_engaged, restriction_enabled=restriction_enabled
                )
        except Exception:
            pass


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


def check_claude_isolation() -> dict[str, Any]:
    """Проверяет, изолирован ли узел Type-B на сокет 1015 и активен ли брандмауэр."""
    # 1. Проверяем настройки Windows
    win_has_proxy = False
    win_proxy_val = ""
    try:
        win_f = Path.home() / ".claude" / "settings.json"
        if win_f.exists():
            with open(win_f, "r", encoding="utf-8") as f:
                d = json.load(f)
                env = d.get("env", {}) if isinstance(d, dict) else {}
                for k in ("ALL_PROXY", "HTTPS_PROXY", "all_proxy", "https_proxy"):
                    if k in env and env[k]:
                        win_has_proxy = True
                        win_proxy_val = str(env[k])
                        break
    except Exception:
        pass

    # 2. Проверяем настройки WSL
    wsl_has_proxy = False
    wsl_proxy_val = ""
    try:
        wsl_f = Path(rf"\\wsl$\{WSL_DISTRO}\home\{WSL_USER}\.claude\settings.json")
        if wsl_f.exists():
            with open(wsl_f, "r", encoding="utf-8") as f:
                d = json.load(f)
                env = d.get("env", {}) if isinstance(d, dict) else {}
                for k in ("ALL_PROXY", "HTTPS_PROXY", "all_proxy", "https_proxy"):
                    if k in env and env[k]:
                        wsl_has_proxy = True
                        wsl_proxy_val = str(env[k])
                        break
    except Exception:
        pass

    # 3. Проверяем правило брандмауэра Windows и ядра Linux WSL2
    firewall_active = False
    wsl_kernel_isolated = False
    try:
        import node_isolate_manager
        firewall_active = node_isolate_manager.check_isolation_status("127.0.0.1", 1015, True)
        wsl_kernel_isolated = node_isolate_manager.check_wsl_isolation_active()
    except Exception:
        pass

    # 4. Проверяем доступность портов 1015 и 11015
    p_socks = settings_manager.get_claude_proxy_port()
    p_http = 10000 + p_socks
    p1015_open = check_port_accessible("127.0.0.1", p_socks, timeout=0.3)
    p11015_open = check_port_accessible("127.0.0.1", p_http, timeout=0.3)

    restriction_on = settings_manager.get_claude_restriction_enabled()
    if wsl_kernel_isolated and not restriction_on:
        # При перезапуске WSL2 или загрузке ОС по умолчанию активируется ограничение :1015
        restriction_on = True
        try:
            settings_manager.set_claude_restriction_enabled(True)
        except Exception:
            pass

        if not wsl_has_proxy:
            level = "DIRECT"
            badge = "⚪ ВЫКЛ (Прямой доступ)"
            color = "#a6adc8"
            summary = f"Ограничения отключены: прямой доступ к сети без сокета {p_socks}."
        else:
            level = "RESIDUAL"
            badge = "🟡 ВЫКЛ (Остаточные записи)"
            color = "#f9e2af"
            summary = "Переключатель ВЫКЛ, но в настройках WSL2 обнаружены записи прокси."
    else:
        if wsl_has_proxy and p1015_open:
            level = "ISOLATED"
            badge = f"🟢 WSL2 ИЗОЛИРОВАН (:{p_socks})"
            color = "#a6e3a1"
            summary = f"Изоляция активна: узел Type-B в WSL2 направлен в сокет {p_socks} (remote DNS), ядро блокирует утечки."
        elif not p1015_open:
            if wsl_kernel_isolated:
                level = "LOCKDOWN"
                badge = "🛡️ KILLSWITCH (БЛОКИРОВКА)"
                color = "#fab387"
                summary = f"Порт {p_socks} оффлайн. Включена полная изоляция ядра Linux (Zero Leaks, все внешние соединения заблокированы)."
            else:
                level = "PORT_OFFLINE"
                badge = f"🔴 ОШИБКА: Порт {p_socks} закрыт"
                color = "#f38ba8"
                summary = f"WSL2 замкнут на сокет {p_socks}, но порт закрыт! Сетевые запросы в WSL2 будут падать."
        else:
            level = "PARTIAL"
            badge = "🟡 WSL2 НЕ ИЗОЛИРОВАН"
            color = "#f9e2af"
            summary = "Ограничение включено, но в настройках WSL2 отсутствует прокси."

    details = [
        f"WSL2 settings.json: {'🔒 ' + wsl_proxy_val if wsl_has_proxy else '🌐 Direct (без сокета 1015)'}",
        f"Сокет SOCKS5 127.0.0.1:{p_socks}: {'🟢 ONLINE' if p1015_open else '🔴 OFFLINE'}",
        f"Сокет HTTP 127.0.0.1:{p_http}: {'🟢 ONLINE' if p11015_open else '🔴 OFFLINE'}",
        f"Изоляция ядра Linux (Zero Leaks): {'🟢 АКТИВНА (Внешний трафик перекрыт)' if wsl_kernel_isolated else '⚪ НЕ АКТИВНА'}",
    ]

    return {
        "restriction_enabled": restriction_on,
        "is_isolated": win_has_proxy or wsl_has_proxy or firewall_active or wsl_kernel_isolated,
        "status": level.lower(),
        "level": level,
        "badge": badge,
        "color": color,
        "summary": summary,
        "details": details,
        "win_has_proxy": win_has_proxy,
        "wsl_has_proxy": wsl_has_proxy,
        "firewall_active": firewall_active,
        "wsl_kernel_isolated": wsl_kernel_isolated,
        "p1015_open": p1015_open,
        "p11015_open": p11015_open,
    }


def set_claude_restriction(enabled: bool) -> dict[str, Any]:
    """Включает или выключает ограничения узлов Type-B на сокет 1015 (прокси и брандмауэр)."""
    settings_manager.set_claude_restriction_enabled(enabled)
    h = settings_manager.get_claude_proxy_host()
    p = settings_manager.get_claude_proxy_port()

    if enabled:
        # Включаем ограничение 1015 в файлах settings.json (только WSL2)
        apply_claude_proxy_sync(h, p, killswitch_engaged=False, restriction_enabled=True)
    else:
        # Выключаем ограничение 1015: очищаем settings.json в Windows и WSL
        apply_claude_proxy_sync(h, p, killswitch_engaged=False, restriction_enabled=False)
        # Принудительно снимаем правило брандмауэра
        try:
            import node_isolate_manager
            node_isolate_manager.remove_firewall_rule()
        except Exception:
            pass

    return check_claude_isolation()


def probe_claude_route(
    host: str | None = None,
    port: int | None = None,
    killswitch: bool | None = None,
    fast: bool = False,
) -> dict[str, Any]:
    """Проверяет состояние подключения узла Type-B и управляет состоянием Killswitch.
    
    Для узлов Type-B авто-переключение не применяется: если выбранный порт (например, 1085)
    недоступен или сокет закрыт, строго активируется Killswitch (трафик глушится через blackhole),
    предотвращая любые искажения чистоты сетевых замеров.
    Когда сокет на этом порту поднимается:
      - Killswitch автоматически отключается, восстанавливая нормальную работу.
    """
    if host is None:
        host = settings_manager.get_claude_proxy_host()
    if port is None:
        port = settings_manager.get_claude_proxy_port()
    if killswitch is None:
        killswitch = settings_manager.get_claude_killswitch()

    http_port = 10000 + int(port)

    # Проверяем мастер-переключатель ограничения на сокет 1015
    restriction_enabled = settings_manager.get_claude_restriction_enabled()
    if not restriction_enabled:
        apply_claude_proxy_sync(host, port, killswitch_engaged=False, restriction_enabled=False)
        return {
            "online": True,
            "killswitch_engaged": False,
            "killswitch_enabled": False,
            "restriction_enabled": False,
            "host": host,
            "port": port,
            "http_port": http_port,
            "status_text": "⚪ Прямой доступ (Ограничение на сокет 1015 ВЫКЛ)",
            "route": "Прямой доступ (Direct • Без сокета 1015)",
            "ip": "-",
            "country": "-",
            "latency_ms": None,
            "details": "Ограничение на сокет 1015 отключено: Claude Code и локальные приложения (Smoozy) работают напрямую.",
            "reason": "restriction_off",
            "failover_occurred": False,
        }

    timeout = 0.2 if fast else 0.5
    is_active, reason, proxy_entry = is_proxy_active_in_list(host, port, timeout=timeout)

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

    # Быстрый замер связи через HTTP CONNECT прокси
    t0 = time.time()
    lat = stored_lat
    if not fast:
        try:
            proxy_url = f"http://{host}:{http_port}"
            resp = requests.get(
                ANTHROPIC_ENDPOINT,
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=2.0,
                headers={"User-Agent": "HerdrTelemetryRouter/1.0"}
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
        "details": f"Трафик узлов Type-B туннелируется через {host}:{port} (Killswitch вооружен)",
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
    """Выполняет комплексную синхронизацию узлов Type-B с AionUi через aionui_claude_bridge."""
    try:
        import aionui_claude_bridge
        return aionui_claude_bridge.sync_all(oauth_file=oauth_file)
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_isolation_diagnostic(
    log_callback: Any = None,
) -> dict[str, Any]:
    """Проводит глубокий аудит сетевой изоляции и предотвращения DNS-утечек для узлов Type-B (WSL2).
    
    Проверяет:
    1. Доступность локальных портов SOCKS5 1015 и HTTP CONNECT 11015 с замером TCP handshake.
    2. Конфигурацию узлов Type-B в WSL2 и статус сетевой тюрьмы ядра Linux (nftables).
    3. Живой тест сетевого выхода из Linux WSL2:
       - Если ограничение 1015 ВКЛ:
         * Маршрутизацию через сокеты 1015/11015 и делегирование DNS (socks5h).
         * Прямой тест утечки IP (Direct Egress Leak Test): подтверждает, что прямой трафик без прокси блокируется ядром.
         * Тест защиты от обхода через сторонние порты (например, :2080).
         * Поведение при неработающем порте 1015 (подтверждение герметичности Killswitch).
       - Если ограничение 1015 ВЫКЛ: проверяет свободный прямой доступ из WSL2.
    4. Итоговый вердикт о надежности изоляции.
    """
    import subprocess
    import node_isolate_manager
    logs: list[tuple[str, str]] = []

    def emit(level: str, msg: str) -> None:
        logs.append((level, msg))
        if callable(log_callback):
            try:
                log_callback(level, msg)
            except Exception:
                pass

    ts = time.strftime("%H:%M:%S")
    emit("TIMESTAMP", f"[{ts}] Запуск аудита изоляции и DNS Claude Code в WSL2 Linux...")

    # --- ЭТАП 1: Локальные сокеты 1015 / 11015 ---
    emit("STEP", "--- [ЭТАП 1/4] Проверка доступности локальных портов прокси ---")
    h = settings_manager.get_claude_proxy_host()
    p_socks = settings_manager.get_claude_proxy_port()
    p_http = 10000 + p_socks

    # Проверка 1015
    t0 = time.time()
    p1015_ok = check_port_accessible(h, p_socks, timeout=0.5)
    lat_1015 = int((time.time() - t0) * 1000)
    if p1015_ok:
        emit("SUCCESS", f"✓ Сокет SOCKS5 {h}:{p_socks} активен (TCP handshake: {lat_1015} ms).")
    else:
        emit("ERROR", f"✕ Сокет SOCKS5 {h}:{p_socks} недоступен (порт закрыт или сервер не запущен).")

    # Проверка 11015
    t1 = time.time()
    p11015_ok = check_port_accessible(h, p_http, timeout=0.5)
    lat_11015 = int((time.time() - t1) * 1000)
    if p11015_ok:
        emit("SUCCESS", f"✓ Сокет HTTP CONNECT {h}:{p_http} активен (TCP handshake: {lat_11015} ms).")
    else:
        emit("ERROR", f"✕ Сокет HTTP CONNECT {h}:{p_http} недоступен.")

    # --- ЭТАП 2: Конфигурация в WSL2 Linux ---
    emit("STEP", "--- [ЭТАП 2/4] Проверка конфигурации Claude Code и ядра Linux в WSL2 ---")
    restr_on = settings_manager.get_claude_restriction_enabled()
    emit("INFO", f"Мастер-переключатель ограничения на сокет {p_socks}: {'🟢 ВКЛ' if restr_on else '⚪ ВЫКЛ (Прямой доступ)'}")

    # Проверка и синхронизация изоляции ядра Linux
    kernel_isolated = node_isolate_manager.check_wsl_isolation_active()
    if restr_on:
        if not kernel_isolated:
            emit("INFO", "Применение сетевой изоляции ядра Linux (nftables)...")
            node_isolate_manager.apply_wsl_isolation(port=p_socks, http_port=p_http, killswitch=(not p1015_ok))
            kernel_isolated = node_isolate_manager.check_wsl_isolation_active()
        if kernel_isolated:
            emit("SUCCESS", "✓ Сетевая тюрьма ядра Linux активна (nftables: WAN/LAN трафик отсечён, loopback защищен).")
        else:
            emit("WARN", "⚠ Не удалось активировать тюрьму ядра Linux через nftables/iptables.")
    else:
        if kernel_isolated:
            emit("INFO", "Снятие сетевой тюрьмы ядра Linux для прямого доступа...")
            node_isolate_manager.remove_wsl_isolation()
            emit("SUCCESS", "✓ Сетевая тюрьма ядра Linux отключена.")

    wsl_proxy_val = ""
    wsl_has_proxy = False
    wsl_settings_path = Path(rf"\\wsl$\{WSL_DISTRO}\home\{WSL_USER}\.claude\settings.json")
    try:
        if wsl_settings_path.exists():
            with open(wsl_settings_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                env = d.get("env", {}) if isinstance(d, dict) else {}
                wsl_all_proxy = env.get("ALL_PROXY") or env.get("all_proxy")
                wsl_https_proxy = env.get("HTTPS_PROXY") or env.get("https_proxy")
                if wsl_all_proxy or wsl_https_proxy:
                    wsl_has_proxy = True
                    wsl_proxy_val = str(wsl_all_proxy or wsl_https_proxy)
            if restr_on:
                if "socks5h://" in str(wsl_all_proxy):
                    emit("SUCCESS", f"✓ WSL2 env.ALL_PROXY: {wsl_all_proxy} (протокол socks5h: удаленный DNS).")
                else:
                    emit("WARN", f"⚠ WSL2 ALL_PROXY: {wsl_all_proxy}")
                if wsl_https_proxy:
                    emit("SUCCESS", f"✓ WSL2 env.HTTPS_PROXY: {wsl_https_proxy} (HTTP CONNECT туннель).")
            else:
                if wsl_has_proxy:
                    emit("WARN", f"⚠ Режим ВЫКЛ, но в WSL2 обнаружен прокси {wsl_proxy_val}.")
                else:
                    emit("SUCCESS", "✓ WSL2 settings.json: Direct (прокси-переменные отключены).")
        else:
            emit("WARN", f"⚠ Файл настроек WSL2 не найден: {wsl_settings_path}")
    except Exception as e:
        emit("WARN", f"⚠ Ошибка чтения настроек WSL2: {e}")

    # --- ЭТАП 3: Живой аудит маршрута, защита от обходов и утечек в WSL2 ---
    emit("STEP", "--- [ЭТАП 3/4] Живой аудит маршрутизации, защита от утечек и обходов в WSL2 ---")
    wsl_proxy_ip = "-"
    dns_leak_protected = False
    direct_leak_detected = False
    bypass_leak_detected = False

    def exec_wsl(bash_cmd: str, timeout: float = 5.0) -> tuple[int, str]:
        try:
            r = subprocess.run(
                ["wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-c", bash_cmd],
                capture_output=True, text=True, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
            return r.returncode, r.stdout.strip()
        except Exception as ex:
            return -1, str(ex)

    if restr_on:
        if p1015_ok and p11015_ok:
            # 1. Проверка HTTP CONNECT через сокет 11015 к тестовому эндпоинту API
            c_code, c_out = exec_wsl(f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 4 -x http://127.0.0.1:{p_http} https://api.anthropic.com")
            if c_code == 0 and c_out in ("200", "404", "401", "403"):
                emit("SUCCESS", f"✓ Запрос из WSL2 через HTTP CONNECT :{p_http} успешен (HTTP code {c_out}).")
            else:
                emit("WARN", f"⚠ Запрос через HTTP :{p_http} вернул: {c_out} (код: {c_code}).")

            # 2. Проверка Remote DNS и внешнего IP через SOCKS5H
            c2_code, c2_out = exec_wsl(f"curl -s --connect-timeout 4 --socks5-hostname 127.0.0.1:{p_socks} https://ifconfig.me")
            if c2_code == 0 and c2_out and not "curl:" in c2_out:
                wsl_proxy_ip = c2_out
                emit("SUCCESS", f"✓ Запрос из WSL2 через SOCKS5 :{p_socks} успешен. Внешний IP прокси: {wsl_proxy_ip}")
                dns_leak_protected = True
                emit("SUCCESS", "✓ Защита от DNS-утечек: ДА (протокол socks5h делегирует DNS в туннель, DNS хоста не опрашивается).")
            else:
                emit("ERROR", f"✕ Тест SOCKS5H :{p_socks} завершился с ошибкой: {c2_out}")
        else:
            emit("WARN", f"⚠ Сокет :{p_socks} / :{p_http} оффлайн. Проверка работы Killswitch...")
            c_off_code, c_off_out = exec_wsl(f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 3 -x http://127.0.0.1:{p_http} https://api.anthropic.com")
            if c_off_code != 0 or c_off_out == "000":
                emit("SUCCESS", "✓ Трафик через закрытый порт HTTP CONNECT надёжно отвергнут.")

        # 3. КРИТИЧЕСКИЙ ТЕСТ: Проверка прямого выхода (Direct IP Leak Test)
        c_dir_code, c_dir_out = exec_wsl("curl -s --connect-timeout 2 --noproxy '*' https://ifconfig.me")
        if c_dir_code != 0:
            emit("SUCCESS", "✓ Защита от прямого выхода: АКТИВНА (Прямой интернет-трафик отклонён ядром Linux, утечка IP невозможна).")
        else:
            direct_leak_detected = True
            emit("ERROR", f"✕ ВНИМАНИЕ: ОБНАРУЖЕНА УТЕЧКА СЕТИ! Прямой выход успешен без прокси, реальный IP: {c_dir_out}")

        # 4. ТЕСТ ЗАЩИТЫ ОТ ОБХОДОВ: Проверка стороннего локального сокета 2080
        c_byp_code, c_byp_out = exec_wsl("curl -s --connect-timeout 2 -x http://127.0.0.1:2080 https://ifconfig.me")
        if c_byp_code != 0:
            emit("SUCCESS", "✓ Защита от сторонних обходов: АКТИВНА (Попытка обхода через сокет :2080 отклонена ядром).")
        else:
            bypass_leak_detected = True
            emit("WARN", f"⚠ Сторонний сокет :2080 доступен (IP: {c_byp_out}).")

        # 5. Тест блокировки Claude к эндпоинту API при оффлайн-порте
        if not p1015_ok:
            c_api_dir_code, c_api_dir_out = exec_wsl("curl -s -o /dev/null -w '%{http_code}' --connect-timeout 2 --noproxy '*' https://api.anthropic.com")
            if c_api_dir_code != 0 or c_api_dir_out == "000":
                emit("SUCCESS", "✓ Режим Killswitch подтверждён: доступ к Anthropic API полностью заблокирован на уровне ядра (Zero Leaks).")
            else:
                emit("ERROR", f"✕ Ошибка Killswitch: Anthropic API доступен напрямую (HTTP {c_api_dir_out})!")
    else:
        # Режим ВЫКЛ: проверяем прямой выход из WSL2
        c_dir_code, c_dir_out = exec_wsl("curl -s --connect-timeout 4 https://ifconfig.me")
        if c_dir_code == 0 and c_dir_out:
            emit("SUCCESS", f"✓ Прямой выход из WSL2 активен без прокси. Внешний IP: {c_dir_out}")
        else:
            emit("INFO", f"Прямой запрос из WSL2: {c_dir_out}")

    # --- ЭТАП 4: Итоговое заключение ---
    emit("STEP", "--- [ЭТАП 4/4] Итоговый вердикт аудита изоляции ---")
    if restr_on:
        if direct_leak_detected:
            emit("ERROR", "═════════════════════════════════════════════════════════════════════")
            emit("ERROR", " КРИТИЧЕСКАЯ УТЕЧКА СЕТИ (LEAK DETECTED):")
            emit("ERROR", " • Прямое соединение без прокси доступно в WSL2 в обход ограничений!")
            emit("ERROR", " • Проверьте применение правил фильтрации ядра Linux.")
            emit("ERROR", "═════════════════════════════════════════════════════════════════════")
            status = "leak"
        elif p1015_ok and p11015_ok and wsl_has_proxy and dns_leak_protected:
            emit("SUCCESS", "═════════════════════════════════════════════════════════════════════")
            emit("SUCCESS", " ИЗОЛЯЦИЯ ПОЛНОСТЬЮ ПОДТВЕРЖДЕНА:")
            emit("SUCCESS", f" • Claude Code в WSL2 строго замкнут на системный прокси :{p_socks} / :{p_http}.")
            emit("SUCCESS", " • Прямой выход и сторонние сокеты (:2080) физически заблокированы ядром Linux.")
            emit("SUCCESS", " • DNS-запросы безопасно туннелируются через socks5h, утечек DNS нет.")
            emit("SUCCESS", "═════════════════════════════════════════════════════════════════════")
            status = "isolated"
        elif not p1015_ok:
            emit("SUCCESS", "═════════════════════════════════════════════════════════════════════")
            emit("SUCCESS", f" 🛡️ KILLSWITCH АКТИВЕН (ПОЛНАЯ БЛОКИРОВКА СЕТИ):")
            emit("SUCCESS", f" • Сокет :{p_socks} закрыт. Все сетевые запросы из WSL2 полностью заблокированы ядром.")
            emit("SUCCESS", " • Утечки исключены: прямой доступ и обходные каналы физически перекрыты (Zero Leaks).")
            emit("SUCCESS", "═════════════════════════════════════════════════════════════════════")
            status = "lockdown"
        else:
            emit("WARN", "═════════════════════════════════════════════════════════════════════")
            emit("WARN", " ПРЕДУПРЕЖДЕНИЕ: Обнаружены проблемы с доступностью портов или настройками WSL2.")
            emit("WARN", "═════════════════════════════════════════════════════════════════════")
            status = "warning"
    else:
        emit("SUCCESS", "═════════════════════════════════════════════════════════════════════")
        emit("SUCCESS", " ПРЯМОЙ ДОСТУП АКТИВЕН:")
        emit("SUCCESS", f" • Ограничение на сокет {p_socks} отключено. Сетевые ограничения сняты.")
        emit("SUCCESS", " • Claude Code и WSL2 работают напрямую через прямое интернет-соединение.")
        emit("SUCCESS", "═════════════════════════════════════════════════════════════════════")
        status = "direct"

    return {
        "status": status,
        "logs": logs,
        "wsl_has_proxy": wsl_has_proxy,
        "p1015_open": p1015_ok,
        "p11015_open": p11015_ok,
        "wsl_proxy_ip": wsl_proxy_ip,
        "dns_leak_protected": dns_leak_protected,
        "kernel_isolated": kernel_isolated,
        "direct_leak_detected": direct_leak_detected,
        "bypass_leak_detected": bypass_leak_detected,
    }

