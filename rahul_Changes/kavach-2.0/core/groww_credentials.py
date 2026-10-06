"""Groww access token for this Kavach instance. The token expires at 6:00 AM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.session_file import clear_json, config_dir, load_json, save_json


def session_path(root: Path | None = None) -> Path:
    return config_dir(root) / "groww_session.json"


def load_session(root: Path | None = None) -> dict[str, Any]:
    data = load_json(session_path(root))
    token = str(data.get("token") or "").strip()
    return {"token": token, "saved_at": str(data.get("saved_at") or ""), "ok": bool(token)}


def save_session(token: str, root: Path | None = None) -> None:
    save_json(session_path(root), {"token": token.strip()})


def clear_session(root: Path | None = None) -> None:
    clear_json(session_path(root))
