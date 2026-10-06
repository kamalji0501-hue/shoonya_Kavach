"""Load N-child fleet registry from Trading Runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _runtime_root() -> Path:
    env = (os.environ.get("TRADING_RUNTIME") or "").strip()
    if env:
        return Path(env)
    return Path("/home/ubuntu/Trading_Runtime_Rahul")


def registry_path() -> Path:
    return _runtime_root() / "Data" / "data" / "shared" / "fleet" / "child_registry.json"


def credentials_env_path() -> Path:
    return _runtime_root() / "Credentials" / "web" / "kavach_fleet.env"


def default_registry() -> dict[str, Any]:
    return {
        "master": {
            "id": "master-anand",
            "label": "Anand Master",
            "base_url": "http://127.0.0.1:8789",
            "aws_account": "aws-anand",
            "link_type": "local",
            "local": True,
        },
        "children": [],
        "poll": {"state_sec": 30, "margin_sec": 300},
    }


def load_registry() -> dict[str, Any]:
    path = registry_path()
    data = default_registry()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except Exception:
            pass
    master = dict(data.get("master") or {})
    master.setdefault("local", True)
    data["master"] = master
    kids = data.get("children") or []
    data["children"] = [k for k in kids if isinstance(k, dict)]
    poll = dict(data.get("poll") or {})
    poll.setdefault("state_sec", 30)
    poll.setdefault("margin_sec", 300)
    data["poll"] = poll
    return data


def resolve_copy_key(entry: dict[str, Any]) -> str:
    """Resolve per-child copy key from env, else shared master copy_key.txt."""
    env_name = str(entry.get("copy_key_env") or "").strip()
    if env_name:
        # Load fleet env + process env
        try:
            from dotenv import load_dotenv

            load_dotenv(credentials_env_path(), override=False)
        except Exception:
            pass
        val = (os.environ.get(env_name) or "").strip()
        if val:
            return val
    try:
        from modules.copy_trading import load_key

        return (load_key() or "").strip()
    except Exception:
        key_path = _runtime_root() / "Data" / "data" / "shared" / "copy_trading" / "copy_key.txt"
        if key_path.is_file():
            return key_path.read_text(encoding="utf-8").strip()
    return ""


def iter_desks(reg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    reg = reg or load_registry()
    out: list[dict[str, Any]] = []
    master = dict(reg.get("master") or {})
    if master.get("id"):
        master["role_tag"] = "MASTER"
        master["local"] = bool(master.get("local", True))
        out.append(master)
    for kid in reg.get("children") or []:
        if not isinstance(kid, dict):
            continue
        if kid.get("enabled") is False:
            continue
        row = dict(kid)
        row["role_tag"] = "CHILD"
        row["local"] = False
        out.append(row)
    return out
