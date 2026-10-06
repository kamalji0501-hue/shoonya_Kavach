"""Coverage copy-trading DRY_RUN stubs. No broker calls."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

RUNTIME_DIR = Path(
    os.environ.get(
        "KAVACH_COPY_DIR",
        "/home/ubuntu/Trading_Runtime_Rahul/Data/data/shared/copy_trading",
    )
)
MAX_BODY = 65_536
OVERNIGHT_ROLES = {"OVERNIGHT", "OPEN_TRIAL"}
_LOCK = threading.Lock()


def _paths() -> dict[str, Path]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "key": RUNTIME_DIR / "copy_key.txt",
        "config": RUNTIME_DIR / "copy_config.json",
        "state": RUNTIME_DIR / "copy_state.json",
    }


def _read_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return json.loads(json.dumps(default))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else json.loads(json.dumps(default))
    except Exception:
        return json.loads(json.dumps(default))


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def default_config() -> dict:
    return {
        "role": "standalone",
        "vps_id": "",
        "peer_vps_id": "",
        "peer_base_url": "",
        "allow_copy": False,
        "lots": 1,
        "dry_run": True,
        "receive_paused": False,
    }


def default_state() -> dict:
    return {
        "seq": 0,
        "events": [],
        "open_cycles": {},
        "seen_ids": [],
        "book": {},
        "punch_log": [],
        "last_action": None,
        "last_event": None,
        "last_delivery": None,
        "last_seq_received": 0,
    }


def load_key() -> str:
    path = _paths()["key"]
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_config() -> dict:
    cfg = default_config()
    cfg.update(_read_json(_paths()["config"], {}))
    cfg["dry_run"] = True
    cfg["lots"] = int(cfg.get("lots") or 1)
    cfg["allow_copy"] = bool(cfg.get("allow_copy"))
    cfg["receive_paused"] = bool(cfg.get("receive_paused"))
    return cfg


def load_state() -> dict:
    st = default_state()
    raw = _read_json(_paths()["state"], {})
    st.update(raw)
    st["open_cycles"] = dict(st.get("open_cycles") or {})
    st["book"] = dict(st.get("book") or {})
    st["events"] = list(st.get("events") or [])
    st["seen_ids"] = list(st.get("seen_ids") or [])
    st["punch_log"] = list(st.get("punch_log") or [])
    return st


def save_config(cfg: dict) -> None:
    _write_json(_paths()["config"], cfg)


def save_state(st: dict) -> None:
    st["events"] = list(st.get("events") or [])[-300:]
    st["seen_ids"] = list(st.get("seen_ids") or [])[-500:]
    st["punch_log"] = list(st.get("punch_log") or [])[-300:]
    _write_json(_paths()["state"], st)


def _eq_key(got: str, expect: str) -> bool:
    if not expect or not got:
        return False
    if len(got) != len(expect):
        return False
    return sum(a != b for a, b in zip(got, expect)) == 0 and got == expect


def _auth_fail(request: Request) -> JSONResponse | None:
    expect = load_key()
    got = (request.headers.get("x-copy-key") or "").strip()
    if not _eq_key(got, expect):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return None


def _public_config(cfg: dict) -> dict:
    key = load_key()
    return {
        "role": cfg.get("role"),
        "vps_id": cfg.get("vps_id"),
        "peer_vps_id": cfg.get("peer_vps_id"),
        "peer_base_url": cfg.get("peer_base_url"),
        "allow_copy": bool(cfg.get("allow_copy")),
        "lots": int(cfg.get("lots") or 0),
        "dry_run": True,
        "receive_paused": bool(cfg.get("receive_paused")),
        "key_last4": key[-4:] if key else "",
    }


def _health_body() -> dict:
    cfg = load_config()
    st = load_state()
    role = str(cfg.get("role") or "standalone")
    allow = bool(cfg.get("allow_copy"))
    if role == "master":
        banner = "COPY MASTER"
    elif role == "child":
        banner = f"COPY CHILD — Allow={'ON' if allow else 'OFF'}"
    else:
        banner = "STANDALONE"
    return {
        "ok": True,
        "role": role,
        "banner": banner,
        "vps_id": cfg.get("vps_id"),
        "peer_vps_id": cfg.get("peer_vps_id"),
        "peer_base_url": cfg.get("peer_base_url"),
        "allow_copy": allow,
        "lots": int(cfg.get("lots") or 0),
        "dry_run": True,
        "receive_paused": bool(cfg.get("receive_paused")),
        "seq": int(st.get("seq") or 0),
        "last_seq_received": int(st.get("last_seq_received") or 0),
        "last_action": st.get("last_action"),
        "last_delivery": st.get("last_delivery"),
        "open_cycles": sorted((st.get("open_cycles") or {}).keys()),
        "book_cycles": sorted((st.get("book") or {}).keys()),
        "book": st.get("book") or {},
        "punch_log_tail": (st.get("punch_log") or [])[-8:],
        "punch_count": len(st.get("punch_log") or []),
        "last_event": st.get("last_event"),
    }


def _overnight(body: dict) -> bool:
    for leg in body.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        role = str(leg.get("role") or "").upper()
        if role in OVERNIGHT_ROLES:
            return True
    return False


def _apply_child_event(body: dict, cfg: dict, st: dict) -> dict:
    action = str(body.get("action") or "").upper()
    event_id = str(body.get("event_id") or "")
    cycle_id = str(body.get("cycle_id") or "")
    if action not in {"ENTER", "EXIT", "SIDE_EXIT", "SNAPSHOT"}:
        return {"ok": False, "applied": False, "reason": "unknown_action", "status": 400}
    if not event_id:
        return {"ok": False, "applied": False, "reason": "missing_event_id", "status": 400}
    sender = str(body.get("master_vps_id") or "")
    expect = str(cfg.get("peer_vps_id") or "")
    if expect and sender != expect:
        return {"ok": False, "applied": False, "reason": "vps_mismatch", "status": 403}
    if str(cfg.get("role") or "") != "child":
        return {"ok": False, "applied": False, "reason": "not_child", "status": 403}
    if _overnight(body):
        return {"ok": False, "applied": False, "reason": "overnight_filtered", "status": 400}
    if event_id in set(st.get("seen_ids") or []):
        return {
            "ok": True,
            "applied": False,
            "reason": "duplicate",
            "local_lots": int(cfg.get("lots") or 0),
            "status": 200,
        }
    if not cfg.get("allow_copy"):
        st["seen_ids"].append(event_id)
        st["last_action"] = action
        st["last_event"] = body
        st["last_seq_received"] = int(body.get("seq") or st.get("last_seq_received") or 0)
        return {
            "ok": True,
            "applied": False,
            "reason": "allow_copy_off",
            "status": 200,
        }

    lots = int(cfg.get("lots") or 0)
    book = st["book"]
    if action == "ENTER":
        book[cycle_id or event_id] = {
            "cycle_id": cycle_id or event_id,
            "legs": body.get("legs") or [],
            "manager": body.get("manager") or {},
            "exclude": body.get("exclude") or [],
            "opened_lots": lots,
            "event_id": event_id,
            "order_id": None,
        }
        st["punch_log"].append(
            f"DRY_RUN would punch lots={lots} cycle={cycle_id or event_id}"
        )
    elif action == "EXIT":
        row = book.get(cycle_id)
        if not row:
            st["seen_ids"].append(event_id)
            st["last_action"] = action
            st["last_event"] = body
            return {"ok": True, "applied": False, "reason": "no_open_book", "status": 200}
        opened = int(row.get("opened_lots") or 0)
        st["punch_log"].append(f"DRY_RUN would exit lots={opened} cycle={cycle_id}")
        book.pop(cycle_id, None)
        lots = opened
    elif action == "SIDE_EXIT":
        row = book.get(cycle_id)
        if not row:
            st["seen_ids"].append(event_id)
            return {"ok": True, "applied": False, "reason": "no_open_book", "status": 200}
        sides = {str(l.get("side_book") or "").upper() for l in (body.get("legs") or []) if isinstance(l, dict)}
        kept = []
        for leg in row.get("legs") or []:
            if str(leg.get("side_book") or "").upper() in sides:
                continue
            kept.append(leg)
        opened = int(row.get("opened_lots") or 0)
        st["punch_log"].append(
            f"DRY_RUN would side-exit lots={opened} cycle={cycle_id} sides={','.join(sorted(sides))}"
        )
        if kept:
            row["legs"] = kept
            book[cycle_id] = row
        else:
            book.pop(cycle_id, None)
        lots = opened
    st["seen_ids"].append(event_id)
    st["last_action"] = action
    st["last_event"] = body
    st["last_seq_received"] = max(int(st.get("last_seq_received") or 0), int(body.get("seq") or 0))
    return {"ok": True, "applied": True, "local_lots": lots, "status": 200}


def _record_master(body: dict, st: dict) -> dict:
    action = str(body.get("action") or "").upper()
    cycle_id = str(body.get("cycle_id") or body.get("event_id") or "")
    st["seq"] = int(st.get("seq") or 0) + 1
    body = dict(body)
    body["seq"] = int(body.get("seq") or st["seq"])
    if int(body["seq"]) > st["seq"]:
        st["seq"] = int(body["seq"])
    st["events"].append(body)
    cycles = st["open_cycles"]
    if action == "ENTER":
        cycles[cycle_id] = {
            "cycle_id": cycle_id,
            "legs": body.get("legs") or [],
            "manager": body.get("manager") or {},
            "exclude": body.get("exclude") or [],
            "event_id": body.get("event_id"),
        }
    elif action == "EXIT":
        cycles.pop(cycle_id, None)
    elif action == "SIDE_EXIT":
        row = cycles.get(cycle_id)
        if row:
            sides = {str(l.get("side_book") or "").upper() for l in (body.get("legs") or []) if isinstance(l, dict)}
            row["legs"] = [
                leg for leg in (row.get("legs") or [])
                if str(leg.get("side_book") or "").upper() not in sides
            ]
            if not row["legs"]:
                cycles.pop(cycle_id, None)
            else:
                cycles[cycle_id] = row
    st["last_action"] = action
    st["last_event"] = body
    return body


_PUSH_CONN = None
_PUSH_TARGET = ""


def _push_connection(host: str, port: int):
    global _PUSH_CONN, _PUSH_TARGET
    target = f"{host}:{port}"
    if _PUSH_CONN is not None and _PUSH_TARGET == target:
        return _PUSH_CONN
    if _PUSH_CONN is not None:
        try:
            _PUSH_CONN.close()
        except Exception:
            pass
    import http.client

    conn = http.client.HTTPConnection(host, port, timeout=3)
    _PUSH_CONN = conn
    _PUSH_TARGET = target
    return conn


def _push(cfg: dict, body: dict) -> dict:
    """Signal + confirmation on one kept-alive HTTP connection."""
    import http.client
    from urllib.parse import urlparse

    base = str(cfg.get("peer_base_url") or "").rstrip("/")
    parsed_url = urlparse(base if "://" in base else "http://" + base)
    host = parsed_url.hostname or ""
    port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
    path = (parsed_url.path or "") + "/api/copy/event"
    key = load_key()
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(data)),
        "X-Copy-Key": key,
        "X-Copy-Vps-Id": str(cfg.get("vps_id") or ""),
        "X-Copy-Timestamp": str(int(time.time())),
        "Connection": "keep-alive",
    }
    t0 = time.perf_counter()
    try:
        conn = _push_connection(host, port)
        conn.request("POST", path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw[:300]}
        ok = 200 <= resp.status < 300
        return {"ok": ok, "status": resp.status, "ms": elapsed, "body": parsed}
    except Exception:
        global _PUSH_CONN, _PUSH_TARGET
        try:
            if _PUSH_CONN is not None:
                _PUSH_CONN.close()
        except Exception:
            pass
        _PUSH_CONN = None
        _PUSH_TARGET = ""
        try:
            conn = _push_connection(host, port)
            conn.request("POST", path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"raw": raw[:300]}
            ok = 200 <= resp.status < 300
            return {"ok": ok, "status": resp.status, "ms": elapsed, "body": parsed, "reconnected": True}
        except Exception as exc:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            return {"ok": False, "status": 0, "ms": elapsed, "error": str(exc)}


def default_enter(event_id: str, cycle_id: str, seq: int, master_vps_id: str) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "seq": seq,
        "master_vps_id": master_vps_id,
        "action": "ENTER",
        "ts_ist": "2026-01-15T10:30:00+05:30",
        "cycle_id": cycle_id,
        "legs": [
            {
                "role": "CORE_SELL",
                "side_book": "CE",
                "symbol": "NIFTY15JAN2624500CE",
                "strike": 24500,
                "option_type": "CE",
                "expiry": "2026-01-15",
                "transaction": "SELL",
                "master_qty": 130,
                "master_lots": 2,
            },
            {
                "role": "MARGIN_HEDGE",
                "side_book": "CE",
                "symbol": "NIFTY15JAN2624600CE",
                "strike": 24600,
                "option_type": "CE",
                "expiry": "2026-01-15",
                "transaction": "BUY",
                "master_qty": 65,
                "master_lots": 1,
            },
        ],
        "manager": {
            "ato_ce_active": True,
            "ato_pe_active": False,
            "nifty_ref": 24480.5,
            "levels": {
                "ce_trigger": 24520,
                "ce_exit": 24490,
                "pe_trigger": None,
                "pe_exit": None,
                "retrace_points": 5,
            },
        },
        "exclude": ["OVERNIGHT", "OPEN_TRIAL"],
    }


def default_exit(event_id: str, cycle_id: str, seq: int, master_vps_id: str) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "seq": seq,
        "master_vps_id": master_vps_id,
        "action": "EXIT",
        "ts_ist": "2026-01-15T11:05:00+05:30",
        "cycle_id": cycle_id,
        "legs": [
            {"role": "CORE_SELL", "side_book": "CE", "strike": 24500, "option_type": "CE", "expiry": "2026-01-15", "transaction": "BUY"},
            {"role": "MARGIN_HEDGE", "side_book": "CE", "strike": 24600, "option_type": "CE", "expiry": "2026-01-15", "transaction": "SELL"},
        ],
        "manager": {"ato_ce_active": False, "ato_pe_active": False, "nifty_ref": 24470.0, "levels": {}},
        "exclude": ["OVERNIGHT", "OPEN_TRIAL"],
    }


def _pull_desired(cfg: dict) -> dict:
    url = str(cfg.get("peer_base_url") or "").rstrip("/") + "/api/copy/desired?since_seq=0"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "X-Copy-Key": load_key(),
            "X-Copy-Vps-Id": str(cfg.get("vps_id") or ""),
            "X-Copy-Timestamp": str(int(time.time())),
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _repair_from_desired(cfg: dict, st: dict, desired: dict) -> list[str]:
    notes: list[str] = []
    remote = desired.get("open_cycles") or {}
    book = st["book"]
    if cfg.get("allow_copy"):
        for cycle_id, row in remote.items():
            if cycle_id in book:
                continue
            event_id = f"repair-enter-{cycle_id}-{desired.get('seq')}"
            if event_id in set(st.get("seen_ids") or []):
                continue
            lots = int(cfg.get("lots") or 0)
            book[cycle_id] = {
                "cycle_id": cycle_id,
                "legs": row.get("legs") or [],
                "manager": row.get("manager") or {},
                "exclude": row.get("exclude") or [],
                "opened_lots": lots,
                "event_id": event_id,
                "order_id": None,
            }
            st["seen_ids"].append(event_id)
            line = f"DRY_RUN would punch lots={lots} cycle={cycle_id}"
            st["punch_log"].append(line)
            notes.append(line)
    for cycle_id in list(book.keys()):
        if cycle_id in remote:
            continue
        event_id = f"repair-exit-{cycle_id}-{desired.get('seq')}"
        if event_id in set(st.get("seen_ids") or []):
            continue
        opened = int((book.get(cycle_id) or {}).get("opened_lots") or 0)
        book.pop(cycle_id, None)
        st["seen_ids"].append(event_id)
        line = f"DRY_RUN would exit lots={opened} cycle={cycle_id}"
        st["punch_log"].append(line)
        notes.append(line)
    st["last_action"] = "REPAIR"
    return notes


async def api_copy_health(request: Request) -> JSONResponse:
    bad = _auth_fail(request)
    if bad:
        return bad
    return JSONResponse(_health_body())


async def api_copy_config(request: Request) -> JSONResponse:
    bad = _auth_fail(request)
    if bad:
        return bad
    if request.method == "GET":
        return JSONResponse({"ok": True, "config": _public_config(load_config())})
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return JSONResponse({"ok": False, "error": "body_too_large"}, status_code=413)
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    cfg = load_config()
    if "lots" in body:
        try:
            lots = int(body["lots"])
        except Exception:
            return JSONResponse({"ok": False, "error": "lots_invalid"}, status_code=400)
        if lots < 1:
            return JSONResponse({"ok": False, "error": "lots_must_be_at_least_1"}, status_code=400)
        cfg["lots"] = lots
    if "allow_copy" in body:
        cfg["allow_copy"] = bool(body["allow_copy"])
    if "receive_paused" in body:
        cfg["receive_paused"] = bool(body["receive_paused"])
    if "role" in body:
        role = str(body["role"] or "").lower()
        if role not in {"master", "child", "standalone"}:
            return JSONResponse({"ok": False, "error": "bad_role"}, status_code=400)
        cfg["role"] = role
    if "peer_base_url" in body:
        cfg["peer_base_url"] = str(body["peer_base_url"] or "")
    if "vps_id" in body:
        cfg["vps_id"] = str(body["vps_id"] or "")
    if "peer_vps_id" in body:
        cfg["peer_vps_id"] = str(body["peer_vps_id"] or "")
    cfg["dry_run"] = True
    with _LOCK:
        save_config(cfg)
    return JSONResponse({"ok": True, "config": _public_config(cfg)})


async def api_copy_event(request: Request) -> JSONResponse:
    bad = _auth_fail(request)
    if bad:
        return bad
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return JSONResponse({"ok": False, "applied": False, "error": "body_too_large"}, status_code=413)
    if not raw.strip():
        return JSONResponse({"ok": False, "applied": False, "error": "empty_body"}, status_code=400)
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse({"ok": False, "applied": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict) or not body:
        return JSONResponse({"ok": False, "applied": False, "error": "empty_body"}, status_code=400)
    if "action" not in body:
        return JSONResponse({"ok": False, "applied": False, "error": "missing_action"}, status_code=400)
    with _LOCK:
        cfg = load_config()
        if cfg.get("receive_paused"):
            return JSONResponse({"ok": False, "applied": False, "reason": "paused"}, status_code=503)
        st = load_state()
        out = _apply_child_event(body, cfg, st)
        status = int(out.pop("status", 200))
        if status == 200 or out.get("reason") in {"allow_copy_off", "duplicate", "no_open_book", "overnight_filtered"}:
            save_state(st)
        elif out.get("reason") == "overnight_filtered":
            save_state(st)
        else:
            # Persist only when state mutated inside helper. Re-save if seen appended.
            save_state(st)
        return JSONResponse(out, status_code=status)


async def api_copy_desired(request: Request) -> JSONResponse:
    bad = _auth_fail(request)
    if bad:
        return bad
    since = 0
    try:
        since = int(request.query_params.get("since_seq") or 0)
    except Exception:
        since = 0
    st = load_state()
    events = [ev for ev in (st.get("events") or []) if int(ev.get("seq") or 0) > since]
    return JSONResponse(
        {
            "ok": True,
            "seq": int(st.get("seq") or 0),
            "open_cycles": st.get("open_cycles") or {},
            "events": events,
        }
    )


async def api_copy_events(request: Request) -> JSONResponse:
    bad = _auth_fail(request)
    if bad:
        return bad
    since = 0
    try:
        since = int(request.query_params.get("since_seq") or 0)
    except Exception:
        since = 0
    st = load_state()
    events = [ev for ev in (st.get("events") or []) if int(ev.get("seq") or 0) > since]
    return JSONResponse({"ok": True, "seq": int(st.get("seq") or 0), "events": events})


async def api_copy_test_emit(request: Request) -> JSONResponse:
    bad = _auth_fail(request)
    if bad:
        return bad
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    action = str(body.get("action") or "ENTER").upper()
    with _LOCK:
        cfg = load_config()
        if cfg.get("role") != "master":
            return JSONResponse({"ok": False, "error": "not_master"}, status_code=403)
        st = load_state()
        seq = int(st.get("seq") or 0) + 1
        event_id = str(body.get("event_id") or f"mock-{action.lower()}-{uuid.uuid4().hex[:8]}")
        cycle_id = str(body.get("cycle_id") or "cycle-mock-A")
        master_id = str(cfg.get("vps_id") or "master-rahul")
        if isinstance(body.get("event"), dict):
            event = dict(body["event"])
            event.setdefault("event_id", event_id)
            event.setdefault("cycle_id", cycle_id)
            event.setdefault("master_vps_id", master_id)
            event["action"] = action
        elif action == "EXIT":
            event = default_exit(event_id, cycle_id, seq, master_id)
        elif action == "SIDE_EXIT":
            event = default_exit(event_id, cycle_id, seq, master_id)
            event["action"] = "SIDE_EXIT"
        else:
            event = default_enter(event_id, cycle_id, seq, master_id)
        if body.get("seq") is not None:
            event["seq"] = int(body["seq"])
        event = _record_master(event, st)
        save_state(st)
        cfg_push = cfg
    delivery = _push(cfg_push, event)
    with _LOCK:
        st = load_state()
        st["last_delivery"] = {
            "ok": bool(delivery.get("ok")),
            "status": delivery.get("status"),
            "ms": delivery.get("ms"),
            "event_id": event.get("event_id"),
            "action": event.get("action"),
        }
        save_state(st)
    return JSONResponse({"ok": True, "event": event, "delivery": delivery, "seq": event.get("seq")})


async def api_copy_sync(request: Request) -> JSONResponse:
    bad = _auth_fail(request)
    if bad:
        return bad
    with _LOCK:
        cfg = load_config()
        if cfg.get("role") != "child":
            return JSONResponse({"ok": False, "error": "not_child"}, status_code=403)
    t0 = time.perf_counter()
    try:
        desired = _pull_desired(cfg)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    with _LOCK:
        st = load_state()
        notes = _repair_from_desired(cfg, st, desired)
        save_state(st)
    ms = round((time.perf_counter() - t0) * 1000, 2)
    return JSONResponse({"ok": True, "notes": notes, "ms": ms, "desired_seq": desired.get("seq"), "book_cycles": sorted(load_state()["book"].keys())})


async def api_copy_reset(request: Request) -> JSONResponse:
    bad = _auth_fail(request)
    if bad:
        return bad
    with _LOCK:
        save_state(default_state())
    return JSONResponse({"ok": True, "reset": True})


async def copy_page(request: Request) -> HTMLResponse:
    html = """<!doctype html>
