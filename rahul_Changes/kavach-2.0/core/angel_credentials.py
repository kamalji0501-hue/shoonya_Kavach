"""Angel session: client code, SmartAPI key, and pasted jwtToken."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.session_file import clear_json, config_dir, load_json, save_json


def session_path(root: Path | None = None) -> Path:
    return config_dir(root) / "angel_session.json"


def load_session(root: Path | None = None) -> dict[str, Any]:
    data = load_json(session_path(root))
    client = str(data.get("client_code") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    jwt = str(data.get("jwt") or "").strip()
    feed = str(data.get("feed_token") or "").strip()
    return {
        "client_code": client,
        "api_key": api_key,
        "jwt": jwt,
        "feed_token": feed,
        "saved_at": str(data.get("saved_at") or ""),
        "ok": bool(client and api_key and jwt),
    }


def save_session(client_code: str, api_key: str, jwt: str, feed_token: str = "", root: Path | None = None) -> None:
    save_json(
        session_path(root),
        {
            "client_code": client_code.strip(),
            "api_key": api_key.strip(),
            "jwt": jwt.strip(),
            "feed_token": (feed_token or "").strip(),
        },
    )


def clear_session(root: Path | None = None) -> None:
    clear_json(session_path(root))
