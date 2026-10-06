"""Small JSON session files under Credentials/config. Mode 600. No logging of secrets."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def config_dir(root: Path | None = None) -> Path:
    from core.batman_mode import secrets_root

    path = secrets_root(root) / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_json(path: Path, payload: dict[str, Any]) -> None:
    body = dict(payload)
    body["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(body), encoding="utf-8")
    os.chmod(path, 0o600)


def clear_json(path: Path) -> None:
    if path.is_file():
        path.unlink()
