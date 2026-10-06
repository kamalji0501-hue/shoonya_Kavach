#!/usr/bin/env python3
"""One Crude option via Datafeed IPC. NO ORDERS."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/ubuntu/Datafeedbot")
from datafeedbot.config import Settings
from datafeedbot.ipc import IpcClient

SID = "573424"  # CRUDEOIL 17Aug 9150 CE
print("TS", datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"))
s = Settings.load()
c = IpcClient(s.ipc_socket)
c.connect()
sub = c.send_control(
    "subscribe",
    underlying="CRUDEOIL",
    strike=9150,
    option_type="CE",
    dhan_security_id=SID,
)
print("SUB", {k: sub.get(k) for k in ("type", "dhan_security_id", "zerodha_instrument_token", "instrument")})
sources: dict[str, int] = {}
last = {}
deadline = time.time() + 10.0
while time.time() < deadline:
    msg = c.recv(timeout=1.0)
    if not msg or msg.get("type") != "tick":
        continue
    d = msg.get("data") or {}
    src = str(d.get("source") or "?")
    sources[src] = sources.get(src, 0) + 1
    last = d
st = c.send_control("get_state")
h = ((st.get("data") or {}).get("health") or {}).get("DHAN_WS") or {}
c.send_control("unsubscribe")
c.close()
print(
    json.dumps(
        {
            "sources": sources,
            "last_ltp": last.get("ltp"),
            "last_bid": last.get("bid"),
            "last_ask": last.get("ask"),
            "dhan": {
                "connected": h.get("connected"),
                "status": h.get("status"),
                "packets": h.get("packet_count"),
                "err": h.get("last_error"),
            },
        },
        default=str,
    )
)
ok = (
    sub.get("type") == "subscribed"
    and h.get("connected") is True
    and int(sources.get("DHAN_WS") or 0) > 0
    and "429" not in str(h.get("last_error") or "")
)
raise SystemExit(0 if ok else 1)
