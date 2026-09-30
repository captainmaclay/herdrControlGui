#!/usr/bin/env python3
"""
Gemini SOCKS5 Proxy Resilience & Auto-Failover Monitor for OmniRoute.
- Probes all Gemini SOCKS5 ports (1081-1088) with quick curl timeouts (3s).
- Dynamically maintains proxy_registry (status: 'active' vs 'down').
- Updates proxy_assignments (Tier 1 per-account staggered failover, Tier 2 provider pool).
- Writes heartbeat to ~/.omniroute/.gemini_resilience.heartbeat for aiWatcher.
- ZERO interference with Claude (:1015) or non-Gemini proxies.
- Can run once (--check), benchmark (--test), or as a daemon (--daemon).
"""

import sys
import os
import time
import json
import sqlite3
import datetime
import subprocess

DB_PATH = '/home/f/.omniroute/storage.sqlite'
HEARTBEAT_FILE = '/home/f/.omniroute/.gemini_resilience.heartbeat'
ALL_GEMINI_PORTS = [1081, 1082, 1083, 1084, 1085, 1086, 1087, 1088]
POLL_INTERVAL_S = 30.0

def touch_heartbeat():
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
        with open(HEARTBEAT_FILE, 'w') as f:
            f.write(str(int(time.time())))
    except Exception as e:
        print(f"[!] Warning: failed to touch heartbeat: {e}", file=sys.stderr)

def probe_socks5(port, timeout=3):
    """
    Test SOCKS5 proxy by making a quick request to an egress echo service via curl.
    Returns (is_alive, ip_address, elapsed_ms)
    """
    start = time.time()
    try:
        res = subprocess.run(
            ['curl', '-s', '--max-time', str(timeout), '--socks5-hostname', f'127.0.0.1:{port}', 'http://api.ipify.org'],
            capture_output=True,
            text=True,
            timeout=timeout + 1
        )
        elapsed = int((time.time() - start) * 1000)
        output = res.stdout.strip()
        if res.returncode == 0 and output and all(c.isdigit() or c == '.' for c in output):
            return True, output, elapsed
        else:
            err = res.stderr.strip() or f"exit code {res.returncode}, out: {output[:30]}"
            return False, err, elapsed
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return False, str(e), elapsed

