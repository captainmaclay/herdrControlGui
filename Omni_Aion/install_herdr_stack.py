#!/usr/bin/env python3
"""
CLI Установщик стека Herdr Stack (OmniRoute + AionUi + Gemini Farm).
Поддерживает интерактивный режим (ввод пароля) и автоматический режим для ИИ (Gemini).

Использование:
    Интерактивно:
        python Omni_Aion/install_herdr_stack.py

    Для ИИ / скриптов (неблокирующий режим):
        python Omni_Aion/install_herdr_stack.py --key "ВАШ_МАСТЕР_КЛЮЧ"
        python Omni_Aion/install_herdr_stack.py --key "ВАШ_МАСТЕР_КЛЮЧ" --bundle "dist/my_bundle.hbin" --yes
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# Обеспечиваем поиск модулей из папки проекта
_CUR_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _CUR_DIR.parent if (_CUR_DIR.parent / "config_app.py").exists() else _CUR_DIR
sys.path.insert(0, str(_PROJECT_DIR))
sys.path.insert(0, str(_CUR_DIR))

try:
    from Omni_Aion.stack_bundle_manager import (
        install_stack_bundle,
        find_latest_bundle,
        compute_key_fingerprint,
    )
except ImportError:
    from stack_bundle_manager import (
        install_stack_bundle,
        find_latest_bundle,
        compute_key_fingerprint,
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Автономный установщик стека Omni_Aion (OmniRoute + AionUi + Gemini)")
    parser.add_argument("--key", "-k", type=str, default=None, help="Мастер-ключ для расшифровки токенов и БД")
    parser.add_argument("--bundle", "-b", type=str, default=None, help="Путь к файлу бандла (.hbin)")
    parser.add_argument("--actions", "-a", action="store_true", help="Запустить модульный конвейер Actions Framework вместо распаковки бандла")
    parser.add_argument("--yes", "-y", action="store_true", help="Автоматическое согласие на установку без интерактивных запросов")
    parser.add_argument("--skip-self-test", action="store_true", help="Пропустить проверку сервисов после установки")
    return parser.parse_args()


def main():
    args = parse_args()
    print("=" * 68)
    print("📦 HERDR STACK INSTALLER & RUNTIME DEPLOYMENT (OmniRoute + AionUi)")
    print("=" * 68)

    # Режим прямого запуска модульных блоков Actions Framework
    if args.actions:
        from Omni_Aion.actions import run_all_blocks
        print("[+] Запуск модульного конвейера Actions Framework (Блоки 1..5)...")
        res = run_all_blocks()
        print(res.summary_str())
        sys.exit(0 if res.success else 1)

    # 1. Поиск бандла
    bundle_path = None
    if args.bundle:
        bp = Path(args.bundle)
        if bp.exists():
            bundle_path = bp
        else:
            print(f"[-] Указанный файл бандла не найден: {bp}")
            sys.exit(1)
    else:
        bundle_path = find_latest_bundle()

    if not bundle_path or not bundle_path.exists():
        print("[!] Файлов бандла (*.hbin) в каталоге dist/ не найдено.")
        print("[+] Автоматическое переключение на модульный конвейер Actions Framework...")
        from Omni_Aion.actions import run_all_blocks
        res = run_all_blocks()
        print(res.summary_str())
        sys.exit(0 if res.success else 1)

    sz_mb = bundle_path.stat().st_size / (1024 * 1024)
    print(f"[+] Выбран установочный бандл: {bundle_path.name} ({sz_mb:.1f} МБ)")
    print(f"[+] Расположение: {bundle_path.resolve()}")
    print("-" * 68)

    # 2. Получение мастер-ключа
    master_key = args.key
    if not master_key:
        print("Мастер-ключ защищает конфиденциальные OAuth-токены Google Gemini и базу данных.")
        print("Он запрашивается ТОЛЬКО ОДИН РАЗ при первой установке.")
        print("-" * 68)
        try:
            master_key = getpass.getpass("Введите мастер-ключ для расшифровки: ")
        except (KeyboardInterrupt, EOFError):
            print("\n[-] Прервано пользователем.")
            sys.exit(1)

    if not master_key or len(master_key) < 1:
        print("[-] Ошибка: мастер-ключ не может быть пустым.")
        sys.exit(1)

    fp = compute_key_fingerprint(master_key)
    print(f"[+] Хэш-отпечаток введённого ключа: [ {fp} ]")

    # 3. Подтверждение
    if not args.yes and not args.key:
        try:
            ans = input("\nНачать развертывание и настройку стека в WSL2? [Y/n]: ").strip().lower()
            if ans and ans not in ["y", "yes", "д", "да"]:
                print("[-] Установка отменена.")
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            print("\n[-] Прервано.")
            sys.exit(1)

    print("\n[+] Запуск процесса развертывания...\n")

    # 4. Процесс установки
    try:
        report = install_stack_bundle(
            master_key=master_key,
            bundle_path=bundle_path,
            log_callback=print
        )
    except Exception as e:
        print(f"\n[-] КРИТИЧЕСКАЯ ОШИБКА РАЗВЕРТЫВАНИЯ: {e}")
        sys.exit(1)

    # 5. Результаты
    print("\n" + "=" * 68)
    print("🎉 УСТАНОВКА И РАЗВЕРТЫВАНИЕ УСПЕШНО ЗАВЕРШЕНЫ!")
    print("=" * 68)
    res = report.get("self_test", {})
    print(f"• OmniRoute (порт 20128): {'🟢 ONLINE' if res.get('omniroute') else '🔴 OFFLINE'}")
    print(f"• AionUi WebUI (порт 25808): {'🟢 ONLINE' if res.get('aionui') else '🔴 OFFLINE'}")
    print(f"• Gemini Farm инференс: {'🟢 РАБОТАЕТ' if res.get('gemini_farm') else '⚠️ ПРОВЕРИТЬ'}")
    print("=" * 68)
    print("Токены аккаунтов Gemini и базы данных развернуты в локальном защищенном хранилище.")
    print("При последующих запусках сервисы стартуют автоматически без ввода мастер-ключа.")
    print("Для повторной проверки работоспособности запустите:")
    print("    python Omni_Aion/verify_stack.py")
    print("=" * 68)


if __name__ == "__main__":
    main()
