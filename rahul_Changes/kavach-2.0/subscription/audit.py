"""Audit log for subscription events (no secrets)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)


def audit_event(audit_dir: Path, action: str, result: str, **fields: Any) -> None:
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(IST).strftime("%Y-%m-%d")
        path = audit_dir / f"subscription_audit_{day}.jsonl"
        row = {
            "timestamp": datetime.now(IST).isoformat(),
            "action": action,
            "result": result,
        }
        for k, v in fields.items():
            if k.lower() in {"api_key", "password", "token", "secret", "authorization"}:
                continue
            row[k] = v
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception as exc:
        logger.warning("subscription audit write failed: %s", type(exc).__name__)