def update_resilience(verbose=True):
    """
    Probes all ports. If any status changes, updates storage.sqlite.
    Returns (healthy_ports, dead_ports, state_changed)
    """
    touch_heartbeat()
    
    probe_results = {}
    healthy_ports = []
    dead_ports = []
    
    for port in ALL_GEMINI_PORTS:
        alive, ip_or_err, ms = probe_socks5(port)
        probe_results[port] = (alive, ip_or_err, ms)
        if alive:
            healthy_ports.append(port)
        else:
            dead_ports.append(port)
            
    if not healthy_ports:
        if verbose:
            print("[!] CRITICAL: All Gemini proxy ports are dead! Keeping existing state.", file=sys.stderr)
        return [], dead_ports, False

    if not os.path.exists(DB_PATH):
        if verbose:
            print(f"[!] Error: DB not found at {DB_PATH}", file=sys.stderr)
        return healthy_ports, dead_ports, False

    conn = sqlite3.connect(DB_PATH, timeout=10)
    cur = conn.cursor()
    
    # Check current DB status for gemini proxies
    cur.execute("SELECT id, status FROM proxy_registry WHERE id LIKE 'proxy_gemini_%'")
    current_status = dict(cur.fetchall())
    
    state_changed = False
    for port in ALL_GEMINI_PORTS:
        pid = f"proxy_gemini_{port}"
        expected_status = 'active' if port in healthy_ports else 'down'
        if current_status.get(pid) != expected_status:
            state_changed = True
            break
            
    # Also check legacy name
    if current_status.get('proxy_gemini_putative_1084') not in (None, 'down'):
        state_changed = True

    if not state_changed:
        if verbose:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Gemini proxy status unchanged ({len(healthy_ports)} alive, {len(dead_ports)} down).")
        conn.close()
        return healthy_ports, dead_ports, False

    # Apply changes
    print(f"\n[{datetime.datetime.now()}] State change detected! Updating OmniRoute routing tables...")
    unix_now = str(int(time.time()))
    db_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for port in ALL_GEMINI_PORTS:
        pid = f"proxy_gemini_{port}"
        pname = f"Gemini SOCKS5 (:{port})"
        new_status = 'active' if port in healthy_ports else 'down'
        
        cur.execute("SELECT id FROM proxy_registry WHERE id = ?", (pid,))
        if cur.fetchone():
            cur.execute("UPDATE proxy_registry SET status = ?, updated_at = ? WHERE id = ?", (new_status, unix_now, pid))
        else:
            cur.execute("""
                INSERT INTO proxy_registry (id, name, type, host, port, status, created_at, updated_at, source, family)
                VALUES (?, ?, 'socks5', '127.0.0.1', ?, ?, ?, ?, 'manual', 'auto')
            """, (pid, pname, port, new_status, unix_now, unix_now))
            
    cur.execute("UPDATE proxy_registry SET status = 'down', updated_at = ? WHERE id = 'proxy_gemini_putative_1084'", (unix_now,))
    
    # Retrieve active Gemini (agy) accounts
    cur.execute("SELECT id, name FROM provider_connections WHERE provider = 'agy' AND is_active = 1 ORDER BY id")
    agy_conns = cur.fetchall()
    
    healthy_proxy_ids = [f"proxy_gemini_{p}" for p in healthy_ports]
    
    # Tier 1: Per-account staggered failover chains
    for idx, (c_id, c_name) in enumerate(agy_conns):
        cur.execute("DELETE FROM proxy_assignments WHERE scope = 'account' AND scope_id = ?", (c_id,))
        
        matched_port = None
        for p in healthy_ports:
            if str(p) in (c_name or ""):
                matched_port = p
                break
                
        if matched_port:
            primary_pid = f"proxy_gemini_{matched_port}"
        else:
            primary_pid = healthy_proxy_ids[idx % len(healthy_proxy_ids)]
            
        ordered_proxies = [primary_pid] + [p for p in healthy_proxy_ids if p != primary_pid]
        for pos, pid in enumerate(ordered_proxies):
            cur.execute("""
                INSERT INTO proxy_assignments (proxy_id, scope, scope_id, position, created_at, updated_at)
                VALUES (?, 'account', ?, ?, ?, ?)
            """, (pid, c_id, pos, db_now, db_now))
            
    # Tier 2: Provider-level fallback pool ('agy')
    cur.execute("DELETE FROM proxy_assignments WHERE scope = 'provider' AND scope_id = 'agy'")
    for pos, pid in enumerate(healthy_proxy_ids):
        cur.execute("""
            INSERT INTO proxy_assignments (proxy_id, scope, scope_id, position, created_at, updated_at)
            VALUES (?, 'provider', 'agy', ?, ?, ?)
        """, (pid, pos, db_now, db_now))
        
    # Reset rotation cursors
    cur.execute("DELETE FROM proxy_scope_rotation WHERE scope_id IN (SELECT id FROM provider_connections WHERE provider = 'agy')")
    cur.execute("DELETE FROM proxy_scope_rotation WHERE scope = 'provider' AND scope_id = 'agy'")
    
    conn.commit()
    conn.close()
    
    print(f"  [+] Active IPs updated: {healthy_ports}")
    print(f"  [-] Dead IPs excluded: {dead_ports}")
    print("  [+] Claude proxy (:1015) untouched.")
    return healthy_ports, dead_ports, True

def test_gemini_farm(num_requests=5):
    import urllib.request
    print(f"\n[{datetime.datetime.now()}] Benchmarking {num_requests} requests to 'gemini-farm' via OmniRoute...")
    os.environ['no_proxy'] = '*'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    
    url = "http://127.0.0.1:20128/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-omniroute-secret"
    }
    payload = json.dumps({
        "model": "gemini-farm",
        "messages": [{"role": "user", "content": "Respond with single word: OK"}],
        "max_tokens": 10
    }).encode('utf-8')
    
    successes = 0
    total_time = 0
    for i in range(1, num_requests + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with opener.open(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                elapsed = time.time() - t0
                total_time += elapsed
                content = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                print(f"  Attempt {i}/{num_requests}: SUCCESS in {elapsed:.2f}s | Response: {repr(content)}")
                successes += 1
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  Attempt {i}/{num_requests}: FAILED in {elapsed:.2f}s | Error: {e}")
            
    print(f"\nResults: {successes}/{num_requests} successful.")
    if successes > 0:
        print(f"Average latency: {total_time / successes:.2f}s")

def run_daemon():
    print(f"[{datetime.datetime.now()}] Starting Gemini SOCKS5 Resilience Daemon (interval={POLL_INTERVAL_S}s)...")
    while True:
        try:
            update_resilience(verbose=False)
        except Exception as e:
            print(f"[!] Daemon error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_S)

def main():
    if '--daemon' in sys.argv:
        run_daemon()
    elif '--test' in sys.argv:
        update_resilience(verbose=True)
        test_gemini_farm(5)
    else:
        update_resilience(verbose=True)

if __name__ == '__main__':
    main()
