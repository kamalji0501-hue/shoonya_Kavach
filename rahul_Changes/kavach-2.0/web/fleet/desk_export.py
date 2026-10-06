"""Compact desk snapshot for fleet aggregator (local or via /api/fleet/desk-snapshot)."""

from __future__ import annotations

import time
from typing import Any


def build_desk_snapshot(include_margin: bool = True, force_margin: bool = False) -> dict[str, Any]:
    from web.state import snapshot
    from web.fleet.host_metrics import collect_host_health

    st = snapshot()
    host = collect_host_health()
    copy_cfg: dict[str, Any] = {}
    copy_lots = None
    allow_copy = None
    channel_on = None
    role = None
    try:
        from modules.copy_trading import desk_get, load_config

        copy_cfg = desk_get()
        cfg = load_config()
        allow_copy = bool(cfg.get("allow_copy"))
        channel_on = cfg.get("channel_on") is not False
        role = str(cfg.get("role") or "")
        copy_lots = int(cfg.get("master_allowed_lots") or cfg.get("lots") or 0)
        if str(cfg.get("role") or "") == "child":
            copy_lots = int(cfg.get("lots") or 0)
    except Exception:
        pass

    sub_lots = {
        "max_sell_lots_batman": None,
        "max_ato_lots_batman": None,
        "max_sell_lots_directional": None,
        "max_ato_lots_directional": None,
        "ui_status": None,
        "start_date": None,
        "end_date": None,
    }
    try:
        from subscription.service import get_subscription_view

        view = get_subscription_view() or {}
        sub_lots = {
            "max_sell_lots_batman": view.get("max_sell_lots_batman"),
            "max_ato_lots_batman": view.get("max_ato_lots_batman"),
            "max_sell_lots_directional": view.get("max_sell_lots_directional"),
            "max_ato_lots_directional": view.get("max_ato_lots_directional"),
            "ui_status": view.get("ui_status") or view.get("status"),
            "name": view.get("name"),
            "start_date": view.get("start_date"),
            "end_date": view.get("end_date"),
        }
    except Exception:
        pass

    margin: dict[str, Any] = {"ok": False, "available": None, "skipped": True}
    if include_margin:
        try:
            from web.fleet.margin_cache import get_margin

            margin = get_margin(force=force_margin)
        except Exception as exc:
            margin = {"ok": False, "available": None, "error": str(exc)}

    paused = bool(st.get("paused"))
    status = "PAUSED" if paused else "RUNNING"
    if not st.get("ok"):
        status = "UNKNOWN"
    if not st.get("broker"):
        # still running process, broker may be off
        pass

    return {
        "ok": True,
        "ts": time.time(),
        "status": status,
        "paused": paused,
        "deployment": bool(st.get("deployment")),
        "order_mode": st.get("order_mode") or "paper",
        "day_pnl": st.get("day_pnl"),
        "batman_pnl": st.get("batman_pnl"),
        "nifty": st.get("nifty"),
        "nifty_ltp": st.get("nifty_ltp"),
        "nifty_ok": bool(st.get("nifty_ok")),
        "host": host,
        "copy": {
            "role": role or copy_cfg.get("role"),
            "allow_copy": allow_copy if allow_copy is not None else copy_cfg.get("allow_copy"),
            "channel_on": channel_on if channel_on is not None else copy_cfg.get("channel_on"),
            "lots": copy_lots,
            "link_ok": copy_cfg.get("link_ok"),
            "link_ms": copy_cfg.get("link_ms"),
            "peer_name": copy_cfg.get("peer_name"),
        },
        "subscription": sub_lots,
        "margin": margin,
    }


def apply_remote_action(action: str) -> dict[str, Any]:
    """pause / resume / copy_on / copy_off on this desk."""
    act = (action or "").strip().lower()
    if act == "pause":
        from web import commands

        return commands.pause()
    if act == "resume":
        from web import commands

        return commands.resume()
    if act in {"copy_on", "copy_off"}:
        from modules.copy_trading import desk_post

        on = act == "copy_on"
        return desk_post({"allow_copy": on, "channel_on": on})
    if act == "channel_on":
        from modules.copy_trading import desk_post

        return desk_post({"channel_on": True})
    if act == "channel_off":
        from modules.copy_trading import desk_post

        return desk_post({"channel_on": False})
    return {"ok": False, "error": f"unknown_action:{act}"}
