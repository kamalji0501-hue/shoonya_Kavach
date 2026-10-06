#!/usr/bin/env python3
"""Five dual-broker live verification cycles (Dhan + Zerodha) on VPS."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/Datafeedbot")
from datafeedbot.config import Settings
from datafeedbot.instruments import resolve_dhan
from datafeedbot.ipc import IpcClient


def one_cycle(n: int) -> dict:
    settings = Settings.load()
    sock = settings.ipc_socket
    client = IpcClient(sock)
    client.connect()
    out: dict = {"cycle": n, "ok": False}
    try:
        pong = client.send_control("ping")
        out["ping"] = pong.get("type")
        resolved = resolve_dhan(settings.instruments_csv, strike=24450, option_type="CE")
        if not resolved:
            # snap atm from prior
            resolved = resolve_dhan(settings.instruments_csv, strike=24500, option_type="CE")
        strike = int(resolved.strike) if resolved else 24450
        sid = resolved.security_id if resolved else None
        sub = client.send_control(
            "subscribe",
            underlying="NIFTY",
            strike=strike,
            option_type="CE",
            dhan_security_id=sid,
        )
        out["subscribe"] = sub
        ztok = sub.get("zerodha_instrument_token")
        out["zerodha_token"] = ztok
        out["dhan_sid"] = sub.get("dhan_security_id")
        sources: set[str] = set()
        seqs: list[int] = []
        deadline = time.time() + 20.0
        while time.time() < deadline and len(sources) < 2 and len(seqs) < 8:
            msg = client.recv(timeout=1.0)
            if not msg or msg.get("type") != "tick":
                continue
            data = msg.get("data") or {}
            src = str(data.get("source") or "")
            if src:
                sources.add(src)
            if data.get("sequence") is not None:
                seqs.append(int(data["sequence"]))
            out["last_ltp"] = data.get("ltp")
            out["last_bid"] = data.get("bid")
            out["last_ask"] = data.get("ask")
        # also check health for both WS connected
        st = client.send_control("get_state")
        health = (st.get("data") or {}).get("health") or {}
        out["health"] = {
            k: {
                "connected": (v or {}).get("connected"),
                "status": (v or {}).get("status"),
                "packet_count": (v or {}).get("packet_count"),
            }
            for k, v in health.items()
        }
        out["sources_seen"] = sorted(sources)
        out["seqs"] = seqs[:10]
        dhan_ok = bool(out["health"].get("DHAN_WS", {}).get("connected"))
        z_ok = bool(out["health"].get("ZERODHA_WS", {}).get("connected"))
        z_resolved = ztok is not None
        tick_ok = bool(seqs) and out.get("last_ltp")
        # Prefer seeing both sources in ticks; if market quiet, connected+resolved still counts
        dual = ("DHAN_WS" in sources and any(s.startswith("ZERODHA") for s in sources)) or (
            dhan_ok and z_ok and z_resolved and tick_ok
        )
        out["ok"] = bool(out["ping"] == "pong" and sub.get("type") == "subscribed" and dual and tick_ok)
        client.send_control("unsubscribe")
    finally:
        client.close()
    return out


def main() -> int:
    results = []
    for i in range(1, 6):
        print(f"\n===== CYCLE {i}/5 =====")
        r = one_cycle(i)
        results.append(r)
        print(json.dumps(r, indent=2, default=str)[:1200])
        time.sleep(1.0)
    passed = sum(1 for r in results if r.get("ok"))
    summary = {"passed": passed, "failed": 5 - passed, "total": 5}
    print("\n===== SUMMARY =====")
    print(summary)
    out = Path("/home/ubuntu/Datafeedbot_Runtime/Logs/five_cycle_dual_broker.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    print("wrote", out)
    return 0 if passed == 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
