"""Write Kavach NIFTY cache from Datafeedbot index ticks (sid 13)."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("datafeedbot.nifty_cache")

_IST = ZoneInfo("Asia/Kolkata")
_CACHE = Path("/home/ubuntu/Trading_Runtime_Rahul/Data/data/shared/nifty_ltp_cache.json")


def cache_path() -> Path:
    env = (os.environ.get("NIFTY_LTP_CACHE") or "").strip()
    if env:
        return Path(env)
    return _CACHE


def write_nifty_ltp_cache(ltp: float, source: str = "dhan_websocket") -> None:
    px = float(ltp)
    if px <= 0:
        return
    now = datetime.now(_IST).isoformat()
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ltp": px,
        "updated_at": now,
        "last_changed_at": now,
        "source": source,
        "feed_healthy": True,
        "poll_interval_seconds": 2,
        "consecutive_failures": 0,
        "unchanged_seconds": 0.0,
        "collector": "datafeedbot",
        "collector_pid": os.getpid(),
        "ready_for_consumers": True,
        "cache_age_seconds": 0.0,
    }
    tmp = path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    logger.debug("NIFTY cache ltp=%.2f path=%s", px, path)

def cache_age_seconds() -> float:
    path = cache_path()
    if not path.is_file():
        return 1e9
    return max(0.0, time.time() - path.stat().st_mtime)


def kite_index_ltp(api_key: str, access_token: str) -> float | None:
    """One Kite LTP for NSE:NIFTY 50. No Dhan REST / no extra Dhan WS."""
    key = (api_key or "").strip()
    tok = (access_token or "").strip()
    if not key or not tok:
        return None
    q = urllib.parse.urlencode([("i", "NSE:NIFTY 50")])
    req = urllib.request.Request(
        "https://api.kite.trade/quote/ltp?" + q,
        headers={"X-Kite-Version": "3", "Authorization": f"token {key}:{tok}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("Kite NIFTY LTP failed: %s", type(exc).__name__)
        return None
    if body.get("status") != "success":
        logger.warning("Kite NIFTY LTP rejected: %s", (body.get("message") or "unknown")[:80])
        return None
    data = body.get("data") or {}
    row = data.get("NSE:NIFTY 50") or (next(iter(data.values()), {}) if data else {})
    last = (row or {}).get("last_price") if isinstance(row, dict) else None
    try:
        px = float(last)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None

