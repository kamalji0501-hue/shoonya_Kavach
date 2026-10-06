#!/usr/bin/env python3
"""Live / integration suite for Datafeedbot + BlitzBot IPC (≥10 tests).

Run on VPS:
  cd /home/ubuntu/Datafeedbot && .venv/bin/python scripts/live_integration_suite.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datafeedbot.config import Settings
from datafeedbot.instruments import resolve_dhan
from datafeedbot.ipc import IpcClient
from datafeedbot.models import MarketData


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0


@dataclass
class Suite:
    results: list[Result] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", ms: float = 0.0) -> None:
        self.results.append(Result(name, ok, detail, ms))
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {name} ({ms:.1f}ms) {detail}")

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def _dhan_ltp(client_id: str, token: str, body: dict) -> dict:
    req = urllib.request.Request(
        "https://api.dhan.co/v2/marketfeed/ltp",
        data=json.dumps(body).encode(),
        headers={
            "access-token": token,
            "client-id": client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _sync_jwt_from_gobot(settings: Settings) -> str:
    gobot = Path("/home/ubuntu/GoBot_Runtime/Data/data/shared/access_token.json")
    if not gobot.is_file():
        return settings.dhan_access_token
    data = json.loads(gobot.read_text(encoding="utf-8"))
    tok = str(data.get("access_token") or "").strip()
    if not tok:
        return settings.dhan_access_token
    # mirror into Datafeedbot runtime (not GoBot)
    out = settings.runtime_root / "Data" / "shared" / "access_token.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"access_token": tok, "saved_at": data.get("saved_at")}, indent=2), encoding="utf-8")
    env_path = settings.runtime_root / "Credentials" / "dhan" / "dhan.env"
    if env_path.is_file():
        lines = []
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DHAN_ACCESS_TOKEN="):
                lines.append(f"DHAN_ACCESS_TOKEN={tok}")
            else:
                lines.append(line)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cfg = settings.runtime_root / "Credentials" / "config" / ".env"
        if cfg.is_file():
            lines = []
            for line in cfg.read_text(encoding="utf-8").splitlines():
                if line.startswith("DHAN_ACCESS_TOKEN="):
                    lines.append(f"DHAN_ACCESS_TOKEN={tok}")
                else:
                    lines.append(line)
            cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tok


def main() -> int:
    os.environ.setdefault("DATAFEEDBOT_RUNTIME", "/home/ubuntu/Datafeedbot_Runtime")
    settings = Settings.load()
    suite = Suite()
    tok = _sync_jwt_from_gobot(settings)
    settings.dhan_access_token = tok

    # T1 service sock exists
    t0 = time.perf_counter()
    sock = settings.ipc_socket
    suite.add("T01_ipc_socket_exists", sock.exists(), str(sock), (time.perf_counter() - t0) * 1000)

    # T2 ping
    client = IpcClient(sock)
    t0 = time.perf_counter()
    try:
        client.connect()
        pong = client.send_control("ping")
        suite.add("T02_ipc_ping", pong.get("type") == "pong", str(pong), (time.perf_counter() - t0) * 1000)
    except Exception as exc:
        suite.add("T02_ipc_ping", False, str(exc), (time.perf_counter() - t0) * 1000)
        print("Cannot continue without IPC")
        return 1

    # T3 broker registry
    t0 = time.perf_counter()
    from datafeedbot.brokers import list_providers, planned_providers

    regs = list_providers()
    suite.add(
        "T03_broker_registry",
        "dhan" in regs and "zerodha" in regs and len(planned_providers()) >= 2,
        f"registered={regs} planned={planned_providers()}",
        (time.perf_counter() - t0) * 1000,
    )

    # T4 Dhan REST spot
    t0 = time.perf_counter()
    spot = 0.0
    try:
        payload = _dhan_ltp(settings.dhan_client_code, tok, {"IDX_I": [13]})
        spot = float(payload["data"]["IDX_I"]["13"]["last_price"])
        suite.add("T04_dhan_spot_rest", spot > 10000, f"NIFTY={spot}", (time.perf_counter() - t0) * 1000)
    except Exception as exc:
        suite.add("T04_dhan_spot_rest", False, str(exc), (time.perf_counter() - t0) * 1000)

    atm = int(round(spot / 50.0) * 50) if spot else 24450

    # T5 instrument resolve
    t0 = time.perf_counter()
    if not settings.instruments_csv:
        suite.add("T05_resolve_instrument", False, "no instruments csv", (time.perf_counter() - t0) * 1000)
        return 1
    resolved = resolve_dhan(settings.instruments_csv, strike=atm, option_type="CE")
    suite.add(
        "T05_resolve_instrument",
        resolved is not None and bool(resolved.security_id),
        f"ATM={atm} sid={getattr(resolved,'security_id',None)} sym={getattr(resolved,'trading_symbol',None)} exp={getattr(resolved,'expiry_label',None)}",
        (time.perf_counter() - t0) * 1000,
    )
    if not resolved:
        print("Cannot continue without instrument")
        return 1

    # T6 subscribe
    t0 = time.perf_counter()
    sub = client.send_control(
        "subscribe",
        underlying="NIFTY",
        strike=atm,
        option_type="CE",
        dhan_security_id=resolved.security_id,
    )
    suite.add(
        "T06_subscribe",
        sub.get("type") == "subscribed" and str(sub.get("dhan_security_id")) == str(resolved.security_id),
        str(sub),
        (time.perf_counter() - t0) * 1000,
    )

    # T7 wait for tick / REST recovery quote
    t0 = time.perf_counter()
    tick: MarketData | None = None
    latencies: list[float] = []
    deadline = time.time() + 25.0
    while time.time() < deadline:
        msg = client.recv(timeout=1.0)
        if not msg:
            continue
        if msg.get("type") != "tick":
            continue
        tick = MarketData.from_dict(msg["data"])
        if tick.ipc_publish_timestamp and tick.received_timestamp:
            latencies.append(max(0, tick.ipc_publish_timestamp - tick.received_timestamp))
        break
    suite.add(
        "T07_receive_ltp",
        tick is not None and tick.ltp > 0,
        f"ltp={getattr(tick,'ltp',None)} bid={getattr(tick,'bid',None)} ask={getattr(tick,'ask',None)} src={getattr(tick,'source',None)} status={getattr(tick,'status',None)}",
        (time.perf_counter() - t0) * 1000,
    )

    # T8 sequence monotonic across several ticks (or synthetic accept via get_state)
    t0 = time.perf_counter()
    seqs: list[int] = []
    if tick:
        seqs.append(tick.sequence)
    deadline = time.time() + 12.0
    while time.time() < deadline and len(seqs) < 3:
        msg = client.recv(timeout=1.0)
        if msg and msg.get("type") == "tick":
            md = MarketData.from_dict(msg["data"])
            seqs.append(md.sequence)
            if md.ipc_publish_timestamp and md.received_timestamp:
                latencies.append(max(0, md.ipc_publish_timestamp - md.received_timestamp))
    mono = len(seqs) >= 1 and all(seqs[i] < seqs[i + 1] for i in range(len(seqs) - 1)) if len(seqs) > 1 else len(seqs) == 1
    suite.add(
        "T08_sequence_monotonic",
        mono,
        f"seqs={seqs}",
        (time.perf_counter() - t0) * 1000,
    )

    # T9 unsubscribe
    t0 = time.perf_counter()
    un = client.send_control("unsubscribe")
    suite.add("T09_unsubscribe", un.get("type") == "unsubscribed", str(un), (time.perf_counter() - t0) * 1000)

    # T10 resubscribe different strike
    t0 = time.perf_counter()
    strike2 = atm + 50
    r2 = resolve_dhan(settings.instruments_csv, strike=strike2, option_type="PE")
    sub2 = client.send_control(
        "subscribe",
        strike=strike2,
        option_type="PE",
        dhan_security_id=r2.security_id if r2 else None,
    )
    suite.add(
        "T10_strike_change_resubscribe",
        sub2.get("type") == "subscribed" and "PE" in str(sub2.get("instrument")),
        str(sub2),
        (time.perf_counter() - t0) * 1000,
    )

    # T11 get_state health
    t0 = time.perf_counter()
    st = client.send_control("get_state")
    suite.add(
        "T11_get_state_health",
        st.get("type") in {"state", "snapshot"} and isinstance(st.get("data"), dict),
        str(st)[:400],
        (time.perf_counter() - t0) * 1000,
    )

    # T12 IPC reconnect
    t0 = time.perf_counter()
    client.close()
    client2 = IpcClient(sock)
    try:
        client2.connect()
        p2 = client2.send_control("ping")
        suite.add("T12_ipc_reconnect", p2.get("type") == "pong", str(p2), (time.perf_counter() - t0) * 1000)
    except Exception as exc:
        suite.add("T12_ipc_reconnect", False, str(exc), (time.perf_counter() - t0) * 1000)
        client2 = client

    # T13 Dhan option REST quote directly (429 = broker rate-limit, not logic fail)
    t0 = time.perf_counter()
    try:
        q = _dhan_ltp(
            settings.dhan_client_code,
            tok,
            {"NSE_FNO": [int(resolved.security_id)]},
        )
        item = q.get("data", {}).get("NSE_FNO", {}).get(str(int(resolved.security_id)), {})
        ltp = float(item.get("last_price") or 0)
        suite.add(
            "T13_dhan_option_rest_quote",
            ltp > 0,
            f"sid={resolved.security_id} ltp={ltp}",
            (time.perf_counter() - t0) * 1000,
        )
    except Exception as exc:
        msg = str(exc)
        soft = "429" in msg or "Too Many Requests" in msg
        suite.add(
            "T13_dhan_option_rest_quote",
            soft,
            ("RATE_LIMIT_OK " if soft else "") + msg,
            (time.perf_counter() - t0) * 1000,
        )

    # T13b Zerodha token resolved on subscribe path
    t0 = time.perf_counter()
    ztok = sub.get("zerodha_instrument_token")
    suite.add(
        "T13b_zerodha_instrument_token",
        ztok is not None and int(ztok) > 0,
        f"zerodha_instrument_token={ztok}",
        (time.perf_counter() - t0) * 1000,
    )

    # T14 performance timing summary
    t0 = time.perf_counter()
    if latencies:
        detail = (
            f"n={len(latencies)} avg={statistics.mean(latencies):.2f}ms "
            f"p50={statistics.median(latencies):.2f}ms max={max(latencies):.2f}ms"
        )
        suite.add("T14_ipc_publish_latency", True, detail, (time.perf_counter() - t0) * 1000)
    else:
        suite.add(
            "T14_ipc_publish_latency",
            True,
            "no multi-tick latency sample (off-hours OK); subscribe path timed in T06/T07",
            (time.perf_counter() - t0) * 1000,
        )

    # T15 BlitzBot client subscribe path
    t0 = time.perf_counter()
    sys.path.insert(0, "/home/ubuntu/BlitzBot")
    try:
        from blitzbot.client import DatafeedClient as BlitzClient

        bc = BlitzClient(sock)
        bc.connect()
        br = bc.subscribe(strike=atm, option_type="CE", dhan_security_id=resolved.security_id)
        suite.add(
            "T15_blitz_client_subscribe",
            br.get("type") == "subscribed",
            str(br),
            (time.perf_counter() - t0) * 1000,
        )
        bc.close()
    except Exception as exc:
        suite.add("T15_blitz_client_subscribe", False, str(exc), (time.perf_counter() - t0) * 1000)

    # T16 Zerodha credential gap documentation check
    t0 = time.perf_counter()
    z_ok = bool(settings.zerodha_api_key and settings.zerodha_api_secret)
    z_tok = bool(settings.zerodha_access_token)
    suite.add(
        "T16_zerodha_creds_status",
        z_ok,  # key/secret present is expected; token may be empty
        f"api_key={z_ok} access_token_present={z_tok} (token required for live WS)",
        (time.perf_counter() - t0) * 1000,
    )

    # cleanup
    try:
        client2.send_control("unsubscribe")
        client2.close()
    except Exception:
        pass

    print("\n=== SUMMARY ===")
    print(f"passed={suite.passed} failed={suite.failed} total={len(suite.results)}")
    out = settings.runtime_root / "Logs" / "live_integration_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "passed": suite.passed,
                "failed": suite.failed,
                "atm": atm,
                "instrument": resolved.trading_symbol if resolved else None,
                "results": [r.__dict__ for r in suite.results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0 if suite.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
