"""TTL-cached broker margin (fundlimit / get_balance)."""

from __future__ import annotations

import threading
import time
from typing import Any

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"ts": 0.0, "value": None, "error": None, "inflight": False}
TTL_SEC = 300.0


def _fetch_balance() -> float:
    from web.runtime import get_runtime

    rt = get_runtime()
    if rt is None or not getattr(rt, "broker", None):
        raise RuntimeError("broker_unavailable")
    return float(rt.broker.get_balance())


def get_margin(force: bool = False) -> dict[str, Any]:
    now = time.time()
    with _LOCK:
        age = now - float(_CACHE.get("ts") or 0)
        if not force and _CACHE.get("value") is not None and age < TTL_SEC:
            return {
                "ok": True,
                "available": _CACHE["value"],
                "cached": True,
                "age_s": round(age, 1),
                "ttl_s": TTL_SEC,
                "error": None,
            }
        if _CACHE.get("inflight"):
            # Serve stale if present
            if _CACHE.get("value") is not None:
                return {
                    "ok": True,
                    "available": _CACHE["value"],
                    "cached": True,
                    "age_s": round(age, 1),
                    "ttl_s": TTL_SEC,
                    "error": None,
                    "stale_inflight": True,
                }
        _CACHE["inflight"] = True

    err = None
    val = None
    try:
        val = _fetch_balance()
    except Exception as exc:
        err = str(exc)

    with _LOCK:
        _CACHE["inflight"] = False
        if val is not None:
            _CACHE["value"] = val
            _CACHE["ts"] = time.time()
            _CACHE["error"] = None
            return {
                "ok": True,
                "available": val,
                "cached": False,
                "age_s": 0.0,
                "ttl_s": TTL_SEC,
                "error": None,
            }
        _CACHE["error"] = err
        if _CACHE.get("value") is not None:
            return {
                "ok": True,
                "available": _CACHE["value"],
                "cached": True,
                "age_s": round(time.time() - float(_CACHE.get("ts") or 0), 1),
                "ttl_s": TTL_SEC,
                "error": err,
            }
        return {
            "ok": False,
            "available": None,
            "cached": False,
            "age_s": None,
            "ttl_s": TTL_SEC,
            "error": err or "margin_unavailable",
        }
