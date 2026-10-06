"""Shoonya session for this Kavach instance only. Tokens stay in Credentials."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def _config_dir(root: Path | None = None) -> Path:
    from core.batman_mode import secrets_root

    path = secrets_root(root) / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_path(root: Path | None = None) -> Path:
    return _config_dir(root) / "shoonya.env"


def session_path(root: Path | None = None) -> Path:
    return _config_dir(root) / "shoonya_session.json"


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def load_env(root: Path | None = None) -> dict[str, str]:
    vals = _read_env(env_path(root))
    for key in ("SHOONYA_USER_ID", "SHOONYA_CLIENT_ID", "SHOONYA_SECRET_CODE"):
        if os.environ.get(key):
            vals[key] = os.environ[key].strip()
    return vals


def load_session(root: Path | None = None) -> dict[str, Any]:
    path = session_path(root)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    env = load_env(root)
    uid = str(data.get("uid") or env.get("SHOONYA_USER_ID") or "").strip()
    token = str(data.get("susertoken") or env.get("SHOONYA_SUSERTKEN") or "").strip()
    name = str(data.get("name") or env.get("SHOONYA_DISPLAY_NAME") or "").strip()
    return {
        "uid": uid,
        "susertoken": token,
        "saved_at": str(data.get("saved_at") or ""),
        "name": name,
        "ok": bool(uid and token),
    }


def save_session(uid: str, susertoken: str, root: Path | None = None, name: str = "") -> None:
    path = session_path(root)
    display = (name or "").strip() or "Gitesh"
    payload = {
        "uid": uid.strip(),
        "susertoken": susertoken.strip(),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": display,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)
    env = load_env(root)
    lines = [
        "# Shoonya order credentials for this Kavach instance.",
        f"SHOONYA_USER_ID={payload['uid']}",
        f"SHOONYA_DISPLAY_NAME={display}",
    ]
    if env.get("SHOONYA_CLIENT_ID"):
        lines.append(f"SHOONYA_CLIENT_ID={env['SHOONYA_CLIENT_ID']}")
    if env.get("SHOONYA_SECRET_CODE"):
        lines.append(f"SHOONYA_SECRET_CODE={env['SHOONYA_SECRET_CODE']}")
    env_file = env_path(root)
    env_file.write_text('\n'.join(lines) + '\n', encoding="utf-8")
    os.chmod(env_file, 0o600)


def clear_session(root: Path | None = None) -> None:
    path = session_path(root)
    if path.is_file():
        path.unlink()
