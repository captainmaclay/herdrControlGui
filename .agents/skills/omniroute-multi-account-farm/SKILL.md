---
name: omniroute-multi-account-farm
description: >-
  Оркестрация распределённого кластера исследовательских сабагентов Gemini и Claude
  в OmniRoute с сетевой изоляцией сокетов, балансировкой Round-Robin и замером задержек.
---

# Скилл: Оркестрация кластера исследовательских сабагентов в OmniRoute

Этот скилл предназначен для управления пулом рабочих узлов (сабагентов) Gemini и Claude в балансировщике OmniRoute (`http://127.0.0.1:20128`), привязки независимых сетевых сокетов (1081–1085) и настройки бесперебойного Round-Robin инференса для сравнительного бенчмаркинга.

---

## Научная гипотеза
Экспериментальная проверка гипотезы: «Способен ли ансамбль специализированных сабагентов превзойти монолитные модели по показателям точности, отказоустойчивости и пропускной способности при решении комплексных задач?».

---

## Архитектурные правила

1. **Калибровка сетевых вызовов в WSL:**
   При вызове локальных эндпоинтов OmniRoute (`127.0.0.1:20128`) всегда используйте флаг `--noproxy '*'`:
   ```bash
   curl --noproxy '*' -s http://127.0.0.1:20128/v1/models -H "Authorization: Bearer sk-omniroute-secret"
   ```
2. **Изоляция сетевых сокетов:**
   Каждый узел должен быть жестко привязан к персональному порту в таблице `proxy_assignments` (`scope = 'account'`), чтобы исключить конкуренцию сетевых очередей и гарантировать чистоту замеров задержек (TTFT).

---

## Скрипт автоматического подключения нового узла в ансамбль

Запустите скрипт синхронизации внутри WSL:

```python
import sqlite3, json, datetime

conn = sqlite3.connect('/home/f/.omniroute/storage.sqlite')
cur = conn.cursor()

# 1. Поиск активных подключений agy
cur.execute("SELECT id, name, email FROM provider_connections WHERE provider = 'agy' AND is_active = 1")
agy_conns = cur.fetchall()

# 2. Чтение комбо gemini-farm
cur.execute("SELECT id, data FROM combos WHERE name = 'gemini-farm'")
row = cur.fetchone()
if not row:
    print("Ансамбль gemini-farm не найден!")
    exit(1)

combo_id, combo_data = row[0], json.loads(row[1])
existing_ids = {m['connectionId'] for m in combo_data['models']}

# 3. Регистрация недостающих узлов в ансамбле
added = 0
for conn_id, name, email in agy_conns:
    if conn_id not in existing_ids:
        model_num = len(combo_data['models']) + 1
        combo_data['models'].append({
            'id': f'gemini-farm-model-{model_num}-agy-gemini-pro-agent-{conn_id}',
            'kind': 'model',
            'model': 'agy/gemini-pro-agent',
            'providerId': 'agy',
            'connectionId': conn_id,
            'weight': 1,
            'label': name or f'Gemini Node ({email})'
        })
        added += 1

if added > 0:
    combo_data['version'] = combo_data.get('version', 2) + 1
    combo_data['updatedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cur.execute("UPDATE combos SET data = ?, updated_at = ? WHERE id = ?",
                (json.dumps(combo_data), combo_data['updatedAt'], combo_id))
    conn.commit()
    print(f"Добавлено узлов в ансамбль: {added}. Всего активных узлов: {len(combo_data['models'])}")
else:
    print(f"Все узлы уже подключены к ансамблю ({len(combo_data['models'])} моделей)")

conn.close()
```

---

## Контрольная верификация пропускной способности

```bash
export no_proxy="*"
curl -s -X POST http://127.0.0.1:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-omniroute-secret" \
  -d '{"model":"gemini-farm","messages":[{"role":"user","content":"say PONG"}],"max_tokens":10}'
```
