#!/usr/bin/env python3
"""Live Dhan WS multi-cycle tests. NO ORDERS. Datafeed IPC only."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/ubuntu/Datafeedbot")
from datafeedbot.config import Settings
from datafeedbot.instruments import resolve_dhan
from datafeedbot.ipc import IpcClient

IST = ZoneInfo("Asia/Kolkata")
HOLD_S = 8.0
IDLE_WAIT_S = 3.5


def _ts() -> str:
    return datetime.now(IST).strftime("%H:%M:%S")


def _book_open() -> str | None:
    p = Path("/home/ubuntu/BlitzBot_Runtime/Data/data/prod/go/books/active.json")
    if not p.exists():
        return None
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
        book = json.loads(Path(str(meta.get("path") or "")).read_text(encoding="utf-8"))
    except Exception:
        return None
    if str(book.get("status") or "").lower() == "open":
        return str(book.get("id") or "open")
    return None


def _journal_counts(since: str) -> dict[str, int]:
    def _n(pat: str) -> int:
        r = subprocess.run(
            ["journalctl", "-u", "datafeedbot", "--since", since, "--no-pager"],
            capture_output=True,
            text=True,
        )
        return sum(1 for line in (r.stdout or "").splitlines() if pat in line)

    return {
        "connected": _n("Dhan WS connected"),
        "resub": _n("Dhan WS resubscribed same-socket"),
        "http_429": _n("HTTP 429"),
        "idle_close": _n("Dhan WS idle — no subscribe"),
        "idle_grace": _n("Dhan WS idle grace"),
        "connect_failed": _n("Dhan WS connect failed"),
        "ws_error": _n("Dhan WS error"),
    }


def _health(client: IpcClient) -> dict:
    st = client.send_control("get_state")
    h = ((st.get("data") or {}).get("health") or {}).get("DHAN_WS") or {}
    return {
        "connected": h.get("connected"),
        "status": h.get("status"),
        "packets": h.get("packet_count"),
        "err": (h.get("last_error") or "")[:160],
    }


def _collect(client: IpcClient, seconds: float) -> dict:
    ticks: list[dict] = []
    sources: dict[str, int] = {}
    deadline = time.time() + seconds
    last = {}
    while time.time() < deadline:
        msg = client.recv(timeout=0.8)
        if not msg or msg.get("type") != "tick":
            continue
        d = msg.get("data") or {}
        src = str(d.get("source") or "?")
        sources[src] = sources.get(src, 0) + 1
        ticks.append(d)
        last = d
    return {
        "n": len(ticks),
        "sources": sources,
        "ltp": last.get("ltp"),
        "bid": last.get("bid"),
        "ask": last.get("ask"),
        "dhan_ticks": int(sources.get("DHAN_WS") or 0),
    }


def _resolve(settings: Settings, strike: int) -> tuple[int, str | None, str | None]:
    resolved = resolve_dhan(settings.instruments_csv, strike=strike, option_type="CE")
    sid = resolved.security_id if resolved else None
    exp = resolved.expiry_label if resolved else None
    return strike, sid, exp


def main() -> int:
    print("TS", datetime.now(IST).isoformat(timespec="seconds"))
    open_book = _book_open()
    if open_book:
        print("ABORT open book", open_book, "— no WS steal")
        return 2

    settings = Settings.load()
    a_strike, a_sid, a_exp = _resolve(settings, 24300)
    b_strike, b_sid, b_exp = _resolve(settings, 24400)
    print("A", a_strike, a_sid, a_exp)
    print("B", b_strike, b_sid, b_exp)
    if not a_sid:
        print("ABORT no dhan sid for 24300 CE")
        return 2

    since = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    client = IpcClient(settings.ipc_socket)
    client.connect()
    results: list[dict] = []
    failed = 0

    def record(name: str, ok: bool, **extra: object) -> None:
        nonlocal failed
        row = {"test": name, "ok": ok, "t": _ts(), **extra}
        results.append(row)
        if not ok:
            failed += 1
        print(("PASS" if ok else "FAIL"), name, json.dumps(extra, default=str)[:400])

    try:
        pong = client.send_control("ping")
        record("T00_ping", pong.get("type") == "pong", ping=pong.get("type"))

        # --- 5 sub / hold / unsub cycles ---
        for i in range(1, 6):
            sub = client.send_control(
                "subscribe",
                underlying="NIFTY",
                strike=a_strike,
                option_type="CE",
                dhan_security_id=a_sid,
                expiry=a_exp or "",
            )
            got = _collect(client, HOLD_S)
            h = _health(client)
            ok = (
                sub.get("type") == "subscribed"
                and h.get("status") in {"HEALTHY", "HEALTHY_NO_TICK"}
                and h.get("connected") is True
                and "429" not in str(h.get("err") or "")
                and "no close frame" not in str(h.get("err") or "")
            )
            record(
                f"T1{i}_sub_hold_{a_strike}CE",
                ok,
                sub=sub.get("type"),
                health=h,
                ticks=got,
            )
            client.send_control("unsubscribe")
            time.sleep(IDLE_WAIT_S)
            h2 = _health(client)
            idle_ok = h2.get("status") == "IDLE" and h2.get("connected") is False
            record(f"T1{i}b_idle_after_unsub", idle_ok, health=h2)

        # --- SID switches on same socket (no unsub between) ---
        client.send_control(
            "subscribe",
            underlying="NIFTY",
            strike=a_strike,
            option_type="CE",
            dhan_security_id=a_sid,
            expiry=a_exp or "",
        )
        time.sleep(2.0)
        h_before = _health(client)
        switches = [
            (b_strike, b_sid, b_exp),
            (a_strike, a_sid, a_exp),
            (b_strike, b_sid, b_exp),
        ]
        for j, (st, sid, exp) in enumerate(switches, start=1):
            if not sid:
                record(f"T2{j}_switch", False, err="missing sid")
                continue
            sub = client.send_control(
                "subscribe",
                underlying="NIFTY",
                strike=st,
                option_type="CE",
                dhan_security_id=sid,
                expiry=exp or "",
            )
            got = _collect(client, HOLD_S)
            h = _health(client)
            ok = (
                sub.get("type") == "subscribed"
                and h.get("connected") is True
                and "429" not in str(h.get("err") or "")
                and "no close frame" not in str(h.get("err") or "")
                and h.get("status") in {"HEALTHY", "HEALTHY_NO_TICK"}
            )
            record(
                f"T2{j}_switch_{st}CE",
                ok,
                sub=sub.get("type"),
                health=h,
                ticks=got,
                before=h_before,
            )
            h_before = h

        client.send_control("unsubscribe")
        time.sleep(IDLE_WAIT_S)
        h_end = _health(client)
        record("T30_final_idle", h_end.get("status") == "IDLE", health=h_end)

        counts = _journal_counts(since)
        record(
            "T40_no_429",
            counts.get("http_429", 0) == 0 and counts.get("connect_failed", 0) == 0,
            journal=counts,
        )
        # After first connect, SID switches should resub not open new sockets.
        # 5 sub/unsub cycles + 1 lead-in for switches ≈ 6 connects max; resub >= 2.
        record(
            "T41_reuse_or_idle_ok",
            counts.get("connected", 0) <= 8 and counts.get("http_429", 0) == 0,
            journal=counts,
        )
    finally:
        try:
            client.send_control("unsubscribe")
        except Exception:
            pass
        client.close()

    out = {
        "ts": datetime.now(IST).isoformat(timespec="seconds"),
        "failed": failed,
        "passed": sum(1 for r in results if r.get("ok")),
        "total": len(results),
        "results": results,
        "journal": _journal_counts(since),
    }
    path = Path("/tmp/dhan_ws_multicycle.json")
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("SUMMARY", out["passed"], "/", out["total"], "failed", failed)
    print("JOURNAL", json.dumps(out["journal"]))
    print("WROTE", path)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
