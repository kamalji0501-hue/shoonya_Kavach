#!/usr/bin/env python3
"""One persistent 60s subscribe after 429 cooldown. No orders."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
sys.path.insert(0, "/home/ubuntu/Datafeedbot")
from datafeedbot.config import Settings
from datafeedbot.instruments import resolve_dhan
from datafeedbot.ipc import IpcClient

settings = Settings.load()
resolved = resolve_dhan(settings.instruments_csv, strike=24300, option_type="CE")
print("resolved", resolved.security_id if resolved else None, resolved.expiry_label if resolved else None)
c = IpcClient(settings.ipc_socket)
c.connect()
sub = c.send_control(
    "subscribe",
    underlying="NIFTY",
    strike=24300,
    option_type="CE",
    dhan_security_id=resolved.security_id if resolved else None,
)
print("sub", sub)
ticks = []
deadline = time.time() + 60
while time.time() < deadline:
    msg = c.recv(timeout=1.0)
    if not msg or msg.get("type") != "tick":
        continue
    d = msg.get("data") or {}
    ticks.append(d)
st = c.send_control("get_state")
c.send_control("unsubscribe")
c.close()
data = st.get("data") or {}
health = data.get("health") or {}
ops = data.get("ops") or {}
sources = {}
for t in ticks:
    s = str(t.get("source") or "?")
    sources[s] = sources.get(s, 0) + 1
last = ticks[-1] if ticks else {}
out = {
    "n": len(ticks),
    "sources": sources,
    "last_ltp": last.get("ltp"),
    "last_bid": last.get("bid"),
    "last_ask": last.get("ask"),
    "last_source": last.get("source"),
    "last_seq": last.get("sequence"),
    "health": {
        k: {"connected": (v or {}).get("connected"), "status": (v or {}).get("status"), "err": (v or {}).get("last_error")}
        for k, v in health.items()
    },
    "ops_ticks": ops.get("tick_counts"),
}
print(json.dumps(out, indent=2, default=str))
Path("/home/ubuntu/Datafeedbot_Runtime/Logs/live_24300ce_60s.json").write_text(
    json.dumps(out, indent=2, default=str), encoding="utf-8"
)
