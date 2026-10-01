"""CLI точка входа для запуска модульных блоков действий Omni_Aion.

Примеры использования:
    python -m Omni_Aion.actions --all
    python -m Omni_Aion.actions --block 4
    python -m Omni_Aion.actions --farm
    python -m Omni_Aion.actions --start --force-restart
    python -m Omni_Aion.actions --all --dry-run --json
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

from Omni_Aion.actions import run_all_blocks, run_block, OMNI_AION_ACTIONS_REGISTRY
from Omni_Aion.actions.pipeline import OmniAionDeployPipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m Omni_Aion.actions",
        description="Модульные блоки ускоренной установки и настройки стека OmniRoute + AionUi",
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--all",
        action="store_true",
        help="Выполнить сквозной конвейер (блоки 1..5)",
    )
    group.add_argument(
        "--block",
        type=int,
        choices=[1, 2, 3, 4, 5],
        help="Запустить конкретный блок: 1=Runtime, 2=AionUi, 3=OmniCore, 4=FarmSync, 5=OmniStart",
    )
    group.add_argument(
        "--farm",
        action="store_true",
        help="Синхронизировать аккаунты Gemini и изолировать сокеты (быстрый вызов блока 4)",
    )
    group.add_argument(
        "--start",
        action="store_true",
        help="Запустить демон OmniRoute в tmux (быстрый вызов блока 5)",
    )

    parser.add_argument(
        "--distro",
        default="Ubuntu",
        help="Имя WSL дистрибутива (по умолчанию: Ubuntu)",
    )
    parser.add_argument(
        "--force-restart",
        action="store_true",
        help="Принудительно перезапустить сервисы, даже если они уже online",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Симуляция без внесения изменений",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Подробный вывод журналов",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывод результата в формате JSON",
    )

    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # По умолчанию запускаем полный пайплайн
    if args.farm:
        res = run_block(4, distro=args.distro, dry_run=args.dry_run, verbose=args.verbose)
    elif args.start:
        res = run_block(
            5,
            distro=args.distro,
            force_restart=args.force_restart,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.block:
        res = run_block(
            args.block,
            distro=args.distro,
            force_restart=args.force_restart,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    else:
        # --all или по умолчанию
        res = run_all_blocks(
            distro=args.distro,
            force_restart=args.force_restart,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(res.summary_str())

    return 0 if res.success else 1


if __name__ == "__main__":
    sys.exit(main())
