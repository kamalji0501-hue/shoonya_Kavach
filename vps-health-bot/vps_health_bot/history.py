"""Dedicated history + event logs under VPSHealth_Runtime. No secrets."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from vps_health_bot import runtime_paths as rp

logger = logging.getLogger("vps_health_bot.history")


def samples_path() -> Path:
    return rp.health_data_dir() / "samples.jsonl"


def events_path() -> Path:
    return rp.log_day_dir() / "logs" / "events.jsonl"


def daily_path(when: datetime | None = None) -> Path:
    day = rp.log_day_dir(when=when)
    return day / "history.json"


def record_event(kind: str, **fields: Any) -> None:
    row = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
    row.update(fields)
    path = events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _bot_rss(snap: Any, name: str) -> float:
    for bot in getattr(snap, "bots", []) or []:
        if getattr(bot, "active", "") != "active":
            continue
        if name.lower() in str(getattr(bot, "name", "")).lower():
            return float(getattr(bot, "rss_mb", 0) or 0)
    return 0.0


def record_sample(snap: Any) -> dict[str, Any]:
    row = {
        "ts": snap.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "load1": round(float(snap.load1), 3),
        "mem_pct": round(float(snap.mem_used_pct), 2),
        "steal": round(
            float(
                getattr(snap, "cpu_steal_instant_pct", None)
                or getattr(snap, "cpu_steal_pct", 0.0)
                or 0.0
            ),
            2,
        ),
        "iowait": round(
            float(
                getattr(snap, "cpu_iowait_instant_pct", None)
                or getattr(snap, "cpu_iowait_pct", 0.0)
                or 0.0
            ),
            2,
        ),
        "swap_mb": round(float(snap.swap_used_mb), 1),
        "max_rss_mb": round(
            max((b.rss_mb for b in snap.bots if b.active == "active"), default=0.0), 1
        ),
        "blitz_rss_mb": round(_bot_rss(snap, "Blitz"), 1),
        "datafeed_rss_mb": round(_bot_rss(snap, "Datafeed"), 1),
        "dhan_429": int((snap.hft or {}).get("dhan_429") or 0),
        "dhan_ws_err": int((snap.hft or {}).get("dhan_ws_err") or 0),
        "concerns": list(getattr(snap, "concerns", None) or []),
    }
    path = samples_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    _trim_samples(path, keep=2000)
    _write_daily(snap, row)
    return row


def _trim_samples(path: Path, keep: int = 2000) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= keep:
            return
        path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("trim samples failed: %s", exc)


def _write_daily(snap: Any, row: dict[str, Any]) -> None:
    path = daily_path(snap.ts)
    data: dict[str, Any] = {
        "day": snap.ts.strftime("%Y-%m-%d"),
        "hostname": getattr(snap, "hostname", ""),
        "samples": 0,
        "last": row,
        "peaks": {},
        "dhan_429_max": 0,
        "concern_counts": {},
    }
    if path.is_file():
        try:
            data.update(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    data["samples"] = int(data.get("samples") or 0) + 1
    data["last"] = row
    data["dhan_429_max"] = max(int(data.get("dhan_429_max") or 0), int(row.get("dhan_429") or 0))
    counts = data.get("concern_counts") or {}
    for item in row.get("concerns") or []:
        key = _bucket_concern(str(item))
        counts[key] = int(counts.get(key) or 0) + 1
    data["concern_counts"] = counts
    data["peaks"] = peaks_today(snap.ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_samples(day: str | None = None, last_n: int = 800) -> list[dict[str, Any]]:
    path = samples_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-last_n:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if day and not str(row.get("ts", "")).startswith(day):
            continue
        rows.append(row)
    return rows


def peaks_today(now: datetime | None = None) -> dict[str, Any]:
    day = now.strftime("%Y-%m-%d") if now else None
    rows = load_samples(day=day)
    if not rows:
        return {}

    def peak(key: str) -> dict[str, Any]:
        best = max(rows, key=lambda r: float(r.get(key) or 0.0))
        return {"value": best.get(key), "at": best.get("ts")}

    return {
        "samples": len(rows),
        "load1": peak("load1"),
        "mem_pct": peak("mem_pct"),
        "steal": peak("steal"),
        "max_rss_mb": peak("max_rss_mb"),
        "blitz_rss_mb": peak("blitz_rss_mb") if any("blitz_rss_mb" in r for r in rows) else {},
        "dhan_429": peak("dhan_429"),
    }


def rss_trend(name_key: str = "blitz_rss_mb", window: int = 6) -> dict[str, Any]:
    rows = [r for r in load_samples() if r.get(name_key) is not None]
    if len(rows) < 2:
        return {}
    recent = rows[-window:]
    first = float(recent[0].get(name_key) or 0)
    last = float(recent[-1].get(name_key) or 0)
    return {
        "from": first,
        "to": last,
        "delta": round(last - first, 1),
        "from_ts": recent[0].get("ts"),
        "to_ts": recent[-1].get("ts"),
        "n": len(recent),
    }


def _bucket_concern(text: str) -> str:
    low = text.lower()
    if "429" in low:
        return "dhan_429_storm"
    if "reconnect" in low or "ws error" in low:
        return "dhan_ws_noise"
    if "not active" in low:
        return "unit_down"
    if "memory" in low or "ram " in low:
        return "ram"
    if "steal" in low:
        return "steal"
    if text.startswith("Dhan"):
        return "dhan"
    return text[:80]


def concern_signature(concerns: list[str]) -> str:
    if not concerns:
        return ""
    return "|".join(sorted({_bucket_concern(c) for c in concerns}))
