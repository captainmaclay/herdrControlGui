# Herdr Research Control Center

<p align="center">
  <b>Distributed Multi-Subagent Orchestration, Network Isolation Benchmarking & Endpoint Multiplexer for Windows 11 & WSL2</b><br/>
  <i>An Academic Student Research and Engineering Benchmark Project</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Project-Student_Academic_Benchmark-8A2BE2" alt="Academic" />
  <img src="https://img.shields.io/badge/OS-Windows_11_%2B_WSL2_Ubuntu-blue?logo=windows" alt="OS" />
  <img src="https://img.shields.io/badge/Python-3.10+-yellow?logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/Research-Empirical_Benchmarking-4285F4" alt="Research" />
  <img src="https://img.shields.io/badge/Architecture-Multi--Subagent_Ensemble-D97706" alt="Architecture" />
  <img src="https://img.shields.io/badge/Localization-EN_%7C_RU-success?logo=translate" alt="Localization" />
  <img src="https://img.shields.io/badge/Security-AES--256--GCM_Zero--Trust-green?logo=security" alt="AES-256-GCM" />
</p>

---

# 🇬🇧 English: Research & Engineering Overview

## 1. Core Scientific Hypothesis

**"Can an ensemble of distributed, specialized subagents achieve higher reasoning accuracy, latency stability, and empirical throughput than a single monolithic frontier model?"**

Modern AI evaluation often treats large language models as monolithic reasoning engines. The **Herdr Benchmark & Orchestration Project** is an academic student research initiative investigating an alternative hypothesis:
> *A coordinated team of lightweight, domain-specialized worker agents—executing in isolated network runtimes with deterministic latency profiling—can match or surpass monolithic frontier models in complex task accuracy, resilience, and token efficiency.*

To gather rigorous, reproducible benchmark data, each subagent worker node must be isolated into independent network topologies to eliminate confounding variables such as shared socket contention, regional routing skew, and session state bleed-through.

---

## 2. Experimental Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Benchmark Orchestrator                          │
│               Evaluation Tasks & Comparative Datasets                  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Synthetic Prompts / Verification Run
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        OmniRoute & AionUi                              │
│              Dynamic Routing & Subagent Load Balancer                  │
├───────────────────────┬────────────────────────┬───────────────────────┤
│    Worker Node #1     │     Worker Node #2     │    Worker Node #3     │
│ Specialized Agent (A) │ Specialized Agent (B)  │ Specialized Agent (C) │
└───────────┬───────────┴───────────┬────────────┴───────────┬───────────┘
            │                       │                        │
     Isolated Socket         Isolated Socket          Isolated Socket
      (127.0.0.1:1081)        (127.0.0.1:1082)         (127.0.0.1:1083)
            │                       │                        │
            ▼                       ▼                        ▼
      Regional Node: FI       Regional Node: FR        Regional Node: FI
      (45.137.69.74)          (185.248.33.65)          (45.137.69.102)
            │                       │                        │
            ▼                       ▼                        ▼
      Worker Node A           Worker Node B            Worker Node C
       (Subagent 1)            (Subagent 2)             (Subagent 3)
