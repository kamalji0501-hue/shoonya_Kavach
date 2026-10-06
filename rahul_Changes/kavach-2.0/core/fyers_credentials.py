"""Fyers app id plus the daily access token. The app secret is not stored."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.session_file import clear_json, config_dir, load_json, save_json


def session_path(root: Path | None = None) -> Path:
    return config_dir(root) / "fyers_session.json"


def load_session(root: Path | None = None) -> dict[str, Any]:
    data = load_json(session_path(root))
    app_id = str(data.get("app_id") or "").strip()
    token = str(data.get("access_token") or "").strip()
    return {
        "app_id": app_id,
        "access_token": token,
        "saved_at": str(data.get("saved_at") or ""),
        "ok": bool(app_id and token),
    }


def save_session(app_id: str, access_token: str, root: Path | None = None) -> None:
    save_json(session_path(root), {"app_id": app_id.strip(), "access_token": access_token.strip()})


def clear_session(root: Path | None = None) -> None:
    clear_json(session_path(root))
