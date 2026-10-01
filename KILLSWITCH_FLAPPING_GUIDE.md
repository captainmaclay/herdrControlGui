# Инструкция по диагностике и устранению бага «мигания» Killswitch (цикличное переключение каждые 1.5 секунды)

## 1. Симптоматика проблемы

* **Действие пользователя:** В программе `vless2socks` сокет (например, `1015`) выключается (нажата кнопка «Stop»).
* **Поведение в `herdrCenter`:** Защитный механизм Killswitch корректно перехватывает событие и блокирует трафик (`killswitch_engaged = True`, статус `[KILLSWITCH BLOCK]`).
* **Аномалия (баг):** Соединение начинает самопроизвольно мигать:
  * на ~1.5 секунды соединение восстанавливается (`online: True`),
  * затем на ~1.5 секунды снова падает в Killswitch (`killswitch_engaged: True`),
  * цикл продолжается бесконечно, создавая флуктуации и дребезг в интерфейсе и сетевых правилах ядра.

---

## 2. Архитектурная первопричина (Root Cause)

Баг возникает на стыке двух компонентов: **внутреннего супервизора `vless2socks`** и **быстрого сторожа портов `herdrCenter`**.

### Цепочка событий:
1. **Единый файл конфигурации на все инстансы:**
   В модуле `vless2socks/xray/runner.py` функция `write_config()` записывала конфигурацию `xray-core` в единый жестко заданный путь:
   `C:\MyFiles\vless2socks\runtime\xray-config.json`
   без разделения по портам или номерам инстансов.
2. **Перезапись конфига:**
   Когда инстанс сокета `1015` запускался, он перезаписывал `runtime/xray-config.json`, указывая в нем порт `1015`.
3. **Остановка сокета 1015 и работа соседних инстансов:**
   Пользователь останавливал сокет `1015` в GUI `vless2socks`. Инстанс 1015 завершался. Однако соседние инстансы (например, инстанс `1081`) продолжали работать под управлением асинхронного супервизора `XrayProcess.supervise()`.
4. **Запуск «чужого» конфига зомби-супервизором:**
   При плановом реконнекте или сетевом сбое инстанса `1081` его супервизор вызывал метод `_spawn()` с путем `self.config_path` (`runtime/xray-config.json`). Так как этот файл был перезаписан сокетом `1015`, супервизор инстанса `1081` запускал `xray.exe` с параметрами порта **`1015`**!
5. **Таймаут проверки готовности (`_wait_ready`):**
   `xray.exe` запускался и открывал порт `1015`. Супервизор инстанса `1081` ждал открытия своего порта (`1081`). Так как открылся порт `1015`, а не `1081`, функция `_wait_ready()` по таймауту (~1.5–2 секунды) принудительно убивала процесс `xray.exe`.
6. **Цикл бэкоффа супервизора:**
   После убийства процесса супервизор инстанса `1081` ждал задержку бэкоффа (`RESTART_BACKOFF` ~1.0–2.0 с) и **снова** вызывал `_spawn()`, опять поднимая порт `1015`.
7. **Реакция Killswitch в `herdrCenter`:**
   Быстрый сторож `_schedule_fast_port_check` (интервал 1.5–2.0 с) в `herdrCenter` фиксировал появление сокета `1015` -> снимал Killswitch. Через 1.5 секунды `xray.exe` погибал -> Killswitch снова активировался.

---

## 3. Чек-лист диагностики при возникновении «мигания»

Если в будущем Killswitch снова начнет циклично переключаться, выполните следующие шаги в терминале PowerShell от имени администратора:

### Шаг 1. Проверка реального владельца порта 1015
Выясните, какой процесс прямо сейчас слушает сокет:
```powershell
Get-NetTCPConnection -LocalPort 1015 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, State, OwningProcess
```

### Шаг 2. Непрерывный мониторинг владельца сокета (поймать всплеск)
Если сокет открывается всего на 1–2 секунды, запустите мониторинг:
```powershell
for ($i = 0; $i -lt 10; $i++) {
    $conn = Get-NetTCPConnection -LocalPort 1015 -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Listen' }
    if ($conn) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)"
        Write-Host "[$i] ОБНАРУЖЕН ПРОЦЕСС: PID=$($conn.OwningProcess) Parent=$($proc.ParentProcessId) Name=$($proc.Name) Cmd=$($proc.CommandLine)"
    } else {
        Write-Host "[$i] Порт 1015 закрыт"
    }
    Start-Sleep -Milliseconds 500
}
```
*Если в выводе `Parent` указывает на процесс `main.py` с другим файлом конфигурации (например, `.instance_1.json`), значит, соседний инстанс поднимает чужой сокет.*

