"""Compatibility wrapper — history module owns samples and peaks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from vps_health_bot.history import peaks_today as _peaks_today
from vps_health_bot.history import record_sample as _record_sample


def record_sample(snap: Any) -> dict[str, Any]:
    return _record_sample(snap)


def peaks_today(now: datetime | None = None) -> dict[str, Any]:
    return _peaks_today(now)
