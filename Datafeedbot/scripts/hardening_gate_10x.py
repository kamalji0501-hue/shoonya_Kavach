#!/usr/bin/env python3
"""Hardening regression gate — run N times. Does NOT touch trail/OMS math."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DF = Path("/home/ubuntu/Datafeedbot")
BZ = Path("/home/ubuntu/BlitzBot")
GO = Path("/home/ubuntu/GoBot")
OUT = Path("/home/ubuntu/Datafeedbot_Runtime/Logs/hardening_gate_10x.json")


def unit(u: str) -> str:
    p = subprocess.run(["systemctl", "is-active", u], text=True, capture_output=True)
    return (p.stdout or "").strip()


def run_once(i: int) -> dict:
    results = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1 pytest datafeed
    p = subprocess.run(
        [str(DF / ".venv/bin/pytest"), "-q", "--tb=line"],
        cwd=str(DF),
        text=True,
        capture_output=True,
        timeout=180,
    )
    out = (p.stdout or "") + (p.stderr or "")
    last = [ln for ln in out.strip().splitlines() if "passed" in ln]
    add(
        "T01_pytest",
        p.returncode == 0 and bool(last) and "failed" not in last[-1],
        last[-1] if last else out[-200:],
    )

    # 2 rest guard import + behavior
    sys.path.insert(0, str(DF))
    from datafeedbot.rest_guard import RestRateGuard

    g = RestRateGuard(min_interval_ms=100, cooldown_on_429_ms=150)
    assert g.try_acquire().allowed
    assert not g.try_acquire().allowed
    g.record_failure(is_429=True)
    add("T02_rest_guard", g.stats()["http_429"] == 1, str(g.stats()))

    # 3 dhan rest outcome type
    from datafeedbot.brokers.dhan.provider import dhan_rest_option_quote
    from datafeedbot.rest_guard import RestQuoteOutcome

    # bad sid → outcome error, no exception
    o = dhan_rest_option_quote(
        client_id="x", access_token="y", security_id="not-int", instrument_key="NIFTY_1_CE"
    )
    add("T03_rest_outcome_type", isinstance(o, RestQuoteOutcome) and o.tick is None, o.error)

    # 4 engine has ops_snapshot / rest_guard
    from datafeedbot.config import Settings
    from datafeedbot.engine import DatafeedEngine

    # don't start network — just construct with dummy settings if possible
    add(
        "T04_engine_api",
        hasattr(DatafeedEngine, "ops_snapshot") and hasattr(DatafeedEngine, "health_pulse"),
        "ops+pulse",
    )

    # 5 ipc client_count
    from datafeedbot.ipc import IpcServer

    add("T05_ipc_client_count", hasattr(IpcServer, "client_count"), "ok")

    # 6 services
    df_a, bz_a, go_a = unit("datafeedbot"), unit("blitzbot"), unit("gobot")
    add(
        "T06_services",
        df_a == "active" and bz_a == "active" and go_a == "inactive",
        f"df={df_a} bz={bz_a} go={go_a}",
    )

    # 7 trail math still identical path (Blitz import — unchanged API)
    if str(BZ) not in sys.path:
        sys.path.insert(0, str(BZ))
    from GO.strategy.single_leg import check_exit, initial_risk, update_trailing

    risk = initial_risk(100.0, sl_rupees=10.0, target_rupees=0.0)
    risk = update_trailing(risk, 106.0, activate_rupees=5.0, trail_sl_distance=10.0)
    risk = update_trailing(risk, 110.0, activate_rupees=5.0, trail_sl_distance=10.0)
    reason = check_exit(risk, 99.0)
    add("T07_trail_untouched", reason == "trailing_sl", f"exit={reason}")

    # 8 core file hashes still match (book etc)
    import hashlib

    same = True
    for rel in [
        "GO/book.py",
        "GO/protect_trail_sync.py",
        "GO/order_manager.py",
        "GO/punch.py",
        "GO/broker_protect_sl.py",
    ]:
        gb = (GO / rel).read_bytes()
        bb = (BZ / rel).read_bytes()
        if hashlib.sha256(gb).hexdigest() != hashlib.sha256(bb).hexdigest():
            same = False
            break
    add("T08_core_logic_intact", same, "hashes")

    # 9 single_leg still only feed-diff (has DatafeedOptionFeed)
    sl = (BZ / "GO/strategy/single_leg.py").read_text(encoding="utf-8")
    add(
        "T09_feed_adapter_only",
        "DatafeedOptionFeed" in sl and "update_trailing" in sl,
        "ok",
    )

    # 10 IPC sock + ping via python
    sock = Path("/home/ubuntu/Datafeedbot_Runtime/ipc/market.sock")
    ok_sock = sock.exists()
    ping_ok = False
    detail = "no_sock"
    if ok_sock:
        try:
            from datafeedbot.ipc import IpcClient

            c = IpcClient(sock)
            c.connect(timeout=3.0)
            resp = c.send_control("ping")
            c.close()
            ping_ok = resp.get("type") == "pong"
            detail = str(resp)[:120]
        except Exception as exc:
            detail = str(exc)[:160]
    add("T10_ipc_live_ping", ok_sock and ping_ok, detail)

    # 11 get_state has ops
    ops_ok = False
    d11 = ""
    if ok_sock:
        try:
            from datafeedbot.ipc import IpcClient

            c = IpcClient(sock)
            c.connect(timeout=3.0)
            st = c.send_control("get_state")
            c.close()
            data = st.get("data") or {}
            ops_ok = isinstance(data.get("ops"), dict) and "rest_guard" in data["ops"]
            d11 = f"keys={list(data.keys())[:8]} ops={list((data.get('ops') or {}).keys())[:8]}"
        except Exception as exc:
            d11 = str(exc)[:160]
    add("T11_ops_snapshot", ops_ok, d11)

    # 12 datafeed feed module has gap logging
    feed = (BZ / "GO/datafeed_option_feed.py").read_text(encoding="utf-8")
    add(
        "T12_feed_logging",
        "sequence gap" in feed and "Datafeed feed pulse" in feed,
        "ok",
    )

    passed = sum(1 for r in results if r["ok"])
    return {
        "run": i,
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "results": results,
        "ts": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    }


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    runs = []
    all_ok = True
    for i in range(1, n + 1):
        try:
            r = run_once(i)
        except Exception:
            r = {
                "run": i,
                "passed": 0,
                "failed": 1,
                "total": 1,
                "error": traceback.format_exc(),
                "results": [],
            }
        runs.append(r)
        if r.get("failed", 1) or r.get("error"):
            all_ok = False
        print(f"RUN {i:02d}/{n}: {r.get('passed')}/{r.get('total')} failed={r.get('failed')}")
        for t in r.get("results", []):
            if not t["ok"]:
                print("  FAIL", t["name"], t.get("detail", "")[:200])
        if r.get("error"):
            print(r["error"][:500])
        time.sleep(0.2)

    summary = {
        "iterations": n,
        "all_runs_clean": all_ok,
        "runs_passed": sum(1 for r in runs if r.get("failed") == 0 and not r.get("error")),
        "runs_failed": sum(1 for r in runs if r.get("failed", 1) > 0 or r.get("error")),
        "runs": runs,
        "finished_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("==== OVERALL ====")
    print(json.dumps({k: summary[k] for k in ["iterations", "all_runs_clean", "runs_passed", "runs_failed"]}, indent=2))
    print("wrote", OUT)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
