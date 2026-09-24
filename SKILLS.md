# Herdr AI Skills Integration Guide

В проекте **Herdr** активная настройка баз данных и провайдеров больше не выполняется "жестко" (hardcoded) кнопками из GUI. Вся оркестрация и применение конфигураций делегирована **интеллектуальным агентам (LLM)**.

Это руководство описывает скиллы (Skills) и инструкции, как ИИ-модель должна безошибочно настраивать интеграции `AionUi`, `Claude Code` и `OmniRoute (Gemini)`.

---

## Skill 1: Авто-настройка OmniRoute Gemini Farm

### Описание:
Агенты должны использовать этот чек-лист для прозрачной настройки кластера бесплатных аккаунтов Gemini в локальном балансировщике. Роутер переключает сессии при получении триггера `429 (Resource exhausted)`.

### Действия, требуемые от ИИ-Агента:
1. **Проверить SOCKS-сокеты Herdr:**
   Опросить локальные сокеты TCP начиная с `1081`. 
   Аккаунты Gemini Farm маппятся строго последовательно (`account-1` -> 127.0.0.1:1081).
2. **Зарегистрировать Провайдера в AionUi:**
   Во избежание ошибок с CLI утилитами используйте встроенный бинарный инструмент:
   ```bash
   "$AIONUI_HELPER_BIN" config providers create <<'JSON'
   {
     "name": "OmniRoute Gemini Farm",
     "platform": "custom",
     "base_url": "http://127.0.0.1:20128/v1"
   }
   JSON
   ```
3. **Обновить конфигурацию OmniRoute:**
   Вписать обнаруженные порты SOCKS в массив роутинга OmniRoute (`routes.config.json`).

---

## Skill 2: Быстрое внедрение Claude OAuth Tokens в AionUi (Anti-Fraud)

### Описание:
Claude (Anthropic) жестко банит аккаунты за резкую смену ASN или IP. Агент, внедряющий профиль из Herdr в AionUi, должен гарантировать, что трафик пойдет по SOCKS5 каналу `1015`.

### ТОЧНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ (Для ИИ-Агентов):
*Внимание ИИ:* Не пытайтесь использовать консольную команду `sqlite3` — она может отсутствовать в среде. Всегда используйте модуль `sqlite3` через `python3 -c "..."` для прямого межпроцессного доступа к `/home/f/.aionui-web/aionui-backend.db`.

**Шаг 1: Извлечение OAuth токена и создание Провайдера**
Прочитайте файл `credentials.json` напрямую, распакуйте токен и пропишите его как `api_key` стандартного провайдера Anthropic:
```bash
TOKEN=$(cat ~/.claude/.credentials.json | grep -o '\"accessToken\":\"[^\"]*\"' | cut -d '\"' -f 4)

"$AIONUI_HELPER_BIN" config providers create <<JSON
{
  "name": "Anthropic (Herdr OAuth)",
  "platform": "anthropic",
  "base_url": "https://api.anthropic.com/v1",
  "api_key": "${TOKEN}"
}
JSON
```

**Шаг 2: Внедрение Глобального Killswitch (Proxy 1015)**
Чтобы защитить аккаунт от утечки DNS и падения прокси, внедрите переменные окружения напрямую во все активные профили агентов `agent_metadata`:

```bash
python3 -c "
import sqlite3, json
db = sqlite3.connect('/home/f/.aionui-web/aionui-backend.db')
cursor = db.cursor()
cursor.execute('SELECT id, env_override FROM agent_metadata')
proxy_env = {
    'HTTP_PROXY': 'socks5h://127.0.0.1:1015',
    'HTTPS_PROXY': 'socks5h://127.0.0.1:1015',
    'ALL_PROXY': 'socks5h://127.0.0.1:1015'
}

for row_id, env_str in cursor.fetchall():
    env_dict = json.loads(env_str) if env_str else {}
    env_dict.update(proxy_env)
    cursor.execute('UPDATE agent_metadata SET env_override = ? WHERE id = ?', (json.dumps(env_dict), row_id))

conn.commit()
conn.close()
"
```

**Шаг 3: Регистрация команды /claude**
У главного агента Aion CLI (ID: `632f31d2`) необходимо открыть доступ к пользовательской команде.
```bash
python3 -c "
import sqlite3, json
db_path = '/home/f/.aionui-web/aionui-backend.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute('UPDATE agent_metadata SET available_commands = ? WHERE agent_id = ?', (json.dumps(['/claude']), '632f31d2')) 
conn.commit()
conn.close()
"
```

---

## Инструкция для вызова (Triggering)
Пользователь может написать агенту: *"Настрой aionui - возьми из herder claude аккаунт oauth2 файл и авторизуй меня"*. 

Получив эту команду, агент должен:
1. Прочесть этот файл (`SKILLS.md`).
2. Скопировать bash/python сниппеты (Шаги 1-3).
3. Привести AionUi в боевую готовность, не задавая пользователю дополнительных вопросов.