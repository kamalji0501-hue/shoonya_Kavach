"""Cached Dhan account holder label for the Kavach web desk chip.

Name comes from generateAccessToken (dhanClientName) when TOTP is configured,
or DHAN_CLIENT_NAME in dhan.env. Profile API has no holder name.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger("batman.dhan.account_label")

_lock = threading.Lock()
_cache: dict[str, str] = {"client_id": "", "name": "", "present": "0"}


def _cache_path() -> Path:
    try:
        from core.batman_mode import shared_data_dir

        return shared_data_dir() / "dhan_account_label.json"
    except Exception:
        return Path("/home/ubuntu/Trading_Runtime_Rahul/Data/data/shared/dhan_account_label.json")


def _load_disk() -> dict[str, str]:
    path = _cache_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def remember_dhan_name(name: str, client_id: str = "") -> None:
    """Persist holder name from a TOTP login / env so /api/me does not remint."""
    name = str(name or "").strip()
    client_id = str(client_id or "").strip()
    if not name and not client_id:
        return
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cur = _load_disk()
    if name:
        cur["name"] = name
    if client_id:
        cur["client_id"] = client_id
    path.write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")
    with _lock:
        if name:
            _cache["name"] = name
        if client_id:
            _cache["client_id"] = client_id
        _cache["present"] = "1"


def get_dhan_account_label() -> dict[str, str]:
    """Return {client_id, name, present} for the live Dhan order account."""
    client_id = ""
    token = ""
    try:
        from core.order_broker_select import dhan_client_and_token

        client_id, token = dhan_client_and_token()
    except Exception:
        pass
    if not client_id:
        try:
            from core.dhan_credentials import load_dhan_env_values

            vals = load_dhan_env_values()
            client_id = str(vals.get("DHAN_CLIENT_CODE") or vals.get("DHAN_CLIENT_ID") or "").strip()
        except Exception:
            pass

    disk = _load_disk()
    name = str(disk.get("name") or "").strip()
    try:
        from core.dhan_credentials import load_dhan_env_values

        env_name = str(load_dhan_env_values().get("DHAN_CLIENT_NAME") or "").strip()
        if env_name:
            name = env_name
    except Exception:
        pass

    present = bool(token or client_id)
    if not name and token and client_id:
        try:
            import httpx

            r = httpx.get(
                "https://api.dhan.co/v2/profile",
                headers={"access-token": token, "client-id": client_id, "Accept": "application/json"},
                timeout=12.0,
            )
            if r.status_code == 200:
                data = r.json() if isinstance(r.json(), dict) else {}
                for key in ("dhanClientName", "clientName", "name", "userName", "user_name"):
                    val = str(data.get(key) or "").strip()
                    if val:
                        name = val
                        break
        except Exception as exc:
            logger.warning("Dhan profile label fetch failed: %s", exc)

    if name and client_id:
        remember_dhan_name(name, client_id)

    return {"client_id": client_id, "name": name, "present": present}
