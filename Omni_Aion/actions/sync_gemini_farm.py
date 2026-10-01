"""Блок 4: Синхронизация фермы аккаунтов Gemini и изоляция сокетов в OmniRoute.

Обеспечивает:
1. Регистрацию мастер-ключа 'sk-omniroute-secret' в api_keys.
2. Конфигурирование proxy_registry с критическим правилом изоляции:
   - Живой рабочий туннель :1015 (proxy_system_1015) переводится в статус 'active'.
   - Неактивные сокеты 1081-1090 помечаются 'disabled', что исключает сбои PROXY_UNREACHABLE
     во встроенном планировщике ProxyHealth и цикле round-robin ротации.
3. Сканирование профилей ~/.gemini/profiles/, шифрование токенов алгоритмом AES-GCM
   через OmniRoute encryption.mjs и регистрацию в provider_connections с проектом
   'aicode-consumers' (tier: free-tier).
4. Настройку двухуровневой отказоустойчивой привязки (proxy_assignments):
   - Позиция 0: проверенный сокет proxy_system_1015.
   - Позиции 1..N: резервные порты профилей.
5. Создание и обновление комбо-маршрутов 'gemini-farm' и 'auto/best-fast' (round-robin).
6. Сброс сработавших автоматических выключателей нагрузки (circuit breakers).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from actions.base import ActionResult, BaseAction, WSL_DISTRO

logger = logging.getLogger("herdr.omni_aion.actions.sync_gemini_farm")

# Встроенный эталонный скрипт Node.js для выполнения в окружении WSL2
FARM_SYNC_SCRIPT = r'''
import Database from '/usr/lib/node_modules/better-sqlite3/lib/index.js';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';

// 1. Чтение .env
const envPath = '/home/79251/.omniroute/.env';
if (existsSync(envPath)) {
  const envContent = readFileSync(envPath, 'utf8');
  for (const line of envContent.split('\n')) {
    const match = line.match(/^\s*([\w.-]+)\s*=\s*(.*)?\s*$/);
    if (match) {
      process.env[match[1]] = match[2].trim();
    }
  }
}

const { encryptCredential } = await import('/usr/lib/node_modules/omniroute/bin/cli/encryption.mjs');
const dbPath = '/home/79251/.omniroute/storage.sqlite';
const db = new Database(dbPath);

const secretKey = 'sk-omniroute-secret';
const now = new Date().toISOString();

// 2. Регистрация мастер-ключа
const existingKey = db.prepare('SELECT id FROM api_keys WHERE key = ?').get(secretKey);
if (!existingKey) {
  db.prepare(`
    INSERT INTO api_keys (id, name, key, key_prefix, created_at, machine_id, no_log)
    VALUES (?, ?, ?, ?, ?, ?, 0)
  `).run('omniroute-default-key', 'Herdr Default Key', secretKey, 'sk-omni', now, 'herdr-machine');
}

// 3. Регистрация прокси (1015 active, остальные disabled во избежание PROXY_UNREACHABLE)
const geminiProxies = [
  { id: 'proxy_system_1015', name: 'System SOCKS5 (:1015)', host: '127.0.0.1', port: 1015 },
  { id: 'proxy_gemini_1081', name: 'Gemini SOCKS5 (:1081)', host: '127.0.0.1', port: 1081 },
  { id: 'proxy_gemini_1082', name: 'Gemini SOCKS5 (:1082)', host: '127.0.0.1', port: 1082 },
  { id: 'proxy_gemini_1083', name: 'Gemini SOCKS5 (:1083)', host: '127.0.0.1', port: 1083 },
  { id: 'proxy_gemini_1084', name: 'Gemini SOCKS5 (:1084)', host: '127.0.0.1', port: 1084 },
  { id: 'proxy_gemini_1085', name: 'Gemini SOCKS5 (:1085)', host: '127.0.0.1', port: 1085 },
  { id: 'proxy_gemini_1086', name: 'Gemini SOCKS5 (:1086)', host: '127.0.0.1', port: 1086 },
  { id: 'proxy_gemini_1087', name: 'Gemini SOCKS5 (:1087)', host: '127.0.0.1', port: 1087 },
  { id: 'proxy_gemini_1088', name: 'Gemini SOCKS5 (:1088)', host: '127.0.0.1', port: 1088 },
  { id: 'proxy_gemini_1089', name: 'Gemini SOCKS5 (:1089)', host: '127.0.0.1', port: 1089 },
  { id: 'proxy_gemini_1090', name: 'Gemini SOCKS5 (:1090)', host: '127.0.0.1', port: 1090 },
];

const unixNow = String(Math.floor(Date.now() / 1000));
for (const p of geminiProxies) {
  const initialStatus = p.id === 'proxy_system_1015' ? 'active' : 'disabled';
  const row = db.prepare('SELECT id FROM proxy_registry WHERE id = ?').get(p.id);
  if (!row) {
    db.prepare(`
      INSERT INTO proxy_registry (id, name, type, host, port, status, created_at, updated_at, source, family)
      VALUES (?, ?, 'socks5', ?, ?, ?, ?, ?, 'manual', 'auto')
    `).run(p.id, p.name, p.host, p.port, initialStatus, unixNow, unixNow);
  } else {
    db.prepare('UPDATE proxy_registry SET status = ?, updated_at = ? WHERE id = ?').run(initialStatus, unixNow, p.id);
  }
}

// 4. Сканирование профилей и шифрование токенов
const profilesDir = '/home/79251/.gemini/profiles';
const profileEntries = existsSync(profilesDir) ? readdirSync(profilesDir, { withFileTypes: true }) : [];
const registeredConns = [];

for (const entry of profileEntries) {
  if (!entry.isDirectory()) continue;
  const profDir = join(profilesDir, entry.name);
  const cfgPath = join(profDir, 'profile_config.json');
  const tokenPath = join(profDir, 'antigravity-oauth-token');

  if (!existsSync(cfgPath) || !existsSync(tokenPath)) continue;

  try {
    const cfg = JSON.parse(readFileSync(cfgPath, 'utf8'));
    const tokenData = JSON.parse(readFileSync(tokenPath, 'utf8'));

    const email = cfg.profile_name || entry.name;
    const port = cfg.port || 1081;

    const rawToken = tokenData.token || {};
    const accessToken = rawToken.access_token || '';
    const refreshToken = rawToken.refresh_token || '';
    const expiry = rawToken.expiry || '';

    const encAccess = accessToken ? encryptCredential(accessToken) : null;
    const encRefresh = refreshToken ? encryptCredential(refreshToken) : null;

    const safeId = 'agy-conn-' + email.replace(/[^a-zA-Z0-9]/g, '_');
    const connName = `Gemini Node (${email})`;
    const providerSpecificData = JSON.stringify({
      clientProfile: 'cli',
      projectId: 'aicode-consumers',
      tier: 'free-tier',
    });

    const existingConn = db.prepare('SELECT id FROM provider_connections WHERE id = ?').get(safeId);
    if (!existingConn) {
      db.prepare(`
        INSERT INTO provider_connections (
          id, provider, auth_type, name, email, priority, is_active,
          access_token, refresh_token, token_expires_at, project_id,
          provider_specific_data, test_status, backoff_level, last_error,
          rate_limited_until, created_at, updated_at
        ) VALUES (
          ?, 'agy', 'oauth', ?, ?, 1, 1,
          ?, ?, ?, 'aicode-consumers',
          ?, 'active', 0, NULL,
          NULL, ?, ?
        )
      `).run(safeId, connName, email, encAccess, encRefresh, expiry, providerSpecificData, now, now);
    } else {
      db.prepare(`
        UPDATE provider_connections SET
          name = ?, email = ?, is_active = 1,
          access_token = ?, refresh_token = ?, token_expires_at = ?,
          project_id = 'aicode-consumers', provider_specific_data = ?,
          test_status = 'active', backoff_level = 0, last_error = NULL,
          rate_limited_until = NULL, updated_at = ?
        WHERE id = ?
      `).run(connName, email, encAccess, encRefresh, expiry, providerSpecificData, now, safeId);
    }

    registeredConns.push({ id: safeId, name: connName, email, port });
  } catch (err) {
    console.error('Error importing profile:', entry.name, err.message);
  }
}

// 5. Привязка сокетов (proxy_assignments) с pos 0 = proxy_system_1015
db.prepare("DELETE FROM proxy_assignments WHERE scope = 'account'").run();
for (const conn of registeredConns) {
  db.prepare(`
    INSERT INTO proxy_assignments (proxy_id, scope, scope_id, position, created_at, updated_at)
    VALUES ('proxy_system_1015', 'account', ?, 0, datetime('now'), datetime('now'))
  `).run(conn.id);

  const primaryPortProxy = `proxy_gemini_${conn.port}`;
  let pos = 1;
  const hasPrimary = geminiProxies.some(p => p.id === primaryPortProxy);
  if (hasPrimary && primaryPortProxy !== 'proxy_system_1015') {
    db.prepare(`
      INSERT INTO proxy_assignments (proxy_id, scope, scope_id, position, created_at, updated_at)
      VALUES (?, 'account', ?, ?, datetime('now'), datetime('now'))
    `).run(primaryPortProxy, conn.id, pos++);
  }
}

// Привязка на уровне провайдера
db.prepare("DELETE FROM proxy_assignments WHERE scope = 'provider' AND scope_id = 'agy'").run();
db.prepare(`
  INSERT INTO proxy_assignments (proxy_id, scope, scope_id, position, created_at, updated_at)
  VALUES ('proxy_system_1015', 'provider', 'agy', 0, datetime('now'), datetime('now'))
`).run();

// 6. Конфигурация комбо: gemini-farm и auto/best-fast
const modelsList = registeredConns.map((conn, idx) => ({
  id: `gemini-farm-model-${idx + 1}-agy-gemini-pro-agent-${conn.id}`,
  kind: 'model',
  model: 'agy/gemini-pro-agent',
  providerId: 'agy',
  connectionId: conn.id,
  weight: 1,
  label: conn.name
}));

const comboFarmData = {
  id: 'combo-gemini-farm',
  name: 'gemini-farm',
  strategy: 'round-robin',
  models: modelsList,
  config: {},
  sortOrder: 1,
  version: 2,
  createdAt: now,
  updatedAt: now
};

const existingFarm = db.prepare("SELECT id FROM combos WHERE name = 'gemini-farm'").get();
if (!existingFarm) {
  db.prepare(`
    INSERT INTO combos (id, name, data, sort_order, created_at, updated_at, context_cache_protection)
    VALUES (?, 'gemini-farm', ?, 1, ?, ?, 1)
  `).run(comboFarmData.id, JSON.stringify(comboFarmData), now, now);
} else {
  db.prepare('UPDATE combos SET data = ?, updated_at = ? WHERE id = ?').run(JSON.stringify(comboFarmData), now, existingFarm.id);
}

const comboBestFastData = {
  id: 'combo-auto-best-fast',
  name: 'auto/best-fast',
  strategy: 'round-robin',
  models: modelsList,
  config: {},
  sortOrder: 2,
  version: 2,
  createdAt: now,
  updatedAt: now
};

const existingBestFast = db.prepare("SELECT id FROM combos WHERE name = 'auto/best-fast'").get();
if (!existingBestFast) {
  db.prepare(`
    INSERT INTO combos (id, name, data, sort_order, created_at, updated_at, context_cache_protection)
    VALUES (?, 'auto/best-fast', ?, 2, ?, ?, 1)
  `).run(comboBestFastData.id, JSON.stringify(comboBestFastData), now, now);
} else {
  db.prepare('UPDATE combos SET data = ?, updated_at = ? WHERE id = ?').run(JSON.stringify(comboBestFastData), now, existingBestFast.id);
}

// 7. Сброс circuit breakers
db.prepare('UPDATE provider_connections SET backoff_level = 0, rate_limited_until = NULL, last_error = NULL').run();

db.close();
console.log(JSON.stringify({ success: true, accountsCount: registeredConns.length }));
'''


class SyncGeminiFarmAction(BaseAction):
    """Действие по синхронизации и настройке кластера Gemini в OmniRoute."""

    name: str = "sync_gemini_farm"
    description: str = "Синхронизация профилей Gemini, токенов, сокетов и комбо-маршрутов"

    def __init__(
        self,
        distro: str = WSL_DISTRO,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        super().__init__(dry_run=dry_run, verbose=verbose)
        self.distro = distro

    def run(self, **kwargs: Any) -> ActionResult:
        result = ActionResult(action_name=self.name, dry_run=self.dry_run)

        def step_sync_farm():
            if self.dry_run:
                return "dry_run", "Симуляция синхронизации аккаунтов Gemini и сокетов в storage.sqlite", {}

            # Записываем скрипт во временный файл в WSL2 и выполняем
            script_path = "/tmp/herdr_sync_gemini_farm.mjs"
            b64_script = FARM_SYNC_SCRIPT.encode("utf-8")
            write_cmd = f"node -e \"{FARM_SYNC_SCRIPT}\""
            
            # Безопасное сохранение и вызов
            save_cmd = f"cat << 'EOF' > {script_path}\n{FARM_SYNC_SCRIPT}\nEOF\nnode {script_path}"
            code, out, err = self.run_wsl_cmd(save_cmd, distro=self.distro, timeout=30.0)
            
            if code != 0:
                raise RuntimeError(f"Сбой синхронизации кластера Gemini: {err or out}")

            accounts_count = 0
            for line in out.splitlines():
                if '{"success":true' in line:
                    try:
                        data = json.loads(line)
                        accounts_count = data.get("accountsCount", 0)
                    except Exception:
                        pass

            return "ok", f"Синхронизировано {accounts_count} аккаунтов Gemini, сокет :1015 закреплен как основной", {
                "accounts_count": accounts_count
            }

        self.execute_step(result, "execute_farm_sync", step_sync_farm)

        return result.finish()