### Шаг 3. Проверка файлов в директории `runtime/`
```powershell
Get-ChildItem -Path C:\MyFiles\vless2socks\runtime
```
*Убедитесь, что там лежат индивидуальные файлы `xray-config-<PORT>.json`, а старый общий файл `xray-config.json` отсутствует.*

---

## 4. Обязательные правила для кода (Best Practices)

Чтобы не допустить рецидива этой проблемы:

### 1. Строгая изоляция конфигов по портам (`vless2socks/xray/runner.py`)
Функция генерации конфигурации обязана формировать уникальное имя файла, привязанное к локальному порту инстанса:
```python
def write_config(config: AppConfig, directory: str | os.PathLike[str], *, legacy_vnext: bool = False) -> Path:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    # Изолированное имя для каждого слушающего порта
    filename = f"xray-config-{config.listen_port}.json" if config.listen_port else "xray-config.json"
    path = target_dir / filename
    ...
    return path
```

### 2. Перегенерация конфига перед перезапуском в `supervise()`
Перед каждым вызовом `self._spawn()` супервизор должен заново записать собственный файл конфигурации, чтобы гарантировать его целостность и предотвратить подхват чужих устаревших данных:
```python
# vless2socks/xray/runner.py -> XrayProcess.supervise()
try:
    self.config_path = write_config(
        self.config, self.runtime_dir, legacy_vnext=self.legacy_vnext
    )
    await self._spawn()
    await self._wait_ready()
except XrayStartupError as exc:
    log.error("перезапуск не удался: %s", exc)
```

### 3. Очистка файлов при остановке (`stop()` в `gui.py` и `runner.py`)
При остановке инстанса файл конфигурации `xray-config-<PORT>.json` должен удаляться с диска, исключая сохранение «мусора»:
```python
# vless2socks/gui.py -> ProxyInstance.stop()
for cfg_name in (f"xray-config-{port}.json", "xray-config.json"):
    cfg_p = ROOT_DIR / "runtime" / cfg_name
    if cfg_p.exists():
        with contextlib.suppress(Exception):
            cfg_p.unlink()
```

### 4. Кэширование сетевой изоляции WSL2 (`node_isolate_manager.py`)
Вызовы `wsl` и манипуляции с `nftables` относительно медленные (~200–500 мс). Если статус сокета не изменился, повторный вызов должен быть мгновенно отсечен через кэш:
```python
_LAST_APPLIED_ISOLATION_KEY = None

def apply_wsl_isolation(port=1015, http_port=None, block_bypass_ports=None, killswitch=False, force=False):
    global _LAST_APPLIED_ISOLATION_KEY
    current_key = (int(port), int(http_port), bool(killswitch), tuple(sorted(block_bypass_ports)))
    if not force and _LAST_APPLIED_ISOLATION_KEY == current_key:
        return True
    ...
    if success:
        _LAST_APPLIED_ISOLATION_KEY = current_key
    return success
```

### 5. Защита от микроджиттера планировщика ОС (`claude_manager.py`)
При коротких таймаутах проверки портов (≤ 0.25 с) в Windows возможны ложные срабатывания из-за задержек переключения потоков. В функцию `check_port_accessible` добавляется короткая повторная попытка перед возвратом отрицательного результата.

---

## 5. Экстренный скрипт сброса (Rescue One-Liner)

Если система зависла в состоянии циклических перезапусков, выполните в PowerShell:
```powershell
# 1. Принудительно завершить все процессы xray и main.py
Get-Process -Name xray -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process | Where-Object { $_.Name -like "*python*" -and $_.CommandLine -like "*main.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# 2. Удалить старые несегментированные конфиги
Remove-Item "C:\MyFiles\vless2socks\runtime\xray-config.json" -Force -ErrorAction SilentlyContinue

# 3. Перезапустить нужные инстансы из GUI vless2socks
```
