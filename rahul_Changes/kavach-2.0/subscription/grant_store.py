"""Persist once-daily subscription grant on disk."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from subscription.models import DayGrant, today_ist

logger = logging.getLogger(__name__)


def grant_path(grant_dir: Path, for_date: str | None = None) -> Path:
    d = for_date or today_ist().isoformat()
    return Path(grant_dir) / f"grant_{d}.json"


def write_grant(grant_dir: Path, grant: DayGrant) -> Path:
    grant_dir.mkdir(parents=True, exist_ok=True)
    path = grant_path(grant_dir, grant.valid_for_date)
    payload = json.dumps(grant.to_dict(), indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=str(grant_dir), prefix=".grant_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def read_grant(grant_dir: Path, for_date: str | None = None) -> DayGrant | None:
    path = grant_path(grant_dir, for_date)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return DayGrant.from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("failed to read subscription grant %s: %s", path, type(exc).__name__)
        return None
