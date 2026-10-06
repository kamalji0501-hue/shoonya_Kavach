"""Background poller + in-memory fleet snapshot cache."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from web.fleet.registry import iter_desks, load_registry, resolve_copy_key, registry_path

logger = logging.getLogger("batman.kavach.fleet")

_LOCK = threading.Lock()
_SNAP: dict[str, Any] = {"ok": True, "desks": [], "totals": {}, "nifty": None, "updated_at": 0.0}
_STARTED = False
_THREAD: threading.Thread | None = None
_CTRL_LAST: dict[str, float] = {}
_AUDIT_PATH = None


def _audit_path():
    global _AUDIT_PATH
    if _AUDIT_PATH is None:
        p = registry_path().parent / "audit.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        _AUDIT_PATH = p
    return _AUDIT_PATH


def audit(event: str, detail: dict[str, Any]) -> None:
    try:
        row = {"ts": time.time(), "event": event, **detail}
        with open(_audit_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception:
        logger.exception("fleet audit write failed")


def get_snapshot() -> dict[str, Any]:
    with _LOCK:
        return json.loads(json.dumps(_SNAP, default=str))


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    timeout: float = 4.0,
) -> tuple[int, dict[str, Any]]:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = {"raw": raw[:300]}
            if not isinstance(parsed, dict):
                parsed = {"data": parsed}
            return int(resp.status), parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"error": raw[:300]}
        if not isinstance(parsed, dict):
            parsed = {"error": str(parsed)}
        return int(exc.code), parsed
    except Exception as exc:
        return 0, {"ok": False, "error": str(exc)}


def _local_desk(entry: dict[str, Any], want_margin: bool) -> dict[str, Any]:
    from web.fleet.desk_export import build_desk_snapshot

    # Always read margin cache; only force a broker refresh on margin cadence.
    snap = build_desk_snapshot(include_margin=True, force_margin=want_margin)
    return _normalize_desk(entry, snap, link_status="UP", reachable=True, source="local")


def _remote_desk(entry: dict[str, Any], want_margin: bool) -> dict[str, Any]:
    base = str(entry.get("base_url") or "").rstrip("/")
    key = resolve_copy_key(entry)
    headers = {"X-Copy-Key": key, "X-Copy-Vps-Id": "fleet-master"}
    # margin=1 serves cached margin; force=1 hits broker (rate-limited by desk TTL)
    url = f"{base}/api/fleet/desk-snapshot?margin=1"
    if want_margin:
        url += "&force=1"
    code, body = _http_json("GET", url, headers=headers, timeout=4.0)
    if code == 200 and body.get("ok"):
        return _normalize_desk(entry, body, link_status="UP", reachable=True, source="desk-snapshot")

    # Fallback: copy health only (child may not have fleet endpoints yet)
    code2, health = _http_json("GET", f"{base}/api/copy/health", headers=headers, timeout=3.0)
    if code2 == 200 and health.get("ok"):
        slim = {
            "ok": True,
            "status": "UNKNOWN",
            "paused": None,
            "day_pnl": None,
            "batman_pnl": None,
            "nifty_ltp": None,
            "host": {},
            "copy": {
                "role": health.get("role"),
                "allow_copy": health.get("allow_copy"),
                "channel_on": health.get("channel_on"),
                "lots": health.get("lots"),
                "link_ok": True,
            },
            "subscription": {},
            "margin": {"ok": False, "available": None, "skipped": True},
            "partial": True,
            "note": "desk-snapshot missing; copy health only",
            "error": body.get("error") if code != 200 else None,
        }
        return _normalize_desk(entry, slim, link_status="UP", reachable=True, source="copy-health")

    err = body.get("error") or health.get("error") or "unreachable"
    return _normalize_desk(
        entry,
        {"ok": False, "status": "UNREACHABLE", "host": {}, "copy": {}, "subscription": {}, "margin": {}},
        link_status="DOWN",
        reachable=False,
        source="error",
        error=str(err),
    )


def _normalize_desk(
    entry: dict[str, Any],
    snap: dict[str, Any],
    *,
    link_status: str,
    reachable: bool,
    source: str,
    error: str | None = None,
) -> dict[str, Any]:
    host = snap.get("host") or {}
    copy = snap.get("copy") or {}
    sub = snap.get("subscription") or {}
    margin = snap.get("margin") or {}
    status = str(snap.get("status") or ("UNREACHABLE" if not reachable else "UNKNOWN"))
    allow = copy.get("allow_copy")
    channel = copy.get("channel_on")
    copy_on = bool(allow) and (channel is not False)
    lots_sub = sub.get("max_sell_lots_batman")
    lots_copy = copy.get("lots")
    return {
        "id": entry.get("id"),
        "label": entry.get("label") or entry.get("id"),
        "role_tag": entry.get("role_tag") or "CHILD",
        "aws_account": entry.get("aws_account") or "",
        "link_type": entry.get("link_type") or "unknown",
        "base_url": entry.get("base_url") or "",
        "reachable": reachable,
        "link_status": link_status,
        "status": status,
        "paused": snap.get("paused"),
        "order_mode": snap.get("order_mode"),
        "day_pnl": snap.get("day_pnl"),
        "batman_pnl": snap.get("batman_pnl"),
        "nifty_ltp": snap.get("nifty_ltp"),
        "cpu_pct": host.get("cpu_pct"),
        "ram_pct": host.get("ram_pct"),
        "disk_pct": host.get("disk_pct"),
        "units": host.get("units") or {},
        "feeder_ok": host.get("feeder_ok"),
        "feeder_cpu_pct": host.get("feeder_cpu_pct"),
        "feeder_unit": host.get("feeder_unit"),
        "copy_on": copy_on,
        "allow_copy": allow,
        "channel_on": channel,
        "lots_allowed": lots_copy,
        "lots_subscription_sell": lots_sub,
        "lots_subscription_ato": sub.get("max_ato_lots_batman"),
        "subscription_status": sub.get("ui_status"),
        "margin_available": margin.get("available"),
        "margin_ok": bool(margin.get("ok")),
        "partial": bool(snap.get("partial")),
        "source": source,
        "error": error or snap.get("error"),
        "updated_at": snap.get("ts") or time.time(),
    }


def _want_margin(last_margin_poll: float, margin_sec: float) -> bool:
    return (time.time() - last_margin_poll) >= margin_sec


def refresh_once(force_margin: bool = False) -> dict[str, Any]:
    reg = load_registry()
    poll = reg.get("poll") or {}
    margin_sec = float(poll.get("margin_sec") or 300)
    with _LOCK:
        last_m = float(_SNAP.get("last_margin_poll") or 0)
    want_m = force_margin or _want_margin(last_m, margin_sec)

    desks: list[dict[str, Any]] = []
    nifty = None
    for entry in iter_desks(reg):
        try:
            if entry.get("local"):
                row = _local_desk(entry, want_m)
            else:
                row = _remote_desk(entry, want_m)
        except Exception as exc:
            logger.exception("fleet desk poll failed id=%s", entry.get("id"))
            row = _normalize_desk(
                entry,
                {"ok": False, "status": "UNREACHABLE", "host": {}, "copy": {}, "subscription": {}, "margin": {}},
                link_status="DOWN",
                reachable=False,
                source="exception",
                error=str(exc),
            )
        desks.append(row)
        if entry.get("local") and row.get("nifty_ltp"):
            nifty = row.get("nifty_ltp")

    total_pnl = 0.0
    pnl_n = 0
    total_margin = 0.0
    margin_n = 0
    links_up = 0
    for d in desks:
        if d.get("day_pnl") is not None:
            try:
                total_pnl += float(d["day_pnl"])
                pnl_n += 1
            except Exception:
                pass
        if d.get("margin_available") is not None:
            try:
                total_margin += float(d["margin_available"])
                margin_n += 1
            except Exception:
                pass
        if d.get("link_status") == "UP":
            links_up += 1

    children = [d for d in desks if d.get("role_tag") == "CHILD"]
    out = {
        "ok": True,
        "desks": desks,
        "totals": {
            "desk_count": len(desks),
            "child_count": len(children),
            "links_up": links_up,
            "links_down": len(desks) - links_up,
            "pnl_sum": round(total_pnl, 2) if pnl_n else None,
            "pnl_n": pnl_n,
            "margin_sum": round(total_margin, 2) if margin_n else None,
            "margin_n": margin_n,
        },
        "nifty": nifty,
        "updated_at": time.time(),
        "last_margin_poll": time.time() if want_m else last_m,
        "poll": {"state_sec": poll.get("state_sec", 30), "margin_sec": margin_sec},
    }
    with _LOCK:
        _SNAP.clear()
        _SNAP.update(out)
    return out


def control(desk_id: str, action: str) -> dict[str, Any]:
    """Proxy pause/resume/copy to local or remote desk. Rate-limited."""
    act = (action or "").strip().lower()
    if act not in {"pause", "resume", "copy_on", "copy_off", "channel_on", "channel_off"}:
        return {"ok": False, "error": "bad_action"}
    key = f"{desk_id}:{act}"
    now = time.time()
    last = float(_CTRL_LAST.get(key) or 0)
    if now - last < 2.0:
        return {"ok": False, "error": "rate_limited"}
    _CTRL_LAST[key] = now

    reg = load_registry()
    entry = None
    for d in iter_desks(reg):
        if str(d.get("id")) == str(desk_id):
            entry = d
            break
    if not entry:
        return {"ok": False, "error": "unknown_desk"}

    if entry.get("local"):
        from web.fleet.desk_export import apply_remote_action

        result = apply_remote_action(act)
        audit("control", {"desk_id": desk_id, "action": act, "local": True, "result": result})
        try:
            refresh_once(force_margin=False)
        except Exception:
            pass
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        return {"ok": ok, "desk_id": desk_id, "action": act, "result": result}

    base = str(entry.get("base_url") or "").rstrip("/")
    headers = {
        "X-Copy-Key": resolve_copy_key(entry),
        "X-Copy-Vps-Id": "fleet-master",
        "Content-Type": "application/json",
    }
    code, body = _http_json(
        "POST",
        f"{base}/api/fleet/remote",
        headers=headers,
        body={"action": act},
        timeout=5.0,
    )
    audit("control", {"desk_id": desk_id, "action": act, "local": False, "http": code, "result": body})
    try:
        refresh_once(force_margin=False)
    except Exception:
        pass
    return {
        "ok": code == 200 and bool(body.get("ok", True)),
        "desk_id": desk_id,
        "action": act,
        "http": code,
        "result": body,
    }


def _loop() -> None:
    while True:
        try:
            reg = load_registry()
            state_sec = max(10, int((reg.get("poll") or {}).get("state_sec") or 30))
            refresh_once(force_margin=False)
            time.sleep(state_sec)
        except Exception:
            logger.exception("fleet aggregator loop error")
            time.sleep(15)


def start_aggregator() -> None:
    global _STARTED, _THREAD
    if _STARTED:
        return
    # Master-only: need registry on disk, or explicit env. Children just expose desk APIs.
    force = (os.environ.get("FLEET_ENABLE_AGGREGATOR") or "").strip() == "1"
    if not force and not registry_path().is_file():
        logger.info("fleet aggregator skipped (no %s)", registry_path())
        return
    _STARTED = True
    try:
        registry_path().parent.mkdir(parents=True, exist_ok=True)
        if not registry_path().is_file() and force:
            from web.fleet.registry import default_registry

            registry_path().write_text(json.dumps(default_registry(), indent=2) + "\n", encoding="utf-8")
    except Exception:
        logger.exception("fleet registry bootstrap failed")
    try:
        refresh_once(force_margin=True)
    except Exception:
        logger.exception("fleet initial refresh failed")
    _THREAD = threading.Thread(target=_loop, name="kavach-fleet-agg", daemon=True)
    _THREAD.start()
    logger.info("fleet aggregator started")
