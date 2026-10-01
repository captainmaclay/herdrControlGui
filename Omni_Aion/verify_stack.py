#!/usr/bin/env python3
"""
Скрипт сквозной проверки и тестирования работоспособности стека OmniRoute + AionUi.
Может вызываться автономно ИИ-агентом (Gemini) или человеком:
    python Omni_Aion/verify_stack.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# Обеспечиваем no_proxy для локальных запросов
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def test_omniroute_endpoint(url: str = "http://127.0.0.1:20128", token: str = "sk-omniroute-secret") -> dict:
    """Проверяет эндпоинт OmniRoute."""
    t0 = time.time()
    res = {"online": False, "models_count": 0, "latency_ms": 0, "error": None}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(
            f"{url}/v1/models",
            headers={"Authorization": f"Bearer {token}"}
        )
        with opener.open(req, timeout=5.0) as resp:
            elapsed = int((time.time() - t0) * 1000)
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", [])]
                res["online"] = True
                res["models_count"] = len(models)
                res["models"] = models[:5]
                res["latency_ms"] = elapsed
            else:
                res["error"] = f"HTTP {resp.status}"
    except Exception as e:
        res["error"] = str(e)
    return res


def test_aionui_endpoint(url: str = "http://127.0.0.1:25808") -> dict:
    """Проверяет эндпоинт AionUi."""
    t0 = time.time()
    res = {"online": False, "authenticated": False, "latency_ms": 0, "error": None}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(f"{url}/api/auth/status")
        with opener.open(req, timeout=5.0) as resp:
            elapsed = int((time.time() - t0) * 1000)
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                res["online"] = True
                res["authenticated"] = data.get("authenticated", False)
                res["latency_ms"] = elapsed
            else:
                res["error"] = f"HTTP {resp.status}"
    except Exception as e:
        res["error"] = str(e)
    return res


def test_gemini_farm_inference(url: str = "http://127.0.0.1:20128", token: str = "sk-omniroute-secret") -> dict:
    """Проверяет реальный инференс через шлюз OmniRoute в кластер токенов Gemini."""
    t0 = time.time()
    res = {"inference_ok": False, "model": "auto/best-fast", "response": "", "latency_ms": 0, "error": None}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    payload = json.dumps({
        "model": "auto/best-fast",
        "messages": [{"role": "user", "content": "Respond with single word: READY"}],
        "max_tokens": 15
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{url}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            },
            method="POST"
        )
        with opener.open(req, timeout=18.0) as resp:
            elapsed = int((time.time() - t0) * 1000)
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"].strip()
                res["inference_ok"] = True
                res["response"] = content[:100]
                res["latency_ms"] = elapsed
            else:
                res["error"] = f"HTTP {resp.status}"
    except Exception as e:
        res["error"] = str(e)
    return res


def run_full_stack_verification(verbose: bool = True) -> tuple[bool, dict]:
    """Запускает полную проверку всех сервисов."""
    if verbose:
        print("=" * 65)
        print("🔍 ЗАПУСК ПРОВЕРКИ СТЕКА OMNI_AION (OmniRoute + AionUi + Gemini)")
        print("=" * 65)

    omni_res = test_omniroute_endpoint()
    if verbose:
        status_str = "🟢 ONLINE" if omni_res["online"] else f"🔴 OFFLINE ({omni_res['error']})"
        print(f"1. OmniRoute (:20128) -> {status_str} [Задержка: {omni_res['latency_ms']} ms]")
        if omni_res["online"]:
            print(f"   Доступно моделей: {omni_res['models_count']} (в т.ч. {omni_res.get('models', [])})")

    aion_res = test_aionui_endpoint()
    if verbose:
        status_str = "🟢 ONLINE" if aion_res["online"] else f"🔴 OFFLINE ({aion_res['error']})"
        print(f"2. AionUi WebUI (:25808) -> {status_str} [Задержка: {aion_res['latency_ms']} ms]")

    gemini_res = test_gemini_farm_inference()
    if verbose:
        status_str = "🟢 УСПЕШНО" if gemini_res["inference_ok"] else f"🔴 ОШИБКА ({gemini_res['error']})"
        print(f"3. Инференс Gemini Farm -> {status_str} [Задержка: {gemini_res['latency_ms']} ms]")
        if gemini_res["inference_ok"]:
            print(f"   Ответ модели: {gemini_res['response']}")

    all_ok = omni_res["online"] and aion_res["online"] and gemini_res["inference_ok"]

    if verbose:
        print("=" * 65)
        if all_ok:
            print("✅ ВСЕ КОМПОНЕНТЫ СТЕКА OMNI_AION ФУНКЦИОНИРУЮТ ШТАТНО!")
        else:
            print("⚠️ ОБНАРУЖЕНЫ СБОИ В РАБОТЕ КОМПОНЕНТОВ СТЕКА.")
        print("=" * 65)

    return all_ok, {
        "omniroute": omni_res,
        "aionui": aion_res,
        "gemini_farm": gemini_res,
        "all_ok": all_ok
    }


if __name__ == "__main__":
    ok, _ = run_full_stack_verification(verbose=True)
    sys.exit(0 if ok else 1)
