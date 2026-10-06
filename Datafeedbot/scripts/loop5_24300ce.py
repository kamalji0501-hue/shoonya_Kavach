#!/usr/bin/env python3
"""5-cycle persistent 24300 CE feed test. No orders. No Blitz logic changes."""
from __future__ import annotations

import json
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
OUT = Path("/home/ubuntu/Datafeedbot_Runtime/Logs/loop5_24300ce.json")


def analyze(ticks: list[dict]) -> dict:
    sources: dict[str, int] = {}
    seqs = []
    ba = 0
    last = {}
    gaps = 0
    prev = None
    for t in ticks:
        src = str(t.get("source") or "?")
        sources[src] = sources.get(src, 0) + 1
        try:
            s = int(t.get("sequence") or 0)
        except (TypeError, ValueError):
            s = 0
        if s:
            if prev is not None and s > prev + 1:
                gaps += s - prev - 1
            prev = s
            seqs.append(s)
        bid, ask = t.get("bid"), t.get("ask")
        try:
            if float(bid or 0) > 0 and float(ask or 0) > 0:
                ba += 1
        except (TypeError, ValueError):
            pass
        last = t
    return {
        "n": len(ticks),
        "sources": sources,
        "bidask_n": ba,
        "gaps": gaps,
        "seq_first": seqs[0] if seqs else None,
        "seq_last": seqs[-1] if seqs else None,
        "ltp": last.get("ltp"),
        "bid": last.get("bid"),
        "ask": last.get("ask"),
        "last_source": last.get("source"),
        "dual": any(k.startswith("DHAN") for k in sources) and any(k.startswith("ZERODHA") for k in sources),
    }


def main() -> int:
    settings = Settings.load()
    resolved = resolve_dhan(settings.instruments_csv, strike=24300, option_type="CE")
    client = IpcClient(settings.ipc_socket)
    client.connect()
    sub = {"type": "error"}
    for attempt in range(1, 4):
        sub = client.send_control(
            "subscribe",
            underlying="NIFTY",
            strike=24300,
            option_type="CE",
            dhan_security_id=resolved.security_id if resolved else None,
        )
        print("sub_attempt", attempt, sub)
        if sub.get("type") == "subscribed":
            break
        time.sleep(1.5)
    cycles = []
    try:
        for i in range(1, 6):
            ticks = []
            end = time.time() + 10.0
            while time.time() < end:
                msg = client.recv(timeout=0.5)
                if msg and msg.get("type") == "tick":
                    ticks.append(msg.get("data") or {})
            st = client.send_control("get_state")
            data = st.get("data") or {}
            health = data.get("health") or {}
            stats = analyze(ticks)
            dhan = health.get("DHAN_WS") or {}
            z = health.get("ZERODHA_WS") or {}
            ok = stats["n"] >= 3 and stats["bidask_n"] >= 1 and (
                bool(dhan.get("connected")) or bool(z.get("connected"))
            )
            row = {
                "cycle": i,
                "ok": ok,
                **stats,
                "dhan_connected": bool(dhan.get("connected")),
                "dhan_status": dhan.get("status"),
                "dhan_err": (dhan.get("last_error") or "")[:120],
                "z_connected": bool(z.get("connected")),
                "z_status": z.get("status"),
                "ops": (data.get("ops") or {}).get("tick_counts"),
            }
            cycles.append(row)
            print(
                f"C{i} ok={ok} n={stats['n']} ltp={stats['ltp']} bid={stats['bid']} "
                f"ask={stats['ask']} src={stats['sources']} dual={stats['dual']} "
                f"dhan={row['dhan_connected']}/{row['dhan_status']} z={row['z_connected']}"
            )
    finally:
        try:
            client.send_control("unsubscribe")
        except Exception:
            pass
        client.close()
    passed = sum(1 for r in cycles if r["ok"])
    summary = {
        "ts": datetime.now(IST).isoformat(timespec="seconds"),
        "passed": passed,
        "total": 5,
        "dual_any": any(r["dual"] for r in cycles),
        "dhan_any": any(r["dhan_connected"] for r in cycles),
        "zerodha_any": any(r["z_connected"] for r in cycles),
    }
    OUT.write_text(json.dumps({"summary": summary, "cycles": cycles}, indent=2), encoding="utf-8")
    print("SUMMARY", json.dumps(summary))
    return 0 if passed == 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