<html><head><meta charset="utf-8"><title>Coverage copy</title>
<style>body{font-family:sans-serif;margin:24px;max-width:720px}pre{background:#f4f4f4;padding:12px}</style>
</head><body>
<h1>Coverage copy</h1>
<p>DRY_RUN status. Paste the link key to view this desk. No orders are sent.</p>
<input id="k" type="password" placeholder="link key" style="width:280px">
<button id="b">Load</button>
<pre id="o">Waiting.</pre>
<script>
document.getElementById('b').onclick = async () => {
  const r = await fetch('/api/copy/health', {headers:{'X-Copy-Key': document.getElementById('k').value}});
  document.getElementById('o').textContent = JSON.stringify(await r.json(), null, 2);
};
</script>
</body></html>"""
    return HTMLResponse(html)


def copy_routes() -> list[Route]:
    return [
        Route("/copy", copy_page),
        Route("/api/copy/health", api_copy_health),
        Route("/api/copy/config", api_copy_config, methods=["GET", "POST"]),
        Route("/api/copy/event", api_copy_event, methods=["POST"]),
        Route("/api/copy/desired", api_copy_desired),
        Route("/api/copy/events", api_copy_events),
        Route("/api/copy/test-emit", api_copy_test_emit, methods=["POST"]),
        Route("/api/copy/sync", api_copy_sync, methods=["POST"]),
        Route("/api/copy/reset", api_copy_reset, methods=["POST"]),
    ]
