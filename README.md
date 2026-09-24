# Herdr Control Center

<p align="center">
  <b>Multi-Account AI Routing, SOCKS5 Tunneling & OAuth Session Multiplexer for Windows 11 & WSL2</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/OS-Windows_11_%2B_WSL2_Ubuntu-blue?logo=windows" alt="OS" />
  <img src="https://img.shields.io/badge/Python-3.10+-yellow?logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/Gemini-OAuth2_Advanced_(No_API_Keys)-4285F4?logo=google" alt="Gemini" />
  <img src="https://img.shields.io/badge/Claude-Direct_IP-D97706?logo=anthropic" alt="Claude" />
  <img src="https://img.shields.io/badge/Localization-EN_%7C_RU-success?logo=translate" alt="Localization" />
  <img src="https://img.shields.io/badge/Encryption-AES--256--GCM-green?logo=security" alt="AES-256-GCM" />
</p>

![screenshot](https://i.imgur.com/3qZUEsZ.png)

---

# 🇬🇧 English Documentation

## 1. What is Herdr Control Center?
**Herdr Control Center** is a professional desktop GUI and routing orchestrator designed for AI developers, multi-agent workflows, and power users running LLM agents (such as Google Gemini, Antigravity CLI `agy`, and Anthropic Claude Code) inside **WSL2 (Ubuntu)** while controlling everything seamlessly from **Windows 11**.

### 💡 Why is this software necessary?
1. **Zero API Key Costs for Gemini:** Google AI Pro / Gemini Advanced accounts provide high usage limits via browser OAuth tokens. Standard tools force paid API keys. Herdr Control Center manages native Google OAuth2 tokens directly.
2. **Anti-Ban IP Isolation:** Google fraud systems detect multiple accounts accessing servers from the exact same residential IP. Herdr assigns **each Google profile its own isolated SOCKS5 proxy port** (e.g., Account 1 on `1081`, Account 2 on `1082`, etc.).
3. **Automated Quota Failover:** When an account hits a `429 Too Many Requests` or `RESOURCE_EXHAUSTED` limit during long agent runs, a background WSL2 daemon instantly rotates the session and switches SOCKS5 tunnels to the next account without interrupting work.
4. **Clean Network Routing:** Automatically routes Google Gemini traffic through dedicated SOCKS5 proxies while sending Anthropic Claude requests over your original, unproxied IP.
5. **Zero-Friction Portability:** Built-in military-grade AES-256-GCM backups allow moving your entire workspace, saved OAuth sessions, and proxy settings to a bare-metal machine in a single click.

---

## 2. Key Features

### 📡 1. Routes & Herdr Status
- **Real-Time Multiplexer Inspection:** Interacts with the active WSL2 Herdr daemon, displaying running agent panes (`agy`, `claude`, terminals).
- **Gemini SOCKS5 Gate Status:** Probes local SOCKS5 tunnel reachability and verifies direct latency to Google Generative Language endpoints.
- **Claude Direct IP Gate:** Verifies direct Anthropic API availability and detects your public residential/direct IP.

### 🛡️ 2. SOCKS5 Proxy Manager
- **Auto-Increment Ports:** Automatically registers SOCKS5 inbound proxies starting from port `1081` (+1 per profile).
- **10-Row Pagination:** Strict 10-row page view with dynamic page switching.
- **Live Geolocation & Ping:** Identifies the true exit IP, country, and round-trip response time via `ip-api.com`.
- **Intelligent Auto-Failover:** If a proxy tunnel goes down, the system automatically selects another operational proxy from your list, giving strict priority to the **same country** (e.g., Finland ➔ Finland) to protect Google security trust scores.

### 🔮 3. Gemini OAuth Profiles
- **Multi-Account Storage:** Stores multiple authorized Google profiles in `~/.gemini/profiles/`.
- **1-Click Switching:** Instantly changes the active token in WSL2 and rewires `ALL_PROXY` / `HTTPS_PROXY` environment variables.
- **Live Token Validation:** Queries Google OAuth2 endpoints to inspect token expiration and validity without touching CLI terminals.
- **Background Quota Guard:** A resilient background daemon monitors `cli.log` for HTTP 429 quota exhaustion errors and performs round-robin account switching.

### 📊 4. Geolocation Strategy & Connection Time Analytics
- **Duration-Based Geolocation Preference:** Evaluates account regional habits using **cumulative active connection time** (instead of launch counts), identifying the true primary country.
- **Continuous Connection Telemetry:** Logs active session durations and updates heartbeat records in `strategy_history.json`.
- **Compliance Badges:**
  - `🟢 OK • Region Confirmed`: The currently assigned SOCKS5 matches the account's historical connection time (>50% duration).
  - `🟡 Nice to change`: The account is currently routed through an unfamiliar country, risking Google verification checks. Provides a 1-click button to reassign to the recommended country.
- **Extended Logs:** Pop-up window with session durations and total connection time per unique IP.

### 💾 5. Encrypted Backup & Factory Reset
- **AES-256-GCM Encryption:** All settings, tokens, OAuth profiles, and logs are encrypted with a user-defined master password using PBKDF2-HMAC-SHA256 (600,000 iterations).
- **SHA-256 Password Fingerprint:** Displays a live hash fingerprint of your password with an eye 👁️ toggle for password visibility.
- **Bare-Metal Restore:** Importing a `.hbak` archive immediately restores all Windows and WSL2 files without needing previous logins.
- **Automated Scheduled Backups:** Configurable timer (e.g., every 12 hours) automatically saves encrypted snapshots to your designated directory (default: `%USERPROFILE%\Documents\HerdrBackups`).
- **⚠️ Danger Zone (Wipe All Data):** Complete factory reset with two-step confirmation that securely purges all local logs, OAuth tokens, and profile stores.

### 🌐 6. Instant Localization (EN / RU)
- **English Default:** Starts in English by default, ready for international distribution.
- **1-Click Language Switch:** Toggle instantly between English and Russian without restarting the application.
- **Persistent State:** Saves chosen language in `settings.json`.

---

## 3. Architecture

```
┌────────────────────────────────────────────────────────┐
│                   Windows 11 Host                      │
│                                                        │
│  [Herdr Control Center GUI] (Tkinter, Catppuccin Mocha)│
│    ├── sync_manager.py     (Route ping & telemetry)   │
│    ├── proxy_manager.py    (SOCKS5 list & IP geo)      │
│    ├── gemini_manager.py   (Profile & token linkage)   │
│    ├── strategy_manager.py (Geo-tendency analytics)    │
│    ├── backup_manager.py   (AES-256-GCM snapshots)     │
│    ├── i18n.py             (Bilingual engine EN/RU)    │
│    └── settings_manager.py (Persistent configs)        │
└──────────────────────────┬─────────────────────────────┘
                           │ (WSL Interop / UNC Paths)
┌──────────────────────────▼─────────────────────────────┐
│                     WSL2 Ubuntu                        │
│                                                        │
│  ~/.gemini/                                            │
│    ├── antigravity-cli/active_proxy.env                │
│    ├── antigravity-cli/antigravity-oauth-token         │
│    ├── profiles/account-1/, account-2/...              │
│    └── gemini-oauth-guard (Daemon monitoring 429s)     │
│                                                        │
│  SOCKS5 Inbounds: 127.0.0.1:1081, 1082, 1083...         │
└────────────────────────────────────────────────────────┘
```

---

## 4. Installation & Setup

### Prerequisites
- Windows 11 (or Windows 10 with WSL2).
- Ubuntu installed in WSL2 (`wsl -d Ubuntu`).
- Python 3.10+ installed on Windows.

### Quick Start
1. Clone the repository:
   ```cmd
   git clone https://github.com/captainmaclay/herdrControlGui.git
   cd herdrControlGui
   ```
2. Run the automated setup or launch script:
   ```cmd
   gui.bat
   ```
   *The launcher automatically initializes a Python virtual environment (`.venv`) and installs all dependencies from `requirements.txt`.*

---

## 5. Standalone Release Executable (.exe)
You can download the pre-compiled `HerdrControlCenter.exe` from the [GitHub Releases](https://github.com/captainmaclay/herdrControlGui/releases) page.

To compile it yourself from source:
```cmd
python -m pip install pyinstaller
pyinstaller --noconsole --onefile --name HerdrControlCenter --hidden-import=cryptography --hidden-import=pystray --hidden-import=PIL --hidden-import=requests --hidden-import=socks --hidden-import=dotenv --hidden-import=tkinter --hidden-import=i18n config_app.py
```
The output file will be generated in `dist/HerdrControlCenter.exe`.

---
---

# 🇷🇺 Документация на русском языке

## 1. Что такое Herdr Control Center?
**Herdr Control Center** — это настольный графический центр управления и интеллектуальный сетевой маршрутизатор для разработчиков и пользователей автономных мультиагентных систем. Программа связывает графическую среду **Windows 11** и виртуальное рабочее пространство **WSL2 (Ubuntu)**, координируя работу агентов Google Gemini (`agy`) и Anthropic Claude Code.

### 💡 Зачем нужна эта программа?
1. **Работа с Gemini AI Pro без платных API-ключей:** официальные подписки Google AI Pro и Gemini Advanced предоставляют большие квоты через веб-авторизацию. Herdr Control Center позволяет использовать нативные OAuth2 токены вместо покупки дорогих API-ключей.
2. **Защита от блокировок Google (Изоляция IP):** алгоритмы безопасности Google выявляют мультиаккаунтинг, когда несколько профилей работают с одного и того же IP. Программа закрепляет за **каждым Google-профилем персональный SOCKS5-порт** (Аккаунт 1 ➔ `1081`, Аккаунт 2 ➔ `1082` и т.д.).
3. **Автоматическая ротация при исчерпании лимитов:** фоновый сторож в WSL2 непрерывно мониторит ошибки `429 Too Many Requests` / `RESOURCE_EXHAUSTED`. При исчерпании лимита активного аккаунта система бесшовно переключает токен и туннель на следующий профиль.
4. **Раздельная маршрутизация:** трафик Gemini туннелируется через выделенный SOCKS5, в то время как Anthropic Claude работает напрямую через ваш чистый оригинальный IP.
5. **Мгновенный перенос в «голую» систему:** встроенное шифрование AES-256-GCM позволяет упаковать все ключи, токены и настройки в один защищенный файл и восстановить рабочее окружение на новом компьютере в 1 клик.

---

## 2. Обзор функционала по вкладкам

### 📡 1. Маршруты & Herdr
- **Мониторинг сессий Herdr в WSL2:** считывание активных панелей агентов (`agy`, `claude`, bash) напрямую из мультиплексора.
- **Шлюз Google Gemini:** проверка доступности локального SOCKS5 порта и времени отклика (latency) серверов Google API.
- **Шлюз Anthropic Claude:** проверка прямого доступа без проксирования и определение белого внешнего IP.

### 🛡️ 2. SOCKS5 Прокси
- **Авто-инкремент портов:** порты назначаются от `1081` с шагом `+1` для каждого нового аккаунта.
- **Пагинация:** удобная постраничная навигация строго по 10 строк на страницу.
- **Проверка в реальном времени:** пинг туннеля, получение внешнего IP и страны выхода через `ip-api.com`.
- **Автоподбор при сбое (с приоритетом той же страны):** если текущий прокси вышел из строя, программа автоматически подбирает рабочий SOCKS5 из списка, отдавая приоритет IP-адресам **той же страны** (например, Финляндия ➔ Финляндия), чтобы не вызывать подозрений Google Antifraud.

### 🔮 3. Gemini OAuth
- **Хранение профилей:** безопасное хранение профилей Google в `~/.gemini/profiles/`.
- **Переключение в 1 клик:** смена активного аккаунта с автоматической перезаписью переменных окружения в WSL2 (`ALL_PROXY`, `HTTPS_PROXY`).
- **Онлайн-валидация:** проверка валидности токена в Google API без открытия терминала.
- **Фоновый сторож автосмены (Guard):** демон в WSL2 переключает профили по кругу при получении лимитов квоты 429.

### 📊 4. Стратегия & Аналитика времени соединений
- **Оценка стратегии по совокупному времени соединений:** предпочтение страны отдается на основе **общей длительности активных подключений** (вместо счетчика запусков).
- **Непрерывный учет сессий:** запись времени начала, длительности каждого сеанса и автоматическое продление активного соединения в `strategy_history.json`.
- **Система рекомендаций:**
  - `🟢 ОК • Регион подтвержден`: текущий SOCKS5 соответствует доминирующей стране по времени соединений (минимальный риск блокировок).
  - `🟡 Nice to change`: аккаунт запущен через непривычный регион. Выводится совет с накопленным временем и кнопка быстрой смены на рекомендуемую страну.
- **Расширенный лог:** детальный журнал сессий с длительностью и суммарное время работы по уникальным IP-адресам.

### 💾 5. Резервное копирование & Сброс
- **Шифрование AES-256-GCM:** архивы шифруются ключом на базе мастер-пароля (PBKDF2, соль 16 байт, 600 000 итераций).
- **Хэш-отпечаток пароля:** вывод SHA-256 Fingerprint пароля и кнопка 👁️ для скрытия/показа.
- **Автобэкап по расписанию:** таймер создания снимков с интервалом в часах в заданную папку (по умолчанию: `%USERPROFILE%\Documents\HerdrBackups`).
- **Импорт в чистую программу:** файл `.hbak` полностью восстанавливает настройки Windows и сессии WSL2.
- **⚠️ Опасная зона (Wipe All Data):** кнопка полной очистки всех локальных логов, токенов и профилей с двойным подтверждением.

### 🌐 6. Мгновенная локализация (EN / RU)
- **Английский язык по умолчанию:** международная версия из коробки.
- **Переключение в 1 клик:** мгновенная смена языка интерфейса без перезапуска приложения.
- **Сохранение состояния:** выбранный язык сохраняется в `settings.json`.

---

## 3. Установка и запуск

1. Склонируйте репозиторий:
   ```cmd
   git clone https://github.com/captainmaclay/herdrControlGui.git
   cd herdrControlGui
   ```
2. Запустите центр управления:
   ```cmd
   gui.bat
   ```
   *Скрипт автоматически создаст изолированное окружение `.venv` и установит необходимые библиотеки из `requirements.txt`.*

---

## 4. Готовый исполняемый файл (.exe)
Вы можете скачать собранный автономный файл `HerdrControlCenter.exe` со страницы [GitHub Releases](https://github.com/captainmaclay/herdrControlGui/releases).

Для самостоятельной компиляции из исходного кода:
```cmd
python -m pip install pyinstaller
pyinstaller --noconsole --onefile --name HerdrControlCenter --hidden-import=cryptography --hidden-import=pystray --hidden-import=PIL --hidden-import=requests --hidden-import=socks --hidden-import=dotenv --hidden-import=tkinter --hidden-import=i18n config_app.py
```
Готовый релизный файл появится в папке `dist/HerdrControlCenter.exe`.
