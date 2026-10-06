#!/usr/bin/env python3
"""Live 24300 CE dual-broker feed validation + mock consumer tests.

NO ORDERS. Market-data only via Datafeedbot IPC.
"""
from __future__ import annotations

import json
import statistics
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
OPT = "CE"
OUT = Path("/home/ubuntu/Datafeedbot_Runtime/Logs/live_24300ce_validation.json")
CYCLES = 12
MOCKS = 5
COLLECT_SEC = 12.0


def now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def resolve_latest(settings: Settings):
    resolved = resolve_dhan(
        settings.instruments_csv,
        strike=STRIKE,
        option_type=OPT,
        expiry="",  # empty = nearest/latest in resolver
    )
    return resolved


def collect_ticks(client: IpcClient, seconds: float) -> list[dict]:
    ticks: list[dict] = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        msg = client.recv(timeout=0.5)
        if not msg:
            continue
        if msg.get("type") != "tick":
            continue
        data = msg.get("data") or {}
        ticks.append(data)
    return ticks


def analyze(ticks: list[dict]) -> dict:
    if not ticks:
        return {"ok": False, "reason": "no_ticks", "n": 0}
    ltps, bids, asks, seqs, sources = [], [], [], [], []
    bidask_n = 0
    spread_ok = 0
    gaps = 0
    last_seq = None
    for t in ticks:
        try:
            ltp = float(t.get("ltp") or 0)
        except (TypeError, ValueError):
            continue
        if ltp <= 0:
            continue
        ltps.append(ltp)
        src = str(t.get("source") or "")
        if src:
            sources.append(src)
        seq = t.get("sequence")
        try:
            seq_i = int(seq) if seq is not None else None
        except (TypeError, ValueError):
            seq_i = None
        if seq_i is not None:
            if last_seq is not None and seq_i > last_seq + 1:
                gaps += seq_i - last_seq - 1
            last_seq = seq_i
            seqs.append(seq_i)
        bid = t.get("bid")
        ask = t.get("ask")
        try:
            bf = float(bid) if bid not in (None, "") else None
            af = float(ask) if ask not in (None, "") else None
        except (TypeError, ValueError):
            bf = af = None
        if bf and af and bf > 0 and af > 0:
            bidask_n += 1
            bids.append(bf)
            asks.append(af)
            if af >= bf and abs(af - bf) < max(50.0, ltp * 0.5):
                spread_ok += 1
    sources_set = sorted(set(sources))
    seq_mono = all(seqs[i] < seqs[i + 1] for i in range(len(seqs) - 1)) if len(seqs) >= 2 else bool(seqs)
    dual = any(s.startswith("DHAN") for s in sources_set) and any(
        s.startswith("ZERODHA") for s in sources_set
    )
    ltp_ok = len(ltps) >= 3 and min(ltps) > 0
    ba_ok = bidask_n >= 1
    ok = ltp_ok and seq_mono and ba_ok
    return {
        "ok": ok,
        "n": len(ticks),
        "ltp_n": len(ltps),
        "ltp_last": ltps[-1] if ltps else None,
        "ltp_min": min(ltps) if ltps else None,
        "ltp_max": max(ltps) if ltps else None,
        "ltp_med": statistics.median(ltps) if ltps else None,
        "bidask_n": bidask_n,
        "bid_last": bids[-1] if bids else None,
        "ask_last": asks[-1] if asks else None,
        "spread_ok": spread_ok,
        "sources": sources_set,
        "source_counts": {s: sources.count(s) for s in sources_set},
        "seq_first": seqs[0] if seqs else None,
        "seq_last": seqs[-1] if seqs else None,
        "seq_mono": seq_mono,
        "seq_gaps": gaps,
        "dual_broker_ticks": dual,
        "ltp_ok": ltp_ok,
        "bidask_ok": ba_ok,
    }


def one_cycle(i: int, client: IpcClient, sub: dict) -> dict:
    row: dict = {"cycle": i, "ts": now_ist(), "ok": False}
    pong = client.send_control("ping")
    ticks = collect_ticks(client, COLLECT_SEC)
    st = client.send_control("get_state")
    data = st.get("data") or {}
    health = data.get("health") or {}
    ops = data.get("ops") or {}
    stats = analyze(ticks)
    dhan_ws = bool((health.get("DHAN_WS") or {}).get("connected"))
    z_ws = bool((health.get("ZERODHA_WS") or {}).get("connected"))
    row.update(stats)
    row["ping"] = pong.get("type")
    row["subscribe"] = sub.get("type")
    row["instrument"] = sub.get("instrument") or data.get("active")
    row["dhan_sid"] = sub.get("dhan_security_id")
    row["zerodha_token"] = sub.get("zerodha_instrument_token")
    row["health_dhan_ws"] = dhan_ws
    row["health_zerodha_ws"] = z_ws
    row["dhan_ws_status"] = (health.get("DHAN_WS") or {}).get("status")
    row["ops_ipc_clients"] = ops.get("ipc_clients")
    row["ops_tick_counts"] = ops.get("tick_counts")
    row["ops_last_source"] = ops.get("last_source")
    row["active"] = data.get("active")
    latest = data.get("latest") or {}
    row["latest_ltp"] = latest.get("ltp")
    row["latest_source"] = latest.get("source")
    row["ok"] = bool(
        row.get("ping") == "pong"
        and row.get("subscribe") == "subscribed"
        and stats.get("ok")
        and (dhan_ws or z_ws)
    )
    return row