```

### Why Network Isolation per Subagent Worker?
1. **Confounding Variable Elimination:** In distributed latency benchmarking, evaluating multiple concurrent model runs over a single egress socket introduces TCP queuing delays, socket port contention, and noisy-neighbor variance. Assigning each worker its own dedicated socket (`1081`, `1082`, `1083`...) guarantees pristine round-trip telemetry.
2. **Regional Latency Profiling:** Evaluates how model generation throughput fluctuates across different international routing backbones (Finland, France, etc.) under identical prompt distributions.
3. **Session State Isolation:** Prevents token cache contamination and ensures each subagent begins evaluation with a completely clean context state.
4. **Reproducibility Fail-Safe:** Connection circuit breakers ensure requests never fallback to unintended default network routes, preserving experimental data purity.
5. **Continuous Benchmarking Load Balancing:** Automatically handles rate windows and throughput ceilings across worker nodes, enabling long-running 24/7 benchmark evaluations without manual intervention.

---

## 3. Module Capabilities

### 📡 1. Routes & aiWatcher
- **aiWatcher (built-in service watchdog, replaces the "Herdr Multiplexer in WSL2" card):** every 5 s checks **AionUi** (:25808) and **OmniRoute** (:20128) in WSL2 and restarts them if they are down. Turn ON/OFF, per-service checkboxes, "Check now", live statuses and a log. Respects the `~/.aionui-web/.maintenance` flag, has a start-up grace period (no double starts), strips proxy variables when starting AionUi, and pauses itself if the old standalone `D:\My files\aiWatcher` is still running (one-click stop + autostart disable). Config: `watchdog_config.json`. Full docs: [docs/AIWATCHER.md](docs/AIWATCHER.md).
- **Latency & Socket Reachability:** Continuous millisecond-level telemetry to inference endpoints, logging round-trip times and DNS resolution latency.

### 🌐 2. Dedicated Socket & Proxy Manager
- **Per-Node Port Allocation:** Assigns isolated SOCKS5 ports (1081–1085+) to each test subagent node.
- **Regional Telemetry:** Validates egress point, regional IP, and round-trip ping time to verify node diversity.
- **Fail-Safe Routing:** Seamlessly redirects worker traffic to verified standby nodes within the same regional topology if a routing interface degrades.

### 🔮 3. Worker Node Session Profiles
- **Profile Orchestration:** Manages diverse model authorization sessions stored in isolated WSL2 runtimes.
- **Automated Round-Robin Evaluation:** Evaluates comparative throughput across multiple worker endpoints in multi-subagent combo configurations.
- **Zero-Trust Token Vault:** Cryptographic AES-256-GCM encryption of experimental credential artifacts, decrypting strictly on-demand for transient test runs.

### 🔐 4. Fault-Tolerant Zero-Trust Token Vault & Watchdog
- **Transient In-Memory Decryption:** 99% of the operational lifecycle keeps session credentials encrypted on disk with AES-256-GCM. Decryption strictly occurs within scoped ephemeral contexts (`auto_unlock_context()`) for millisecond-level inference calls.
- **Asynchronous Token Health Watchdog:** Continuously tracks token TTL and validity state without keeping master secrets in memory. Automated cache revalidation ensures benchmark worker subagents never experience auth timeouts mid-execution.
- **Fail-Safe Integrity Check:** Prevents state corruption during ungraceful process terminations, auto-recovering token metadata from cryptographically verified backups.

### 🛡️ 5. WSL2 Isolation Guard & Live DNS Leak Audit
- **Socket :1015 Strict Restriction:** Locks Worker Node Type-B runtimes in WSL2 to local egress SOCKS5 (`127.0.0.1:1015`) / HTTP (`127.0.0.1:11015`) proxy channels with remote DNS resolution.
- **Disable Warning Safeguard:** Protects benchmark environments against inadvertent network exposure. Toggling restriction to OFF prompts a confirmation warning before reverting WSL2 to Direct IP.
- **Live Isolation Audit Modal:** An integrated diagnostic window streaming live audit stages (proxy ports reachability, WSL2 configuration inspection, real outbound IP check, and DNS leak verification).
- **Windows Host Direct IP Guarantee:** Windows runtime environments (`node.exe`, desktop tools, and Smoozy audio host) permanently maintain unimpeded Direct IP access with zero firewall blocks.
- **Standalone Diagnostic Utility:** Fast, decoupled health check available via `D:\My files\check_smoozy_node\check_smoozy_node.bat`.

### ⚡ 6. Resilient Single-Instance Process Lifecycle
- **Fault-Tolerant Socket Lock (`SO_REUSEADDR`):** Eliminates `WinError 10048` address conflicts during rapid app restarts when the loopback socket (`127.0.0.1:38124`) resides in transient `TIME_WAIT` states.
- **Smart Handshake & Win32 Foreground Restorer:** If an instance is already running, subsequent launches transmit a `SHOW` signal over the loopback socket, leveraging native Win32 `SetForegroundWindow` API to restore the window from system tray directly into foreground focus.

---

# 🇷🇺 Русский: Обзор исследовательского проекта

## 1. Научная гипотеза проекта

**«Способен ли ансамбль специализированных сабагентов превзойти монолитные старшие модели по точности решения задач, устойчивости и эффективности вычислений?»**

Проект **Herdr Research Control Center** — это студенческий учебно-исследовательский и инженерный проект для верификации фундаментальной гипотезы автономного мультиагентного ИИ:
> *Коллаборативная связка узкоспециализированных сабагентов, распределённых по независимым изолированным узлам инференса, демонстрирует более высокую отказоустойчивость, низкую суммарную задержку и превосходную полноту решений по сравнению с единой монолитной моделью.*

Для получения научно достоверных данных бенчмарка каждый сабагент изолирован в собственном сетевом окружении (отдельный сокет, персональный сетевой шлюз, независимый контекстный кэш). Это полностью исключает интерференцию измерений, задержки в общих сокетах и взаимное загрязнение контекстов.

---

## 2. Ключевые инженерные решения

1. **Изоляция сетевых сокетов (1081, 1082, 1083...):**
   Гарантирует чистоту замеров задержки (latency) и скорости генерации токенов (tokens/sec) для каждого сабагента без взаимного влияния очередей сокетов.
2. **Балансировка нагрузки и ротация узлов (Round-Robin):**
   Позволяет проводить непрерывные длительные стресс-тесты и бенчмарки на тысячах задач, автоматически распределяя запросы по доступным узлам при заполнении окон пропускной способности.
3. **Криптографическая защита экспериментальных данных (AES-256-GCM):**
   Все учётные записи и токены рабочих узлов шифруются на диске и расшифровываются исключительно в оперативной памяти на миллисекунды вызова.
4. **Связка с OmniRoute и AionUi:**
   Интеграция с диспетчером сабагентов AionUi и шлюзом OmniRoute для проведения сравнительных тестов распределённых цепочек рассуждений.
5. **aiWatcher — встроенный сторож сервисов WSL2 (страница «Маршруты», вместо карточки «Herdr Multiplexer в WSL2»):**
   Каждые 5 с проверяет **AionUi** (:25808) и **OmniRoute** (:20128) и перезапускает упавшие. Возможности бывшего отдельного `D:\My files\aiWatcher`:
   - включение/выключение сторожа, галочки по каждому сервису, кнопка «Проверить сейчас», статусы и журнал событий;
   - настройки в `watchdog_config.json` (при первом запуске импортируются из `aiWatcher\watcher_config.json`, опасные старые команды заменяются);
   - флаг обслуживания `~/.aionui-web/.maintenance`: не мешает ремонту базы;
   - период прогрева `grace_seconds` против двойного запуска; таймаут 30 с на команду WSL;
   - запуск AionUi без `HTTP(S)_PROXY`/`ALL_PROXY` (иначе экран входа «Connection failed»);
   - обнаружение отдельного aiWatcher: встроенный на паузе, кнопка «Остановить внешний aiWatcher» (отключает и его автозапуск).

   Подробно: [docs/AIWATCHER.md](docs/AIWATCHER.md).
6. **Отказоустойчивое хранилище токенов и фоновый сторож сессий (Zero-Trust Token Vault & Watchdog):**
   - **Эфемерная расшифровка в памяти:** 99% времени учётные данные и токены сессий сабагентов находятся на диске в зашифрованном виде (AES-256-GCM). Расшифровка выполняется строго на миллисекунды сетевого запроса через изолированный контекстный менеджер `auto_unlock_context()`.
   - **Автоматический мониторинг срока жизни токенов:** Фоновый поток `TokenVaultWatchdog` валидирует состояние сессий без хранения мастер-ключа в памяти, предотвращая срыв длительных бенчмарков из-за протухания токенов.
   - **Самовосстановление метаданных:** При внезапных сбоях или отключении питания целостность хранилища автоматически восстанавливается из верифицированных резервных копий.
7. **Отказоустойчивый сокет запуска приложения (SO_REUSEADDR + Win32 Foreground Restorer):**
   - **Устранение залипания порта 38124:** Флаг `SO_REUSEADDR` позволяет приложению мгновенно стартовать при перезапуске, не дожидаясь истечения таймаута сетевого стека Windows `TIME_WAIT` (ранее вызывавшего ложное завершение `WinError 10048`).
   - **Интеллектуальный Handshake и поднятие из трея:** Повторный клик по ярлыку отправляет сигнал `SHOW` активному процессу через локальный сокет, а прямой вызов Win32 API (`SetForegroundWindow`) надёжно восстанавливает окно из системного трея поверх всех открытых окон.
   - **Автовосстановление при сбоях:** Если предыдущий процесс завершился аварийно, система выполняет автоматическую повторную попытку захвата порта, гарантируя запуск GUI без ложных сообщений «Уже запущено».
8. **Сетевое разграничение: изоляция WSL2 (:1015) и гарантированный Direct IP для Windows:**
   - **Принудительное замыкание на сокет :1015:** В среде WSL2 рабочие узлы Type-B изолированы на локальный сокет (`127.0.0.1:1015` SOCKS5 / `127.0.0.1:11015` HTTP) с удалённым разрешением DNS (Remote DNS), что исключает искажение сетевых замеров при бенчмаркинге.
   - **Предупреждение при отключении изоляции (Warning Dialog):** Попытка выключить ограничение вызывает подтверждающий диалог с предупреждением о том, что WSL2 получит прямой доступ к интернету.
   - **Полная свобода хоста Windows:** `node.exe` и системные утилиты Windows (Smoozy / `Audio Runtime Host.exe`) всегда работают напрямую (`Direct IP`), любые блокирующие правила брандмауэра исключены.
   - **Автономная утилита проверки:** Все проверки для хоста Windows и Smoozy вынесены в отдельную утилиту `D:\My files\check_smoozy_node\check_smoozy_node.bat`.
9. **Интерактивный аудит сетевой изоляции и защита от утечек DNS:**
   - Модальное окно диагностики в Herdr Center с цветным потоковым выводом проверки в реальном времени.
   - Полное тестирование: доступность сокетов 1015/11015, конфигурация сетевых параметров рабочего узла в WSL2, реальный сетевой тест и аудит DNS-запросов на отсутствие утечек.
