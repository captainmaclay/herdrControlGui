"""CLI интерфейс для выполнения автоматизированных действий Herdr Control Center.

Примеры использования:
    python -m actions list
    python -m actions install_claude_wsl --dry-run
    python -m actions killswitch_heal --port 1015
    python -m actions wsl_isolation --mode apply
    python -m actions verify_connectivity --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from actions import ACTIONS_REGISTRY, get_action, list_actions



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m actions",
        description="Выполнение системных действий Herdr Control Center",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="list",
        help=f"Имя действия или 'list'. Доступные: {list(ACTIONS_REGISTRY.keys())}",
    )
    parser.add_argument("--dry-run", action="store_true", help="Режим симуляции без внесения изменений")
    parser.add_argument("--verbose", "-v", action="store_true", help="Подробный вывод логов")
    parser.add_argument("--json", action="store_true", help="Форматировать вывод в виде JSON")

    # Сетевые порты
    parser.add_argument("--port", type=int, default=1015, help="Локальный порт SOCKS5 (по умолчанию: 1015)")
    parser.add_argument("--http-port", type=int, default=11015, help="Локальный порт HTTP CONNECT (по умолчанию: 11015)")
    parser.add_argument("--distro", default="Ubuntu", help="Имя WSL дистрибутива (по умолчанию: Ubuntu)")

    # Параметры wsl_isolation
    parser.add_argument(
        "--mode",
        choices=["apply", "teardown", "verify"],
        default="apply",
        help="Режим для wsl_isolation: apply (установить), teardown (снять), verify (проверить)",
    )
    parser.add_argument("--killswitch", action="store_true", help="Флаг Killswitch для wsl_isolation")

    # Параметры install_claude_wsl
    parser.add_argument("--no-shortcuts", action="store_true", help="Не создавать ярлыки на рабочем столе")
    parser.add_argument("--shutdown-wsl", action="store_true", help="Перезапустить WSL после правки .wslconfig")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.action == "list":
        if args.json:
            print(json.dumps(list_actions(), ensure_ascii=False, indent=2))
        else:
            print("Доступные системные действия Herdr:")
            for item in list_actions():
                print(f"  * {item['name']:<22} - {item['description']}")
        return 0

    if args.action not in ACTIONS_REGISTRY:
        print(f"Ошибка: неизвестное действие '{args.action}'.", file=sys.stderr)
        print(f"Доступные действия: {', '.join(ACTIONS_REGISTRY.keys())}", file=sys.stderr)
        return 1

    # Инстанцирование действия
    init_kwargs = {
        "dry_run": args.dry_run,
        "verbose": args.verbose,
    }
    if args.action in ("install_claude_wsl", "wsl_isolation", "verify_connectivity"):
        init_kwargs["socks_port"] = args.port
        init_kwargs["http_port"] = args.http_port
        init_kwargs["distro"] = args.distro
    elif args.action == "killswitch_heal":
        init_kwargs["target_port"] = args.port
    elif args.action == "omni_aion_pipeline":
        init_kwargs["distro"] = args.distro

    action_instance = get_action(args.action, **init_kwargs)

    # Параметры запуска
    run_kwargs = {}
    if args.action == "wsl_isolation":
        run_kwargs["action_mode"] = args.mode
        run_kwargs["killswitch"] = args.killswitch
    elif args.action == "install_claude_wsl":
        run_kwargs["create_shortcuts"] = not args.no_shortcuts
        run_kwargs["shutdown_wsl_on_config_change"] = args.shutdown_wsl

    result = action_instance.run(**run_kwargs)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.summary_str())

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
