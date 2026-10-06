#!/usr/bin/env python3
"""5-cycle live feed batch for 24300 CE. Persistent subscribe. No orders."""
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
STRIKE = 24300
WINDOW = 10.0
CYCLES = 5


def main() -> int:
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    settings = Settings.load()
    resolved = resolve_dhan(settings.instruments_csv, strike=STRIKE, option_type="CE")
    if not resolved:
        print("FAIL resolve")
        return 2
    client = IpcClient(settings.ipc_socket)
    client.connect()
    sub = client.send_control(
        "subscribe",
        underlying="NIFTY",
        strike=STRIKE,
        option_type="CE",
        dhan_security_id=resolved.security_id,
    )
    cycles = []
    try:
        for i in range(1, CYCLES + 1):
            ticks = []
            deadline = time.time() + WINDOW
            while time.time() < deadline:
                msg = client.recv(timeout=0.4)
                if not msg or msg.get("type") != "tick":
                    continue
                ticks.append(msg.get("data") or {})
            st = client.send_control("get_state")
            data = st.get("data") or {}
            health = data.get("health") or {}
            ops = data.get("ops") or {}
            sources: dict[str, int] = {}
            seqs, ltps, ba = [], [], 0
            gaps = 0
            last_seq = None
            last = {}
            for t in ticks:
                src = str(t.get("source") or "?")
                sources[src] = sources.get(src, 0) + 1
                try:
                    ltp = float(t.get("ltp") or 0)
                except (TypeError, ValueError):
                    continue
                if ltp > 0:
                    ltps.append(ltp)
                try:
                    seq = int(t["sequence"]) if t.get("sequence") is not None else None
                except (TypeError, ValueError):
                    seq = None
                if seq is not None:
                    if last_seq is not None and seq > last_seq + 1:
                        gaps += seq - last_seq - 1
                    last_seq = seq
                    seqs.append(seq)
                try:
                    b = float(t["bid"]) if t.get("bid") not in (None, "") else 0
                    a = float(t["ask"]) if t.get("ask") not in (None, "") else 0
                except (TypeError, ValueError):
                    b = a = 0
                if b > 0 and a > 0:
                    ba += 1
                last = t
            dhan_h = health.get("DHAN_WS") or {}
            z_h = health.get("ZERODHA_WS") or {}
            ok = bool(ltps) and ba > 0 and (dhan_h.get("connected") or z_h.get("connected"))
            row = {
                "cycle": i,
                "ok": ok,
                "n": len(ticks),
                "ltp": last.get("ltp"),
                "bid": last.get("bid"),
                "ask": last.get("ask"),
                "sources": sources,
                "seq_first": seqs[0] if seqs else None,
                "seq_last": seqs[-1] if seqs else None,
                "gaps": gaps,
                "bidask_n": ba,
                "dhan": {
                    "connected": dhan_h.get("connected"),
                    "status": dhan_h.get("status"),
                    "err": (dhan_h.get("last_error") or "")[:120],
                    "packets": dhan_h.get("packet_count"),
                },
                "zerodha": {
                    "connected": z_h.get("connected"),
                    "status": z_h.get("status"),
                    "packets": z_h.get("packet_count"),
                },
                "ops_ticks": ops.get("tick_counts"),
            }
            cycles.append(row)
            print(
                f"B{batch}C{i} ok={ok} n={row['n']} ltp={row['ltp']} "
                f"bid={row['bid']} ask={row['ask']} src={sources} gaps={gaps} "
                f"dhan={row['dhan']['status']}/{row['dhan']['connected']} "
                f"z={row['zerodha']['status']}/{row['zerodha']['connected']}"
            )
    finally:
        try:
            client.send_control("unsubscribe")
        except Exception:
            pass
        client.close()
    passed = sum(1 for r in cycles if r["ok"])
    summary = {
        "batch": batch,
        "ts": datetime.now(IST).isoformat(),
        "passed": passed,
        "total": CYCLES,
        "dual_tick": any(
            any(k.startswith("DHAN") for k in (r.get("sources") or {}))
            and any(k.startswith("ZERODHA") for k in (r.get("sources") or {}))
            for r in cycles
        ),
        "dhan_connected_any": any(r["dhan"]["connected"] for r in cycles),
        "z_connected_any": any(r["zerodha"]["connected"] for r in cycles),
        "instrument": sub,
        "expiry": resolved.expiry_label,
        "sid": resolved.security_id,
    }
    out = {
        "summary": summary,
        "cycles": cycles,
    }
    path = Path(f"/home/ubuntu/Datafeedbot_Runtime/Logs/batch{batch}_24300ce.json")
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("SUMMARY", json.dumps(summary))
    print("wrote", path)
    return 0 if passed == CYCLES else 1


if __name__ == "__main__":
    raise SystemExit(main())
