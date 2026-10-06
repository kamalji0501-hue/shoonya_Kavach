"""Kotak Neo login fields and the trade session they produce. MPIN is not logged."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.session_file import clear_json, config_dir, load_json, save_json


def session_path(root: Path | None = None) -> Path:
    return config_dir(root) / "kotak_session.json"


def load_session(root: Path | None = None) -> dict[str, Any]:
    data = load_json(session_path(root))
    access = str(data.get("access_token") or "").strip()
    mobile = str(data.get("mobile") or "").strip()
    ucc = str(data.get("ucc") or "").strip()
    trade = str(data.get("trade_token") or "").strip()
    sid = str(data.get("sid") or "").strip()
    ready = bool(access and mobile and ucc and trade and sid)
    return {
        "access_token": access,
        "mobile": mobile,
        "ucc": ucc,
        "trade_token": trade,
        "sid": sid,
        "saved_at": str(data.get("saved_at") or ""),
        "ok": ready,
        "has_login_fields": bool(access and mobile and ucc),
    }


def save_session(
    access_token: str,
    mobile: str,
    ucc: str,
    trade_token: str = "",
    sid: str = "",
    root: Path | None = None,
) -> None:
    save_json(
        session_path(root),
        {
            "access_token": access_token.strip(),
            "mobile": mobile.strip(),
            "ucc": ucc.strip(),
            "trade_token": (trade_token or "").strip(),
            "sid": (sid or "").strip(),
        },
    )


def clear_session(root: Path | None = None) -> None:
    clear_json(session_path(root))
