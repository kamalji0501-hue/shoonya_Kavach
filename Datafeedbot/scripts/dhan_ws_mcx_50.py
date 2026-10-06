#!/usr/bin/env python3
"""Dhan WS soak via Datafeed IPC. NO ORDERS. Do not touch Zerodha.

~50 scenarios across GOLD / GOLDM / SILVER / SILVERM / CRUDEOIL / CRUDEOILM
(futures + nearby CE/PE). Captures LTP / BID / ASK / volume.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/ubuntu/Datafeedbot")
from datafeedbot.brokers.dhan.provider import dhan_rest_option_quote
from datafeedbot.config import Settings
from datafeedbot.instruments import _from_row, dhan_feed_segment
from datafeedbot.ipc import IpcClient

IST = ZoneInfo("Asia/Kolkata")
HOLD_S = 5.0
SWITCH_HOLD_S = 4.0
IDLE_WAIT_S = 2.6
FAMILIES = ("GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL", "CRUDEOILM")


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
    r = subprocess.run(
        ["journalctl", "-u", "datafeedbot", "--since", since, "--no-pager"],
        capture_output=True,
        text=True,
    )
    lines = (r.stdout or "").splitlines()

    def n(pat: str) -> int:
        return sum(1 for line in lines if pat in line)

    return {
        "dhan_connected": n("Dhan WS connected"),
        "dhan_resub": n("Dhan WS resubscribed same-socket"),
        "http_429": n("HTTP 429"),
        "idle_close": n("Dhan WS idle — no subscribe"),
        "connect_failed": n("Dhan WS connect failed"),
        "ws_error": n("Dhan WS error"),
        "zerodha_sub_log": n("zerodha resolve"),
    }


def _parse_exp(label: str) -> datetime:
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime((label or "").strip()[:11], fmt)
        except ValueError:
            continue
    return datetime.max


def _load_mcx(csv_path: Path) -> list[dict]:
    rows: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            clean = {k: (v or "") for k, v in raw.items() if k and k.strip()}
            inst = _from_row(clean)
            if not inst:
                continue
            u = inst.underlying.upper()
            if u not in FAMILIES:
                continue
            if (inst.exchange or "").strip().upper() != "MCX":
                continue
            kind = (clean.get("SEM_INSTRUMENT_NAME") or "").upper()
            if not kind:
                if inst.option_type in {"CE", "PE"}:
                    kind = "OPTFUT"
                elif inst.strike == 0:
                    kind = "FUTCOM"
            rows.append(
                {
                    "sid": inst.security_id,
                    "underlying": u,
                    "opt": inst.option_type,
                    "strike": inst.strike,
                    "expiry": inst.expiry_label,
                    "exp_dt": _parse_exp(inst.expiry_label),
                    "symbol": inst.trading_symbol or inst.custom_symbol,
                    "custom": inst.custom_symbol,
                    "exchange": inst.exchange,
                    "segment": inst.segment,
                    "kind": kind,
                    "exch_code": dhan_feed_segment(inst.exchange, inst.segment)[0],
                    "exch_seg": dhan_feed_segment(inst.exchange, inst.segment)[1],
                }
            )
    return rows


def _nearest_fut(rows: list[dict], und: str) -> dict | None:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cands = [
        r
        for r in rows
        if r["underlying"] == und
        and r["kind"] in {"FUTCOM", "FUTIDX", "FUTSTK"}
        and r["exp_dt"] >= today
    ]
    if not cands:
        cands = [
            r
            for r in rows
            if r["underlying"] == und
            and r["opt"] not in {"CE", "PE"}
            and r["strike"] == 0
            and r["exp_dt"] >= today
        ]
    cands.sort(key=lambda r: r["exp_dt"])
    return cands[0] if cands else None


def _atm_opt(rows: list[dict], und: str, opt: str, atm: float | None) -> dict | None:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cands = [
        r
        for r in rows
        if r["underlying"] == und
        and r["opt"] == opt
        and r["strike"] > 0
        and r["exp_dt"] >= today
    ]
    if not cands:
        return None
    cands.sort(key=lambda r: r["exp_dt"])
    near_exp = cands[0]["exp_dt"]
    same = [r for r in cands if r["exp_dt"] == near_exp]
    if atm and atm > 0:
        same.sort(key=lambda r: abs(float(r["strike"]) - float(atm)))
    else:
        same.sort(key=lambda r: r["strike"])
        same = same[len(same) // 2 : len(same) // 2 + 1] or same
    return same[0]


def _health(client: IpcClient) -> dict:
    st = client.send_control("get_state")
    data = st.get("data") or {}
    h = (data.get("health") or {}).get("DHAN_WS") or {}
    zh = (data.get("health") or {}).get("ZERODHA_WS") or {}
    return {
        "connected": h.get("connected"),
        "status": h.get("status"),
        "packets": h.get("packet_count"),
        "err": (h.get("last_error") or "")[:160],
        "z_status": zh.get("status"),
        "z_connected": zh.get("connected"),
        "z_err": (zh.get("last_error") or "")[:80],
    }


def _collect(client: IpcClient, seconds: float) -> dict:
    ticks: list[dict] = []
    sources: dict[str, int] = defaultdict(int)
    last: dict = {}
    bid_n = ask_n = vol_n = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        msg = client.recv(timeout=0.7)
        if not msg or msg.get("type") != "tick":
            continue
        d = msg.get("data") or {}
        src = str(d.get("source") or "?")
        sources[src] += 1
        ticks.append(d)
        last = d
        if d.get("bid") not in (None, "", 0, 0.0):
            bid_n += 1
        if d.get("ask") not in (None, "", 0, 0.0):
            ask_n += 1
        if d.get("volume") not in (None, "", 0, 0.0):
            vol_n += 1
    dhan_n = int(sources.get("DHAN_WS") or 0)
    z_n = int(sources.get("ZERODHA_WS") or 0)
    return {
        "n": len(ticks),
        "dhan": dhan_n,
        "zerodha": z_n,
        "ltp": last.get("ltp"),
        "bid": last.get("bid"),
        "ask": last.get("ask"),
        "volume": last.get("volume"),
        "seg": last.get("exchange_segment") or last.get("segment") or last.get("exch_seg"),
        "sid": last.get("security_id") or last.get("broker_id"),
        "bid_ticks": bid_n,
        "ask_ticks": ask_n,
        "vol_ticks": vol_n,
        "sources": dict(sources),
    }


def main() -> int:
    print("TS", datetime.now(IST).isoformat(timespec="seconds"))
    open_book = _book_open()
    if open_book:
        print("ABORT open book", open_book, "— will not steal Dhan WS")
        return 2

    settings = Settings.load()
    csv_path = settings.instruments_csv
    if not csv_path or not csv_path.is_file():
        print("ABORT no instruments csv")
        return 2

    client_last4 = (settings.dhan_client_code or "")[-4:]
    jwt_ok = bool(settings.dhan_access_token) and len(settings.dhan_access_token) > 20
    print("CLIENT_LAST4", client_last4, "JWT", "yes" if jwt_ok else "NO")

    rows = _load_mcx(csv_path)
    print("MCX_ROWS", len(rows), "csv", csv_path)

    watch: list[dict] = []
    rest_rows: list[dict] = []
    for und in FAMILIES:
        fut = _nearest_fut(rows, und)
        atm = None
        if fut:
            time.sleep(1.05)
            q = dhan_rest_option_quote(
                client_id=settings.dhan_client_code,
                access_token=settings.dhan_access_token,
                security_id=fut["sid"],
                instrument_key=f"{und}_FUT",
                exchange_segment=fut["exch_seg"],
            )
            rest_ok = bool(q.tick) and not q.is_429 and not q.error
            rest_rows.append(
                {
                    "und": und,
                    "sid": fut["sid"],
                    "ok": rest_ok,
                    "ltp": q.tick.ltp if q.tick else None,
                    "err": q.error,
                    "is_429": q.is_429,
                    "kind": "FUT",
                }
            )
            if q.tick:
                atm = q.tick.ltp
            watch.append({**fut, "role": "FUT", "atm": atm})
        for opt in ("CE", "PE"):
            inst = _atm_opt(rows, und, opt, atm)
            if inst:
                watch.append({**inst, "role": opt, "atm": atm})

    # unique by sid, keep order
    seen: set[str] = set()
    instruments: list[dict] = []
    for w in watch:
        if w["sid"] in seen:
            continue
        seen.add(w["sid"])
        instruments.append(w)
    print("WATCH", json.dumps(
        [
            {
                "u": i["underlying"],
                "role": i["role"],
                "sid": i["sid"],
                "k": i.get("kind"),
                "strike": i.get("strike"),
                "exp": i.get("expiry"),
                "sym": i.get("symbol"),
            }
            for i in instruments
        ],
        default=str,
    ))

    since = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    client = IpcClient(settings.ipc_socket)
    client.connect()
    results: list[dict] = []
    failed = 0

    def record(name: str, ok: bool, **extra: object) -> None:
        nonlocal failed
        row = {"test": name, "ok": bool(ok), "t": _ts(), **extra}
        results.append(row)
        if not ok:
            failed += 1
        print(("PASS" if ok else "FAIL"), name, json.dumps(extra, default=str)[:500])

    try:
        record(
            "T00_jwt_present",
            jwt_ok and client_last4 == "3330",
            last4=client_last4,
            jwt_len=len(settings.dhan_access_token or ""),
        )
        rest_pass = sum(1 for r in rest_rows if r["ok"])
        record(
            "T01_rest_token_quotes",
            rest_pass >= 1 and all(not r["is_429"] for r in rest_rows),
            rest_ok=rest_pass,
            rest_total=len(rest_rows),
            quotes=rest_rows,
        )
        pong = client.send_control("ping")
        record("T02_ipc_ping", pong.get("type") == "pong", ping=pong.get("type"))
        h0 = _health(client)
        record(
            "T03_start_dhan_idle_or_healthy",
            h0.get("status") in {"IDLE", "HEALTHY", "HEALTHY_NO_TICK"},
            health=h0,
        )
        record(
            "T04_zerodha_untouched_at_start",
            h0.get("z_status") in {"HEALTHY", "HEALTHY_NO_TICK", "IDLE", "DISCONNECTED"}
            and "429" not in str(h0.get("z_err") or ""),
            health=h0,
        )

        # --- per-instrument sub / BBO / idle ---
        for i, inst in enumerate(instruments, start=1):
            label = f"{inst['underlying']}_{inst['role']}_{inst['sid']}"
            sub = client.send_control(
                "subscribe",
                underlying=inst["underlying"],
                strike=int(inst["strike"] or 0),
                option_type=inst["opt"] if inst["opt"] in {"CE", "PE"} else "CE",
                dhan_security_id=inst["sid"],
                expiry=inst.get("expiry") or "",
            )
            got = _collect(client, HOLD_S)
            h = _health(client)
            sub_ok = (
                sub.get("type") == "subscribed"
                and h.get("connected") is True
                and h.get("status") in {"HEALTHY", "HEALTHY_NO_TICK"}
                and "429" not in str(h.get("err") or "")
                and "no close frame" not in str(h.get("err") or "")
                and got["dhan"] > 0
                and float(got.get("ltp") or 0) > 0
                and got["zerodha"] == 0
            )
            record(
                f"T1{i:02d}_sub_{label}",
                sub_ok,
                sub=sub.get("type"),
                health=h,
                ticks=got,
                z_token=sub.get("zerodha_instrument_token"),
            )
            bbo_ok = got["dhan"] > 0 and got["bid_ticks"] > 0 and got["ask_ticks"] > 0
            record(
                f"T1{i:02d}b_bbo_{label}",
                bbo_ok,
                bid=got.get("bid"),
                ask=got.get("ask"),
                ltp=got.get("ltp"),
                volume=got.get("volume"),
                bid_ticks=got["bid_ticks"],
                ask_ticks=got["ask_ticks"],
                vol_ticks=got["vol_ticks"],
            )
            client.send_control("unsubscribe")
            time.sleep(IDLE_WAIT_S)
            h2 = _health(client)
            record(
                f"T1{i:02d}c_idle_{label}",
                h2.get("status") == "IDLE" and h2.get("connected") is False,
                health=h2,
            )

        # --- same-socket SID switches (no unsub) ---
        switch_list = [x for x in instruments if x["role"] in {"FUT", "CE"}][:8]
        if switch_list:
            first = switch_list[0]
            client.send_control(
                "subscribe",
                underlying=first["underlying"],
                strike=int(first["strike"] or 0),
                option_type=first["opt"] if first["opt"] in {"CE", "PE"} else "CE",
                dhan_security_id=first["sid"],
                expiry=first.get("expiry") or "",
            )
            time.sleep(1.5)
            for j, inst in enumerate(switch_list, start=1):
                label = f"{inst['underlying']}_{inst['role']}_{inst['sid']}"
                sub = client.send_control(
                    "subscribe",
                    underlying=inst["underlying"],
                    strike=int(inst["strike"] or 0),
                    option_type=inst["opt"] if inst["opt"] in {"CE", "PE"} else "CE",
                    dhan_security_id=inst["sid"],
                    expiry=inst.get("expiry") or "",
                )
                got = _collect(client, SWITCH_HOLD_S)
                h = _health(client)
                ok = (
                    sub.get("type") == "subscribed"
                    and h.get("connected") is True
                    and "429" not in str(h.get("err") or "")
                    and got["dhan"] > 0
                    and got["zerodha"] == 0
                )
                record(
                    f"T2{j:02d}_switch_{label}",
                    ok,
                    sub=sub.get("type"),
                    health=h,
                    ticks=got,
                )
            client.send_control("unsubscribe")
            time.sleep(IDLE_WAIT_S)
            h_end = _health(client)
            record(
                "T30_idle_after_switches",
                h_end.get("status") == "IDLE" and h_end.get("connected") is False,
                health=h_end,
            )

        # --- repeat 2 products (GOLD + CRUDE) twice more ---
        repeats = [
            x
            for x in instruments
            if x["underlying"] in {"GOLD", "CRUDEOIL"} and x["role"] in {"FUT", "CE"}
        ][:2]
        for k, inst in enumerate(repeats, start=1):
            for cyc in (1, 2):
                label = f"{inst['underlying']}_{inst['role']}_c{cyc}"
                sub = client.send_control(
                    "subscribe",
                    underlying=inst["underlying"],
                    strike=int(inst["strike"] or 0),
                    option_type=inst["opt"] if inst["opt"] in {"CE", "PE"} else "CE",
                    dhan_security_id=inst["sid"],
                    expiry=inst.get("expiry") or "",
                )
                got = _collect(client, HOLD_S)
                h = _health(client)
                ok = (
                    sub.get("type") == "subscribed"
                    and h.get("connected") is True
                    and got["dhan"] > 0
                    and "429" not in str(h.get("err") or "")
                )
                record(f"T4{k}{cyc}_repeat_{label}", ok, health=h, ticks=got)
                client.send_control("unsubscribe")
                time.sleep(IDLE_WAIT_S)
                h2 = _health(client)
                record(
                    f"T4{k}{cyc}b_idle_{label}",
                    h2.get("status") == "IDLE",
                    health=h2,
                )

        counts = _journal_counts(since)
        record(
            "T50_no_429",
            counts.get("http_429", 0) == 0 and counts.get("connect_failed", 0) == 0,
            journal=counts,
        )
        record(
            "T51_no_ws_error",
            counts.get("ws_error", 0) == 0,
            journal=counts,
        )
        record(
            "T52_socket_reuse",
            counts.get("http_429", 0) == 0 and counts.get("dhan_connected", 0) <= 20,
            journal=counts,
        )
        h_fin = _health(client)
        record(
            "T53_final_idle",
            h_fin.get("status") == "IDLE" and h_fin.get("connected") is False,
            health=h_fin,
        )
        record(
            "T54_zerodha_still_ok",
            h_fin.get("z_status") in {"HEALTHY", "HEALTHY_NO_TICK", "IDLE", "DISCONNECTED"}
            and "429" not in str(h_fin.get("z_err") or ""),
            health=h_fin,
        )
    finally:
        try:
            client.send_control("unsubscribe")
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass

    out = {
        "ts": datetime.now(IST).isoformat(timespec="seconds"),
        "client_last4": client_last4,
        "failed": failed,
        "passed": sum(1 for r in results if r.get("ok")),
        "total": len(results),
        "instruments": [
            {
                "underlying": i["underlying"],
                "role": i["role"],
                "sid": i["sid"],
                "strike": i.get("strike"),
                "expiry": i.get("expiry"),
                "symbol": i.get("symbol"),
            }
            for i in instruments
        ],
        "rest": rest_rows,
        "results": results,
        "journal": _journal_counts(since),
        "orders": 0,
        "zerodha_touched": False,
    }
    path = Path("/tmp/dhan_ws_mcx_50.json")
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("SUMMARY", out["passed"], "/", out["total"], "failed", failed)
    print("JOURNAL", json.dumps(out["journal"]))
    print("WROTE", path)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
