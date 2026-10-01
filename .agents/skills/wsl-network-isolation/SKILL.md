---
name: wsl-network-isolation
description: >-
  Управление сетевой изоляцией ядра Linux в WSL2 (Zero-Leak Jail), контроль nftables/iptables,
  блокировка обходных портов (2080), персистентный автозапуск и валидация отсечения прямых запросов.
---

# Скилл: Сетевая изоляция ядра Linux в WSL2 (Zero-Leak Guard)

Этот скилл полностью опирается на автоматизированное системное действие **`actions.wsl_isolation`** из пакета `actions` программы `herdrCenter`.

---

## 1. Режимы работы (Команды быстрого вызова)

### Активация строгой изоляции (Zero-Leak Jail):
```powershell
.\.venv\Scripts\python.exe -m actions wsl_isolation --mode apply --port 1015 --http-port 11015
```

### Контрольная верификация изоляции и отсутствия утечек:
```powershell
.\.venv\Scripts\python.exe -m actions wsl_isolation --mode verify
```

### Временное снятие изоляции (для экстренного прямого доступа):
```powershell
.\.venv\Scripts\python.exe -m actions wsl_isolation --mode teardown
```

---

## 2. Архитектура изоляции на уровне ядра Linux

1. **Таблица `nftables` (`herdr_filter`):**
   ```
   table inet herdr_filter {
       chain output {
           type filter hook output priority filter; policy accept;
           ip daddr 127.0.0.1 tcp dport 2080 counter reject
           oifname { "lo", "loopback0" } counter accept
           counter reject
       }
   }
   ```
   - Разрешен только локальный трафик loopback (`lo`, `loopback0`).
   - Заблокирован порт 2080 (сторонний обходной Windows-прокси).
   - Любые прямые исходящие сетевые пакеты в физический интернет (Direct WAN/LAN) отсекаются ядром на уровне хука `output priority filter`.

2. **Персистентность (Автозапуск при старте WSL/Windows):**
   - Правила сохраняются в `/etc/nftables.conf`.
   - В `/etc/wsl.conf` регистрируется директива `[boot] command=/usr/local/bin/herdr_boot_isolation.sh`.
   - Подсистема Linux даже после перезагрузки хоста стартует в защищенном режиме.

3. **Верификация Zero-Leak:**
   - Попытка выполнить прямой `curl http://1.1.1.1` без прокси завершается ошибкой сети.
   - Запросы через `socks5h://127.0.0.1:1015` и `http://127.0.0.1:11015` проходят штатно.
