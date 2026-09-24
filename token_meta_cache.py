import json
import os
from pathlib import Path
import backup_manager

META_FILE = backup_manager.BASE_DIR / ".herdr_vault_meta.json"

def _load():
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"gemini": {}, "claude": {}}

def _save(d):
    try:
        META_FILE.parent.mkdir(parents=True, exist_ok=True)
        META_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def update_meta(service: str, profile_name: str, info: dict):
    safe_info = dict(info)
    safe_info.pop("id_token", None)
    safe_info.pop("access_token", None)
    safe_info.pop("refresh_token", None)
    safe_info.pop("file_path", None)
    d = _load()
    if service not in d:
        d[service] = {}
    d[service][profile_name] = safe_info
    _save(d)

def get_meta(service: str, profile_name: str) -> dict:
    return _load().get(service, {}).get(profile_name, {})