def mock_consumer(i: int, client: IpcClient, resolved) -> dict:
    """Blitz-like consumer: consume N ticks → map OptionWsFeed shape. No orders."""
    out: dict = {"mock": i, "ok": False}
    mapped = []
    deadline = time.time() + 8.0
    while time.time() < deadline and len(mapped) < 8:
        msg = client.recv(timeout=0.5)
        if not msg or msg.get("type") != "tick":
            continue
        d = msg.get("data") or {}
        try:
            ltp = float(d.get("ltp") or 0)
        except (TypeError, ValueError):
            continue
        if ltp <= 0:
            continue
        mapped.append(
            {
                "ltp": ltp,
                "bid": d.get("bid"),
                "ask": d.get("ask"),
                "volume": d.get("volume"),
                "security_id": resolved.security_id if resolved else None,
                "type": "Datafeed",
                "source": d.get("source"),
                "sequence": d.get("sequence"),
                "status": d.get("status"),
            }
        )
    out["subscribe"] = "subscribed"
    out["mapped_n"] = len(mapped)
    out["sample"] = mapped[-1] if mapped else None
    out["all_positive_ltp"] = all(float(t["ltp"]) > 0 for t in mapped) if mapped else False
    out["has_seq"] = all(t.get("sequence") is not None for t in mapped) if mapped else False
    out["ok"] = len(mapped) >= 3 and out["all_positive_ltp"] and out["has_seq"]
    return out


def main() -> int:
    settings = Settings.load()
    resolved = resolve_latest(settings)
    header = {
        "ts": now_ist(),
        "strike": STRIKE,
        "option_type": OPT,
        "resolved": None if not resolved else {
            "security_id": resolved.security_id,
            "trading_symbol": resolved.trading_symbol,
            "custom_symbol": resolved.custom_symbol,
            "expiry_label": resolved.expiry_label,
            "strike": resolved.strike,
            "lot_size": resolved.lot_size,
        },
        "ipc": str(settings.ipc_socket),
        "csv": str(settings.instruments_csv),
    }
    print("==== RESOLVE ====")
    print(json.dumps(header, indent=2, default=str))
    if not resolved:
        print("FAIL: could not resolve 24300 CE")
        return 2

    client = IpcClient(settings.ipc_socket)
    client.connect()
    sub = client.send_control(
        "subscribe",
        underlying="NIFTY",
        strike=STRIKE,
        option_type=OPT,
        dhan_security_id=resolved.security_id,
    )
    print("subscribed", json.dumps(sub, default=str))
    cycles: list[dict] = []
    mocks: list[dict] = []
    try:
        for i in range(1, CYCLES + 1):
            print(f"\n===== CYCLE {i}/{CYCLES} =====")
            r = one_cycle(i, client, sub)
            cycles.append(r)
            print(
                f"  ok={r.get('ok')} ltp={r.get('ltp_last')} bid={r.get('bid_last')} "
                f"ask={r.get('ask_last')} n={r.get('n')} src={r.get('sources')} "
                f"seq={r.get('seq_first')}->{r.get('seq_last')} gaps={r.get('seq_gaps')} "
                f"dhan_ws={r.get('health_dhan_ws')} z_ws={r.get('health_zerodha_ws')} "
                f"dhan_status={r.get('dhan_ws_status')}"
            )
            time.sleep(0.2)

        mocks = []
        for i in range(1, MOCKS + 1):
            print(f"\n===== MOCK {i}/{MOCKS} =====")
            m = mock_consumer(i, client, resolved)
            mocks.append(m)
            print(f"  ok={m.get('ok')} mapped={m.get('mapped_n')} sample={m.get('sample')}")
            time.sleep(0.2)
    finally:
        try:
            client.send_control("unsubscribe")
        except Exception:
            pass
        client.close()

    c_pass = sum(1 for r in cycles if r.get("ok"))
    m_pass = sum(1 for r in mocks if r.get("ok"))
    summary = {
        "cycles_passed": c_pass,
        "cycles_total": CYCLES,
        "mocks_passed": m_pass,
        "mocks_total": MOCKS,
        "all_clean": c_pass == CYCLES and m_pass == MOCKS,
        "dual_ws_any": any(r.get("health_dhan_ws") and r.get("health_zerodha_ws") for r in cycles),
        "dual_tick_any": any(r.get("dual_broker_ticks") for r in cycles),
        "bidask_any": any((r.get("bidask_n") or 0) > 0 for r in cycles),
    }
    payload = {"header": header, "summary": summary, "cycles": cycles, "mocks": mocks}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print("\n===== SUMMARY =====")
    print(json.dumps(summary, indent=2))
    print("wrote", OUT)
    return 0 if summary["all_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
