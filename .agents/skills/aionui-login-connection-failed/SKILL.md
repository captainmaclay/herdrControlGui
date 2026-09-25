---
name: aionui-login-connection-failed
description: >-
  Починка AionUi, когда вместо чатов открывается /#/login и Sign In даёт "Connection failed, please try again",
  в aionui.log есть "/api/auth/status returned 502", база снова "database disk image is malformed",
  или у работающего AionUi пропали aionui-backend.db-wal/-shm. Учитывает гонку ремонта базы с aiWatcher.
---

# Скилл: AionUi — «Connection failed» на экране входа

Полный разбор: `D:\My files\aionUi_helper\docs\LOGIN_CONNECTION_FAILED.md`.

## Шаг 0. Если есть Herdr Control Center (предпочтительно)

Встроенный aiWatcher («Маршруты» → карточка aiWatcher) видит состояние «AionUi жив, но 502»
(статус «🟠 НЕ ОТВЕЧАЕТ (502)») и через ~15 с сам делает чистый перезапуск (`fix_aionui_login.py --fix`,
не чаще раза в 10 мин). Если статус «🔴 ЛЕЧЕНИЕ НЕ ПОМОГЛО», причина почти всегда в битой базе:
попросить пользователя нажать «🛠 Починить базу AionUi», затем «♻ Перезапустить AionUi (чисто)».
Herdr Control Center после обновления нужно перезапустить.

**Почему ошибка держалась часами (25.09):** процесс AionUi, запущенный вручную с прокси, продолжал работать.
Порт 25808 отвечал (502), поэтому ни старый aiWatcher (проверял процесс), ни проверка по порту его не трогали.
Ремонт базы при этом не запускался. Проверка здоровья `/api/auth/status` закрывает эту дыру.

## Шаг 1. Диагностика (ничего не меняет)

```bash
python3 "/mnt/d/My files/aionUi_helper/scripts/fix_aionui_login.py"
```

Читается вывод «Причины»:

| Фраза в выводе | Значит | Шаг |
| :--- | :--- | :--- |
| `aiWatcher не знает о флаге обслуживания` | конфиг сторожа старый, ремонт будет испорчен | 2 |
| `прокси-переменными без NO_PROXY` / `ручным` | AionUi запущен руками с прокси Claude | 4 |
| `aioncore не отвечает` / `База ... повреждена` / `-wal/-shm` | база битая или файлы удалены из-под процесса | 2 → 3 → 4 |
| `не слушает порт 25808` | AionUi не запущен | 4 |
| `OK: вход должен работать` | всё в порядке | 5 |

## Шаг 2. Убедиться, что aiWatcher не вмешается

```bash
grep -c maintenance "/mnt/d/My files/aiWatcher/watcher_config.json"   # 1 = ок
```

Если 0: попросить пользователя нажать **Turn OFF Watcher** в aiWatcher. Правильный `check_cmd` лежит
в `aionui_maint.WATCHER_CHECK_CMD`; после правки конфига aiWatcher нужно перезапустить (трей → Exit).
**Не** запускать `aiWatcher/test_watcher.py`: он удаляет `watcher_config.json`.

## Шаг 3. Ремонт базы (если нужен)

Пользователь запускает `repair_aionui.bat`. В выводе должно быть:
`✓ AionUi остановлен, повторных запусков за 8 сек не было` → `[('ok',)]` → `УСПЕХ`.
`Ремонт ОТМЕНЁН` означает, что сторож поднял AionUi. База не тронута, вернуться к шагу 2.

## Шаг 4. Чистый перезапуск

`fix_aionui_login.bat` (= `fix_aionui_login.py --fix`): ставит флаг `.maintenance`, останавливает AionUi
с проверкой, запускает `tmux new -d -s aionui env -u <6 прокси-переменных> NO_PROXY=* ... --no-open --port 25808`,
ждёт `/api/auth/status` < 500.

## Шаг 5. Проверка

```bash
curl --noproxy '*' -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:25808/api/auth/status   # 200
ls ~/.aionui-web/aionui-backend.db-wal ~/.aionui-web/aionui-backend.db-shm                        # оба есть
python3 -m pytest "/mnt/d/My files/aionUi_helper/tests" -q                                        # зелёные
```

Пользователю: `http://localhost:25808` → Ctrl+F5. Если выключали aiWatcher, вернуть Turn ON Watcher.

## Правила

- НИКОГДА не запускать `aionui-web start` из своего терминала: окружение агента содержит прокси Claude.
- НИКОГДА не останавливать AionUi и не трогать `aionui-backend.db*` без флага `~/.aionui-web/.maintenance`
  (используйте `aionui_maint.maintenance()` + `aionui_maint.stop_aionui()`).
- Бэкап базы только вместе с `-wal/-shm` (`aionui_maint.backup_db_files`).
- Экран логина при `auth=disabled` означает сбой связи, а не неверный пароль.
