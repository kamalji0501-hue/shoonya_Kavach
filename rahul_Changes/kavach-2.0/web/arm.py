"""Web Register / Deploy / Buffer helpers — same backend as Telegram, no second strategy."""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from web.runtime import require_runtime

logger = logging.getLogger("batman.kavach.web.arm")


def _no_broker_error() -> str:
    """Broker-agnostic TOKEN hint (Shoonya desks must not say Zerodha-only)."""
    try:
        from core.order_broker_select import get_main_order_broker
        from core.batman_mode import workspace_root

        house = get_main_order_broker(workspace_root()) or ""
    except Exception:
        house = ""
    labels = {
        "shoonya": "Shoonya session",
        "zerodha": "Zerodha / Kite token",
        "dhan": "Dhan JWT",
    }
    label = labels.get(str(house).lower(), "broker token")
    return f"No broker connected. Set the {label} on TOKEN first."


LOT_SIZE = 65
ATO_STEP = 50
RETRACE_DEFAULT_SENTINEL = 5


_KITE_NIFTY_QUOTE_GAP_S = 5.0
_last_kite_nifty_mono = 0.0


def _cache_ltp_if_fresh(max_age_s: float = 5.0) -> float | None:
    try:
        from core.nifty_ltp_feed import read_nifty_ltp_cache

        snap = read_nifty_ltp_cache()
        if snap is None or float(snap.ltp) <= 0:
            return None
        age = float(snap.age_seconds()) if hasattr(snap, "age_seconds") else 9999.0
        if age <= max_age_s:
            return float(snap.ltp)
    except Exception as exc:
        logger.warning("nifty_ltp cache read failed: %s", exc)
    return None


def _kite_nifty_quote() -> float | None:
    """Live NIFTY via existing Kite REST. Does not open a Dhan WebSocket."""
    import time as _time

    import httpx

    from core.order_broker_select import get_main_order_broker
    from core.zerodha_credentials import load_zerodha_order_creds

    global _last_kite_nifty_mono
    now = _time.monotonic()
    if now - _last_kite_nifty_mono < _KITE_NIFTY_QUOTE_GAP_S:
        return None
    try:
        if get_main_order_broker() == "dhan":
            return None
    except Exception:
        pass
    creds = load_zerodha_order_creds(log=False)
    if not creds.ok:
        return None
    _last_kite_nifty_mono = now
    resp = httpx.get(
        "https://api.kite.trade/quote",
        params=[("i", "NSE:NIFTY 50")],
        headers={
            "X-Kite-Version": "3",
            "Authorization": f"token {creds.api_key}:{creds.access_token}",
        },
        timeout=8.0,
    )
    body = resp.json()
    if resp.status_code != 200 or body.get("status") != "success":
        logger.warning("Kite NIFTY quote failed: %s", (body.get("message") or resp.status_code))
        return None
    data = body.get("data") or {}
    row = data.get("NSE:NIFTY 50") or (next(iter(data.values()), {}) if data else {})
    last = (row or {}).get("last_price") if isinstance(row, dict) else None
    px = float(last) if last is not None else 0.0
    return px if px > 0 else None


def nifty_ltp() -> float | None:
    fresh = _cache_ltp_if_fresh(5.0)
    if fresh is not None:
        return fresh
    try:
        px = _kite_nifty_quote()
        if px is not None and px > 0:
            # Read-only fallback. Feeder keepalive writes the cache.
            return float(px)
    except Exception as exc:
        logger.warning("NIFTY kite fallback failed: %s", exc)
    try:
        from core.nifty_ltp_feed import read_nifty_ltp_cache

        snap = read_nifty_ltp_cache()
        if snap is not None and float(snap.ltp) > 0:
            return float(snap.ltp)
    except Exception as exc:
        logger.warning("nifty_ltp read failed: %s", exc)
    return None


def nifty_ltp_label() -> str:
    px = nifty_ltp()
    if px is None:
        return "—"
    return f"{px:,.2f}"


def round_nifty_50(px: float | None) -> int | None:
    if px is None:
        return None
    return int(round(float(px) / 50.0) * 50)


def _active_deployment() -> Path | None:
    try:
        from core.batman_mode import deployments_dir

        dep_dir = deployments_dir(require_runtime().root)
    except Exception:
        return None
    if not dep_dir.is_dir():
        return None
    files = sorted(
        [p for p in dep_dir.glob("batman_*.json") if p.parent.name != "archive"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _load_dep() -> dict[str, Any]:
    path = _active_deployment()
    if not path:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _persist_live_buffers_to_deployment(st: Any) -> None:
    """Keep Buffer Manager values across Kavach restart (restore reads this file)."""
    path = _active_deployment()
    if not path or st is None:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    from core.buffer_config.schema import (
        BufferKind,
        legacy_int_from_buffer,
        normalize_buffer_field,
        serialize_buffer_field,
    )

    ato = data.setdefault("ato", {})
    pairs = (
        ("ce_entry_buffer_points", "ce_entry_buffer", "ce_entry_buffer_points"),
        ("pe_entry_buffer_points", "pe_entry_buffer", "pe_entry_buffer_points"),
        ("ce_retrace_points", "ce_retrace_buffer", "ce_retrace_points"),
        ("pe_retrace_points", "pe_retrace_buffer", "pe_retrace_points"),
    )
    for state_key, buf_key, pts_key in pairs:
        raw = st.get(f"ato.{state_key}")
        ato[buf_key] = serialize_buffer_field(
            normalize_buffer_field(raw), BufferKind.CUSTOM
        )
        ato[pts_key] = legacy_int_from_buffer(raw)
    if ato.get("ce_protect_symbol"):
        data["retrace_points"] = ato.get("ce_retrace_points", data.get("retrace_points"))
    else:
        data["retrace_points"] = ato.get("pe_retrace_points", data.get("retrace_points"))
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def sell_strikes() -> tuple[int | None, int | None]:
    st = require_runtime().state
    if not st or not st.get("deployment.confirmed"):
        return None, None
    dep = _load_dep()
    positions = dep.get("positions") or {}

    def _strike(role: str, state_key: str) -> int | None:
        leg = positions.get(role) or (st.get(state_key) if st else None)
        if isinstance(leg, dict) and leg.get("strike"):
            try:
                return int(leg["strike"])
            except (TypeError, ValueError):
                return None
        return None

    return _strike("ce_sell", "positions.ce_sell"), _strike("pe_sell", "positions.pe_sell")


def buy_strikes() -> tuple[int | None, int | None]:
    st = require_runtime().state
    if not st or not st.get("deployment.confirmed"):
        return None, None
    dep = _load_dep()
    positions = dep.get("positions") or {}

    def _strike(role: str, state_key: str) -> int | None:
        leg = positions.get(role) or (st.get(state_key) if st else None)
        if isinstance(leg, dict) and leg.get("strike"):
            try:
                return int(leg["strike"])
            except (TypeError, ValueError):
                return None
        return None

    return _strike("ce_buy", "positions.ce_buy"), _strike("pe_buy", "positions.pe_buy")


def deployment_strategy() -> str:
    dep = _load_dep() or {}
    raw = str(dep.get("strategy") or "").strip().lower()
    if raw in ("directional", "batman"):
        return raw
    try:
        st = require_runtime().state
        raw2 = str((st.get("deployment.strategy") if st else "") or "").strip().lower()
        if raw2 in ("directional", "batman"):
            return raw2
    except Exception:
        pass
    return "batman"


def ato_anchor_strikes() -> tuple[int | None, int | None]:
    """Strike used for Auto ATO: Core Buy for Directional, Sell for Batman."""
    if deployment_strategy() == "directional":
        return buy_strikes()
    return sell_strikes()


def buffer_form() -> dict[str, Any]:
    """UI values: entry and retrace as absolute NIFTY levels (blank if dummy retrace 5)."""
    from core.buffer_config.levels import nifty_level_from_buffer
    from core.buffer_config.schema import normalize_buffer_field

    st = require_runtime().state
    if not st or not st.get("deployment.confirmed"):
        return {
            "ok": True,
            "ce_entry": "",
            "pe_entry": "",
            "ce_retrace": "",
            "pe_retrace": "",
            "ce_entry_placeholder": "Nifty Level",
            "pe_entry_placeholder": "Nifty Level",
            "ce_retrace_placeholder": "Nifty Level",
            "pe_retrace_placeholder": "Nifty Level",
            "ce_sell_strike": None,
            "pe_sell_strike": None,
            "ce_buy_strike": None,
            "pe_buy_strike": None,
            "strategy": "batman",
            "ce_ato_mode": "auto",
            "pe_ato_mode": "auto",
            "ce_ato_strike": "",
            "pe_ato_strike": "",
            "ce_ato_auto": None,
            "pe_ato_auto": None,
            "ce_protect_strike": None,
            "pe_protect_strike": None,
            "nifty_ltp": nifty_ltp(),
        }
    from core.position_scope import auto_protect_strike

    ce_strike, pe_strike = sell_strikes()
    ce_buy, pe_buy = buy_strikes()
    strat = deployment_strategy()
    ce_anchor, pe_anchor = ato_anchor_strikes()
    def _auto_for(side: str, anchor, buy, sell, prot_raw):
        if anchor is not None:
            return int(auto_protect_strike(int(anchor), side, ato_step=ATO_STEP))
        # Fallbacks so Auto always recommends a strike when any leg/protect exists.
        for src in (buy, sell):
            if src is not None:
                return int(auto_protect_strike(int(src), side, ato_step=ATO_STEP))
        try:
            if prot_raw not in (None, ""):
                return int(prot_raw)
        except (TypeError, ValueError):
            pass
        return None

    ce_prot = st.get("ato.ce_protect_strike")
    pe_prot = st.get("ato.pe_protect_strike")
    ce_auto = _auto_for("CE", ce_anchor, ce_buy, ce_strike, ce_prot)
    pe_auto = _auto_for("PE", pe_anchor, pe_buy, pe_strike, pe_prot)
    ce_mode = str(st.get("ato.ce_protect_strike_mode") or ( (_load_dep().get("ato") or {}).get("ce_protect_strike_mode") ) or "FIXED").upper()
    pe_mode = str(st.get("ato.pe_protect_strike_mode") or ( (_load_dep().get("ato") or {}).get("pe_protect_strike_mode") ) or "FIXED").upper()
    try:
        ce_prot_i = int(ce_prot) if ce_prot not in (None, "") else None
    except (TypeError, ValueError):
        ce_prot_i = None
    try:
        pe_prot_i = int(pe_prot) if pe_prot not in (None, "") else None
    except (TypeError, ValueError):
        pe_prot_i = None
    ce_entry_off = normalize_buffer_field(st.get("ato.ce_entry_buffer_points", 0) if st else 0)
    pe_entry_off = normalize_buffer_field(st.get("ato.pe_entry_buffer_points", 0) if st else 0)
    ce_ret = normalize_buffer_field(st.get("ato.ce_retrace_points", 5) if st else 5)
    pe_ret = normalize_buffer_field(st.get("ato.pe_retrace_points", 5) if st else 5)

    def _level(offset: Decimal, side: str, strike: int | None, kind: str, *, hide_sentinel: bool = False) -> str:
        if strike is None:
            return ""
        if hide_sentinel and int(offset) == RETRACE_DEFAULT_SENTINEL:
            return ""
        lvl = nifty_level_from_buffer(offset, side=side, kind=kind, sell_strike=strike)
        if not isinstance(lvl, Decimal):
            lvl = Decimal(str(lvl))
        return format(lvl.quantize(Decimal("1")), "f")

    return {
        "ok": True,
        "ce_entry": _level(ce_entry_off, "CE", ce_strike, "entry"),
        "pe_entry": _level(pe_entry_off, "PE", pe_strike, "entry"),
        "ce_retrace": _level(ce_ret, "CE", ce_strike, "exit", hide_sentinel=True),
        "pe_retrace": _level(pe_ret, "PE", pe_strike, "exit", hide_sentinel=True),
        "ce_entry_placeholder": "Nifty Level",
        "pe_entry_placeholder": "Nifty Level",
        "ce_retrace_placeholder": "Nifty Level",
        "pe_retrace_placeholder": "Nifty Level",
        "ce_sell_strike": ce_strike,
        "pe_sell_strike": pe_strike,
        "ce_buy_strike": ce_buy,
        "pe_buy_strike": pe_buy,
        "strategy": strat,
        "ce_ato_mode": "custom" if ce_mode == "CUSTOM" else "auto",
        "pe_ato_mode": "custom" if pe_mode == "CUSTOM" else "auto",
        "ce_ato_strike": "" if ce_mode != "CUSTOM" else ("" if ce_prot_i is None else str(ce_prot_i)),
        "pe_ato_strike": "" if pe_mode != "CUSTOM" else ("" if pe_prot_i is None else str(pe_prot_i)),
        "ce_ato_auto": ce_auto,
        "pe_ato_auto": pe_auto,
        "ce_protect_strike": ce_prot_i,
        "pe_protect_strike": pe_prot_i,
        "nifty_ltp": nifty_ltp(),
    }



def _persist_ato_protect_to_deployment(st: Any) -> None:
    path = _active_deployment()
    if not path or st is None:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    ato = data.setdefault("ato", {})
    for key in (
        "ce_protect_symbol",
        "ce_protect_strike",
        "ce_protect_strike_mode",
        "pe_protect_symbol",
        "pe_protect_strike",
        "pe_protect_strike_mode",
    ):
        val = st.get(f"ato.{key}")
        if val is not None:
            ato[key] = val
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def _ato_manager_side_active(side: str, st: Any, dep: dict[str, Any]) -> bool:
    """PE-only / CE-only Register — skip the inactive side in Buffer Manager protect refresh."""
    side = str(side or "").upper()
    scope = dict(dep.get("registration_scope") or {})
    if st is not None:
        scope = dict(st.get("deployment.registration_scope") or scope)
    enabled_key = f"{side.lower()}_enabled"
    if enabled_key in scope and not scope.get(enabled_key):
        return False
    manage = str(
        (st.get("ato.manage_sides") if st is not None else None)
        or dep.get("ato_manage_sides")
        or "both"
    ).strip().lower()
    if manage in ("pe", "ce") and manage != side.lower():
        return False
    return True


def _apply_ato_protect_from_payload(payload: dict[str, Any], st: Any) -> str | None:
    """Update CE/PE ATO protect strike from ATO Manager Auto/Custom controls."""
    from core.position_scope import auto_protect_strike, parse_and_validate_protect_strike
    from core.positions import build_ato_protect_symbol
    from core.nifty_option_expiry import parse_expiry_label

    if st is None:
        return "No state"
    dep = _load_dep() or {}
    positions = dep.get("positions") or {}
    ce_anchor, pe_anchor = ato_anchor_strikes()

    def _leg_expiry(leg: dict | None):
        if not isinstance(leg, dict):
            return None
        raw = str(leg.get("expiry") or "").strip()
        if not raw:
            scope = dep.get("registration_scope") or {}
            raw = str(scope.get("expiry") or "").strip()
        if not raw:
            cal = dep.get("calendar") or {}
            raw = str(cal.get("expiry_date") or "").strip()
        if not raw:
            return None
        try:
            return parse_expiry_label(raw[:10] if len(raw) >= 10 and raw[4] == "-" else raw)
        except Exception:
            try:
                return date.fromisoformat(raw[:10])
            except Exception:
                return None

    def _one(side: str, anchor: int | None) -> str | None:
        if not _ato_manager_side_active(side, st, dep):
            return None
        mode_raw = str(payload.get(f"{side.lower()}_ato_mode") or "").strip().lower()
        if mode_raw not in ("auto", "custom"):
            return None  # field not sent — skip
        if mode_raw == "custom":
            value, err = parse_and_validate_protect_strike(str(payload.get(f"{side.lower()}_ato_strike") or ""))
            if err:
                return err
            prot = int(value)
            prot_mode = "CUSTOM"
        else:
            if anchor is None:
                return f"{side} Auto ATO needs a registered Core Buy/Sell strike."
            prot = int(auto_protect_strike(int(anchor), side, ato_step=ATO_STEP))
            prot_mode = "FIXED"
        sell_leg = positions.get(f"{side.lower()}_sell") or st.get(f"positions.{side.lower()}_sell")
        buy_leg = positions.get(f"{side.lower()}_buy") or st.get(f"positions.{side.lower()}_buy")
        template = sell_leg if isinstance(sell_leg, dict) else (buy_leg if isinstance(buy_leg, dict) else None)
        if not template or not template.get("symbol"):
            return f"{side} protect update needs a registered leg symbol."
        try:
            sym = build_ato_protect_symbol(
                str(template.get("symbol") or ""),
                int(prot),
                side,
                expiry=_leg_expiry(template if isinstance(template, dict) else None),
            )
        except Exception as exc:
            return f"{side} protect symbol failed: {exc}"
        st.set(f"ato.{side.lower()}_protect_strike", int(prot), save=False)
        st.set(f"ato.{side.lower()}_protect_strike_mode", prot_mode, save=False)
        st.set(f"ato.{side.lower()}_protect_symbol", sym, save=False)
        return None

    err = _one("CE", ce_anchor)
    if err:
        return err
    err = _one("PE", pe_anchor)
    if err:
        return err
    try:
        st.save()
    except Exception:
        pass
    try:
        _persist_ato_protect_to_deployment(st)
    except Exception:
        pass
    return None


def buffer_save(payload: dict[str, Any]) -> dict[str, Any]:
    from core.buffer_config.levels import buffer_from_nifty_level, looks_like_nifty_level
    from core.buffer_config.parser import parse_buffer_text

    st = require_runtime().state
    if not st:
        return {"ok": False, "error": "No state"}
    ce_strike, pe_strike = sell_strikes()

    def _parse_level(raw: Any, side: str, strike: int | None, *, kind: str, label: str) -> Decimal | None:
        text = str(raw or "").strip().replace(",", "")
        if not text:
            return None
        try:
            value = parse_buffer_text(text)
        except ValueError:
            raise ValueError(f"{label} must be a NIFTY level (e.g. 24200).") from None
        if not looks_like_nifty_level(value):
            raise ValueError(f"{label} must be a NIFTY level (e.g. 24200), not points.")
        if strike is None:
            raise ValueError(f"{label} needs a sell strike — Register Batman first.")
        return buffer_from_nifty_level(value, side=side, kind=kind, sell_strike=strike)

    try:
        ce_off = _parse_level(payload.get("ce_entry"), "CE", ce_strike, kind="entry", label="CE entry")
        pe_off = _parse_level(payload.get("pe_entry"), "PE", pe_strike, kind="entry", label="PE entry")
        ce_pts = _parse_level(payload.get("ce_retrace"), "CE", ce_strike, kind="exit", label="CE retrace")
        pe_pts = _parse_level(payload.get("pe_retrace"), "PE", pe_strike, kind="exit", label="PE retrace")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    # Thin safety: reject illegal same-side exit vs entry (desk UI also disables SAVE).
    from core.buffer_config.levels import nifty_level_from_buffer

    def _abs_level(offset, side: str, kind: str, strike: int | None):
        if offset is None or strike is None:
            return None
        return nifty_level_from_buffer(offset, side=side, kind=kind, sell_strike=strike)

    ce_entry_lvl = _abs_level(ce_off, "CE", "entry", ce_strike)
    ce_exit_lvl = _abs_level(ce_pts, "CE", "exit", ce_strike)
    pe_entry_lvl = _abs_level(pe_off, "PE", "entry", pe_strike)
    pe_exit_lvl = _abs_level(pe_pts, "PE", "exit", pe_strike)
    if ce_entry_lvl is not None and ce_exit_lvl is not None and not (ce_exit_lvl < ce_entry_lvl):
        return {"ok": False, "error": f"CE exit must be below CE entry (entry {ce_entry_lvl})."}
    if pe_entry_lvl is not None and pe_exit_lvl is not None and not (pe_exit_lvl > pe_entry_lvl):
        return {"ok": False, "error": f"PE exit must be above PE entry (entry {pe_entry_lvl})."}

    if ce_off is not None:
        st.set("ato.ce_entry_buffer_points", ce_off)
    if pe_off is not None:
        st.set("ato.pe_entry_buffer_points", pe_off)
    if ce_pts is not None:
        st.set("ato.ce_retrace_points", ce_pts)
    if pe_pts is not None:
        st.set("ato.pe_retrace_points", pe_pts)
    try:
        _persist_live_buffers_to_deployment(st)
    except Exception:
        pass
    try:
        st.save()
    except Exception:
        pass
    if any(k in payload for k in ("ce_ato_mode", "pe_ato_mode", "ce_ato_strike", "pe_ato_strike")):
        perr = _apply_ato_protect_from_payload(payload, st)
        if perr:
            return {"ok": False, "error": perr}
    try:
        out = buffer_form()
    except Exception as exc:
        logger.warning("buffer_form after save failed: %s", exc)
        out = {"ok": True}
    out["ok"] = True
    out["text"] = "Buffer saved."
    return out


def poll_form() -> dict[str, Any]:
    st = require_runtime().state
    raw = st.get("ato.poll_interval_seconds") if st else None
    try:
        cur = int(raw) if raw is not None else 2
    except (TypeError, ValueError):
        cur = 2
    if cur not in [0, 1, 2, 3, 4, 5, 10, 15]:
        cur = 2
    return {
        "ok": True,
        "poll_interval": cur,
        "poll_options": [0, 1, 2, 3, 4, 5, 10, 15],
        "default": 2,
    }


def poll_save(payload: dict[str, Any]) -> dict[str, Any]:
    st = require_runtime().state
    if not st:
        return {"ok": False, "error": "No state"}
    raw = payload.get("poll_interval", 2)
    try:
        poll = 2 if raw is None or raw == "" else int(raw)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Poll interval must be a number of seconds."}
    if poll not in (0, 1, 2, 3, 4, 5, 10, 15):
        return {"ok": False, "error": "Poll interval must be 0, 1, 2, 3, 4, 5, 10, or 15 seconds."}
    st.set("ato.poll_interval_seconds", poll)
    path = _active_deployment()
    if path:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("ato", {})["poll_interval_seconds"] = poll
            path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
        except Exception:
            pass
    out = poll_form()
    out["text"] = f"Poll interval saved: {poll}s"
    return out


def ato_combined() -> dict[str, Any]:
    rt = require_runtime()
    st = rt.state
    dep = _active_deployment()
    status_lines = []
    if not dep:
        msg = "No active deployment. Use Register Batman to arm Kavach."
        return {"ok": True, "status_text": msg, "positions_text": "", "text": msg}
    if st:
        status_lines += [
            f"CE protect : {st.get('ato.ce_protect_symbol') or 'N/A'}",
            f"PE protect : {st.get('ato.pe_protect_symbol') or 'N/A'}",
            f"Manage     : {st.get('ato.manage_sides', 'both')}",
        ]
    status_text = "\n".join(status_lines)
    return {
        "ok": True,
        "status_text": status_text,
        "positions_text": "",
        "text": status_text,
    }


def _lot_size() -> int:
    rt = require_runtime()
    broker = getattr(rt, "broker", None)
    broker_lot = None
    if broker:
        try:
            broker_lot = int(broker.get_lot_size("NIFTY"))
        except Exception:
            broker_lot = None
    try:
        from core.position_scope import resolve_nifty_lot_size

        return resolve_nifty_lot_size(config_lot_size=LOT_SIZE, broker_lot_size=broker_lot)
    except Exception:
        return LOT_SIZE


def _positions_from_uat_fixture() -> list[dict[str, Any]]:
    try:
        from core.batman_mode import workspace_root
        from core.uat_positions import load_positions_fixture
        from bat_telegram.bots.kavach2.strategy.multileg_entry import resolve_expiry, resolve_symbol
    except Exception as exc:
        logger.warning("uat fixture import failed: %s", exc)
        return []
    try:
        fx = load_positions_fixture(workspace_root())
    except Exception as exc:
        logger.warning("uat fixture load failed: %s", exc)
        return []
    if not isinstance(fx, dict):
        return []
    try:
        exp = resolve_expiry()
        exp_s = exp.isoformat()
    except Exception:
        exp = None
        exp_s = ""
    out: list[dict[str, Any]] = []
    lot_size = _lot_size()
    for leg in fx.get("legs") or []:
        opt = str(leg.get("type") or "").upper()
        side = str(leg.get("side") or "").upper()
        if opt not in ("CE", "PE") or side not in ("BUY", "SELL"):
            continue
        try:
            strike = int(leg.get("strike") or 0)
            lots = max(1, int(leg.get("lots") or 1))
        except (TypeError, ValueError):
            continue
        qty = lots * lot_size
        symbol = ""
        try:
            symbol, lot = resolve_symbol(strike, opt, exp)
            lot_size = int(lot or lot_size)
            qty = lots * lot_size
        except Exception:
            symbol = f"NIFTY-{strike}-{opt}"
        out.append(
            {
                "symbol": symbol,
                "display_symbol": symbol,
                "strike": strike,
                "opt_type": opt,
                "direction": "LONG" if side == "BUY" else "SHORT",
                "qty": qty,
                "avg_price": float(leg.get("avg_price") or 0),
                "instrument_token": "",
                "expiry": exp_s,
            }
        )
    return out


def _load_register_book() -> tuple[list[dict[str, Any]], str]:
    """Broker NIFTY options first; UAT/paper fixture if the book is empty."""
    note = ""
    positions: list[dict[str, Any]] = []
    rt = require_runtime()
    from core.broker_resolve import resolve_kavach_broker

    broker = resolve_kavach_broker(root=getattr(rt, "root", None), runtime_broker=getattr(rt, "broker", None))
    if broker is not None and getattr(rt, "broker", None) is None:
        rt.broker = broker
    if broker:
        try:
            refresh = getattr(broker, "refresh_fixture_positions", None)
            if callable(refresh):
                refresh()
            try:
                from core.order_broker_select import get_main_order_broker
                from core.zerodha_credentials import sync_live_zerodha_token

                house = get_main_order_broker(getattr(rt, "root", None))
                # Zerodha token sync only when Kite is the order house (not Shoonya).
                if house == "zerodha":
                    sync_live_zerodha_token(broker, root=getattr(rt, "root", None))
            except Exception:
                pass
            from core.positions import filter_nifty_positions

            df = broker.get_positions()
            positions = filter_nifty_positions(df)
        except Exception as exc:
            logger.warning("register book broker failed: %s", exc)
            note = f"Broker book unavailable ({exc})."
    if not positions:
        from core.batman_mode import is_uat

        root = getattr(rt, "root", None)
        if is_uat(root):
            fixture = _positions_from_uat_fixture()
            if fixture:
                positions = fixture
                note = (note + " Using UAT/paper position book.").strip()
            elif not note:
                note = "No open NIFTY option legs in the book. Deploy Batman first, then Register."
        elif not note:
            try:
                from core.order_broker_select import get_main_order_broker

                house = get_main_order_broker(root) or "the broker"
            except Exception:
                house = "the broker"
            names = {"dhan": "Dhan", "zerodha": "Zerodha", "shoonya": "Shoonya"}
            note = f"No open NIFTY option legs on {names.get(house, house)}. Open the book, then Register."
    return positions, note


def _book_abs_qty_by_symbol(book: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """Absolute net qty per option symbol from the register book."""
    if book is None:
        book, _ = _load_register_book()
    out: dict[str, int] = {}
    for pos in book or []:
        sym = str(pos.get("symbol") or "").strip()
        if not sym:
            continue
        out[sym] = abs(int(pos.get("qty") or 0))
    return out


def _verify_directional_fills_in_book(
    placed_legs: list[dict[str, Any]],
    snapshot: dict[str, int],
    *,
    timeout_sec: float = 60.0,
    poll_sec: float = 2.0,
) -> tuple[bool, str | None]:
    """Wait until broker book shows each filled punch's qty added on top of snapshot."""
    need: list[tuple[str, int, str, str]] = []
    for leg in placed_legs or []:
        if str(leg.get("status") or "").lower() != "filled":
            continue
        sym = str(leg.get("symbol") or "").strip()
        qty = abs(int(leg.get("qty") or 0))
        if not sym or qty <= 0:
            continue
        side = str(leg.get("side") or "").upper()
        want_dir = "LONG" if side == "BUY" else ("SHORT" if side == "SELL" else "")
        need.append((sym, qty, want_dir, str(leg.get("key") or sym)))
    if not need:
        return False, "No filled legs to verify in the broker book."

    deadline = time.monotonic() + max(1.0, float(timeout_sec))
    last_err: str | None = None
    while True:
        book, note = _load_register_book()
        by_sym = {str(p.get("symbol") or "").strip(): p for p in (book or [])}
        short: list[str] = []
        for sym, punch_qty, want_dir, key in need:
            pos = by_sym.get(sym)
            if not pos:
                short.append(f"{key}({sym}): missing on broker")
                continue
            cur = abs(int(pos.get("qty") or 0))
            prev = abs(int((snapshot or {}).get(sym) or 0))
            if cur < prev + punch_qty:
                short.append(f"{key}({sym}): book={cur} need>={prev + punch_qty}")
                continue
            if want_dir and str(pos.get("direction") or "") != want_dir:
                short.append(f"{key}({sym}): direction {pos.get('direction')} want {want_dir}")
        if not short:
            return True, None
        last_err = "Broker book not ready after deploy: " + "; ".join(short)
        if note:
            last_err = last_err + f" ({note})"
        if time.monotonic() >= deadline:
            return False, last_err
        time.sleep(max(0.5, float(poll_sec)))


def _public_leg(pos: dict[str, Any], lot_size: int) -> dict[str, Any]:
    from core.position_scope import qty_to_lots

    qty = abs(int(pos.get("qty") or 0))
    lots = qty_to_lots(qty, lot_size)
    symbol = str(pos.get("symbol") or "")
    display = str(pos.get("display_symbol") or symbol)
    avg = pos.get("avg_price") or 0
    try:
        avg_s = f"{float(avg):.2f}"
    except (TypeError, ValueError):
        avg_s = "—"
    label = f"{display} | {pos.get('direction')} {qty} qty ({lots} lots) | avg ₹{avg_s}"
    return {
        "symbol": symbol,
        "strike": int(pos.get("strike") or 0),
        "opt_type": str(pos.get("opt_type") or pos.get("option_type") or ""),
        "direction": str(pos.get("direction") or ""),
        "qty": qty,
        "lots": lots,
        "avg_price": avg,
        "label": label,
        "expiry": pos.get("expiry") or "",
    }


def _find_book_leg(book: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    want = str(symbol or "").strip()
    if not want:
        return None
    for pos in book:
        if str(pos.get("symbol") or "") == want:
            return pos
    return None


def _store_leg(pos: dict[str, Any], lot_size: int) -> dict[str, Any]:
    opt = str(pos.get("opt_type") or pos.get("option_type") or "")
    direction = str(pos.get("direction") or "")
    txn = "BUY" if direction == "LONG" else "SELL"
    return {
        "symbol": pos["symbol"],
        "display_symbol": pos.get("display_symbol") or pos["symbol"],
        "strike": int(pos["strike"]),
        "qty": abs(int(pos.get("qty") or 0)),
        "avg_price": pos.get("avg_price") or 0,
        "option_type": opt,
        "opt_type": opt,
        "direction": direction,
        "transaction_type": txn,
        "lot_size": int(lot_size),
        "expiry": pos.get("expiry") or "",
        "instrument_token": str(pos.get("instrument_token") or ""),
    }


def _archive_active_web() -> list[str]:
    from core.batman_mode import deployments_dir

    dep_dir = deployments_dir(require_runtime().root)
    archive = dep_dir / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for f in sorted(dep_dir.glob("batman_*.json")):
        dest = archive / f.name
        shutil.move(str(f), str(dest))
        moved.append(f.name)
    return moved


def _nifty_level_offset(raw: Any, side: str, kind: str, strike: int | None) -> tuple[Any, str | None]:
    from core.buffer_config.levels import parse_and_validate_user_buffer_or_level

    text = str(raw or "").strip()
    if not text:
        return None, f"{side} {kind} needs a NIFTY level (e.g. 24160)."
    value, err, _mode = parse_and_validate_user_buffer_or_level(
        text, side=side, kind=kind, sell_strike=strike
    )
    if err:
        return None, err
    return value, None



def _default_order_mode() -> str:
    """Prod is live Zerodha; UAT/dev stay paper unless state already says live."""
    try:
        from core.batman_mode import is_prod
        from core.order_mode import order_mode_from_state

        rt = require_runtime()
        default = "live" if is_prod(getattr(rt, "root", None)) else "paper"
        return order_mode_from_state(getattr(rt, "state", None), default=default)
    except Exception:
        return "paper"

def register_defaults(expiry: str | None = None) -> dict[str, Any]:
    from core.position_scope import auto_protect_strike, filter_positions_by_direction, filter_positions_by_side
    from core.wizard_plan import max_wizard_question_count

    from core.nifty_option_expiry import (
        choose_default_register_expiry,
        filter_positions_for_expiry,
        list_register_week_expiries,
    )

    px = nifty_ltp()
    lot_size = _lot_size()
    book, note = _load_register_book()
    expiry_options = list_register_week_expiries()
    expiry_pub = [{"id": o["id"], "iso": o["iso"], "label": o["label"]} for o in expiry_options]
    chosen = str(expiry or "").strip()
    valid = {o["iso"] for o in expiry_pub}
    if chosen not in valid:
        chosen = choose_default_register_expiry(book, expiry_options)
    exp_d = date.fromisoformat(chosen) if chosen else None
    expiry_label = next((o["label"] for o in expiry_pub if o["iso"] == chosen), chosen)
    if exp_d is not None:
        book = filter_positions_for_expiry(book, exp_d)
        if not book:
            extra = f"No open NIFTY option legs for {expiry_label or chosen}."
            note = extra if not note else f"{note} {extra}"
    pe_long = [_public_leg(p, lot_size) for p in filter_positions_by_direction(filter_positions_by_side(book, "PE"), "LONG")]
    pe_short = [_public_leg(p, lot_size) for p in filter_positions_by_direction(filter_positions_by_side(book, "PE"), "SHORT")]
    ce_long = [_public_leg(p, lot_size) for p in filter_positions_by_direction(filter_positions_by_side(book, "CE"), "LONG")]
    ce_short = [_public_leg(p, lot_size) for p in filter_positions_by_direction(filter_positions_by_side(book, "CE"), "SHORT")]
    book_pe = bool(pe_long or pe_short)
    book_ce = bool(ce_long or ce_short)
    if book_pe and book_ce:
        scope = "both"
    elif book_pe:
        scope = "pe"
    elif book_ce:
        scope = "ce"
    else:
        scope = "both"
    questions = [
        {"id": "order_mode", "n": 1, "section": "Shared", "label": "Paper or Live trade"},
        {"id": "reg_expiry", "n": 2, "section": "Shared", "label": "Expiry week"},
        {"id": "reg_scope", "n": 3, "section": "Shared", "label": "Register CE/PE"},
        {"id": "pe_buy", "n": 3, "section": "PE", "label": "Select Core PE BUY leg"},
        {"id": "pe_margin_hedge", "n": 4, "section": "PE", "label": "Select Margin Hedge"},
        {"id": "pe_dyn_hedge", "n": 5, "section": "PE", "label": "35% Dynamic Hedge"},
        {"id": "pe_sell", "n": 6, "section": "PE", "label": "Select PE SELL leg"},
        {"id": "pe_ato_strike", "n": 7, "section": "PE", "label": "ATO strike"},
        {"id": "pe_entry", "n": 8, "section": "PE", "label": "Entry NIFTY level"},
        {"id": "pe_exit", "n": 9, "section": "PE", "label": "Exit NIFTY level (retrace)"},
        {"id": "ce_buy", "n": 10, "section": "CE", "label": "Select Core CE BUY leg"},
        {"id": "ce_margin_hedge", "n": 11, "section": "CE", "label": "Select Margin Hedge"},
        {"id": "ce_dyn_hedge", "n": 12, "section": "CE", "label": "35% Dynamic Hedge"},
        {"id": "ce_sell", "n": 13, "section": "CE", "label": "Select CE SELL leg"},
        {"id": "ce_ato_strike", "n": 14, "section": "CE", "label": "ATO strike"},
        {"id": "ce_entry", "n": 15, "section": "CE", "label": "Entry NIFTY level"},
        {"id": "ce_exit", "n": 16, "section": "CE", "label": "Exit NIFTY level (retrace)"},
        {"id": "ato_mon", "n": 17, "section": "Shared", "label": "ATO manage CE/PE"},
        {"id": "confirm", "n": 18, "section": "Shared", "label": "Confirm deployment"},
    ]
    return {
        "ok": True,
        "question_count": max_wizard_question_count(),
        "questions": questions,
        "order_mode": _default_order_mode(),
        "expiry": chosen,
        "expiry_label": expiry_label,
        "expiry_options": expiry_pub,
        "reg_scope": scope,
        "nifty_ltp": px,
        "lot_size": lot_size,
        "ato_step": ATO_STEP,
        "poll_options": [0, 1, 2, 3, 4, 5, 10, 15],
        "poll_interval": 2,
        "ato_mon": "both" if book_pe and book_ce else ("pe" if book_pe else "ce"),
        "pe_long": pe_long,
        "pe_short": pe_short,
        "ce_long": ce_long,
        "ce_short": ce_short,
        "book_has_pe": book_pe,
        "book_has_ce": book_ce,
        "book_note": note,
        "auto_protect_hint": {
            "pe": auto_protect_strike(int(pe_short[0]["strike"]), "PE", ato_step=ATO_STEP) if pe_short else None,
            "ce": auto_protect_strike(int(ce_short[0]["strike"]), "CE", ato_step=ATO_STEP) if ce_short else None,
        },
        "deployed": bool(_active_deployment()),
        "file": _active_deployment().name if _active_deployment() else "",
        "entry_placeholder": "NIFTY level (e.g. 24160)",
        "exit_placeholder": "NIFTY level (e.g. 24200)",
    }


def register_batman(payload: dict[str, Any]) -> dict[str, Any]:
    from core.batman_mode import deployments_dir
    from core.buffer_config.schema import (
        BufferKind,
        legacy_int_from_buffer,
        normalize_buffer_field,
        serialize_buffer_field,
    )
    from core.deployment_lock import deployment_session
    from core.order_mode import normalize_order_mode
    from core.position_scope import (
        auto_protect_strike,
        build_registration_scope,
        parse_and_validate_protect_strike,
        qty_to_lots,
        suggested_ato_lots,
    )
    from core.positions import build_ato_protect_symbol

    if not payload.get("confirm"):
        return {"ok": False, "error": "Confirm Register to arm Kavach."}

    rt = require_runtime()
    from core.nifty_option_expiry import (
        filter_positions_for_expiry,
        list_register_week_expiries,
        symbol_matches_register_expiry,
    )

    lot_size = _lot_size()
    book, _note = _load_register_book()
    if not book:
        return {"ok": False, "error": "No NIFTY option legs in the book. Deploy Batman first, then Register."}

    expiry_raw = str(payload.get("expiry") or "").strip()
    if not expiry_raw:
        return {"ok": False, "error": "Pick an expiry week before Register."}
    try:
        exp_d = date.fromisoformat(expiry_raw)
    except ValueError:
        return {"ok": False, "error": "Expiry week is not a valid date."}
    expiry_options = list_register_week_expiries()
    expiry_label = next((str(o["label"]) for o in expiry_options if o["iso"] == expiry_raw), expiry_raw)
    if expiry_raw not in {o["iso"] for o in expiry_options}:
        return {"ok": False, "error": f"Expiry {expiry_raw} is not in this week / next week / week after."}
    book = filter_positions_for_expiry(book, exp_d)
    if not book:
        return {"ok": False, "error": f"No NIFTY option legs for {expiry_label}."}

    order_mode = str(payload.get("order_mode") or _default_order_mode()).strip().lower()
    if order_mode not in ("paper", "live"):
        order_mode = _default_order_mode()

    scope_choice = str(payload.get("reg_scope") or "both").strip().lower()
    if scope_choice not in ("both", "ce", "pe"):
        return {"ok": False, "error": "Register CE/PE must be both, CE only, or PE only."}
    pe_on = scope_choice in ("both", "pe")
    ce_on = scope_choice in ("both", "ce")

    selected: dict[str, Any] = {
        "pe_buy": None,
        "pe_margin_hedge": None,
        "pe_dyn_hedge": None,
        "pe_sell": None,
        "ce_buy": None,
        "ce_margin_hedge": None,
        "ce_dyn_hedge": None,
        "ce_sell": None,
    }

    def _role(name: str, required: bool) -> tuple[dict[str, Any] | None, str | None]:
        raw = str(payload.get(name) or "").strip()
        if not raw:
            if required:
                return None, f"{name} is required."
            return None, None
        pos = _find_book_leg(book, raw)
        if not pos:
            return None, f"{name} is not in the current book."
        return _store_leg(pos, lot_size), None

    if pe_on:
        for name, required in (
            ("pe_buy", True),
            ("pe_margin_hedge", False),
            ("pe_dyn_hedge", False),
            ("pe_sell", True),
        ):
            leg, err = _role(name, required)
            if err:
                return {"ok": False, "error": err}
            selected[name] = leg
        if selected["pe_buy"]["direction"] != "LONG":
            return {"ok": False, "error": "PE BUY must be a LONG leg."}
        if selected["pe_sell"]["direction"] != "SHORT":
            return {"ok": False, "error": "PE SELL must be a SHORT leg."}

    if ce_on:
        for name, required in (
            ("ce_buy", True),
            ("ce_margin_hedge", False),
            ("ce_dyn_hedge", False),
            ("ce_sell", True),
        ):
            leg, err = _role(name, required)
            if err:
                return {"ok": False, "error": err}
            selected[name] = leg
        if selected["ce_buy"]["direction"] != "LONG":
            return {"ok": False, "error": "CE BUY must be a LONG leg."}
        if selected["ce_sell"]["direction"] != "SHORT":
            return {"ok": False, "error": "CE SELL must be a SHORT leg."}

    used = [leg["symbol"] for leg in selected.values() if leg]
    if len(set(used)) != len(used):
        return {"ok": False, "error": "Each leg must be a different contract."}
    for leg in selected.values():
        if not leg:
            continue
        sym = str(leg.get("symbol") or "")
        if not symbol_matches_register_expiry(sym, exp_d):
            return {
                "ok": False,
                "error": f"{sym} is not the selected week ({expiry_label}).",
            }

    pe_sell_strike = int(selected["pe_sell"]["strike"]) if selected["pe_sell"] else None
    ce_sell_strike = int(selected["ce_sell"]["strike"]) if selected["ce_sell"] else None
    pe_buy_strike = int(selected["pe_buy"]["strike"]) if selected.get("pe_buy") else None
    ce_buy_strike = int(selected["ce_buy"]["strike"]) if selected.get("ce_buy") else None
    is_dir = _is_directional_payload(payload)

    def _protect(side: str, anchor_strike: int | None):
        if anchor_strike is None:
            return None, "FIXED", None
        mode = str(payload.get(f"{side.lower()}_ato_mode") or "auto").strip().lower()
        if mode == "custom":
            value, err = parse_and_validate_protect_strike(str(payload.get(f"{side.lower()}_ato_strike") or ""))
            if err:
                return None, "CUSTOM", err
            return value, "CUSTOM", None
        return auto_protect_strike(anchor_strike, side, ato_step=ATO_STEP), "FIXED", None

    pe_prot = ce_prot = None
    pe_prot_mode = ce_prot_mode = "FIXED"
    if pe_on:
        pe_prot, pe_prot_mode, perr = _protect("PE", pe_buy_strike if is_dir else pe_sell_strike)
        if perr:
            return {"ok": False, "error": perr}
    if ce_on:
        ce_prot, ce_prot_mode, cerr = _protect("CE", ce_buy_strike if is_dir else ce_sell_strike)
        if cerr:
            return {"ok": False, "error": cerr}

    # Directional defaults: entry at ATO strike, exit 10 pts (CE below / PE above).
    pe_entry_raw = payload.get("pe_entry")
    pe_exit_raw = payload.get("pe_exit")
    ce_entry_raw = payload.get("ce_entry")
    ce_exit_raw = payload.get("ce_exit")
    if is_dir:
        if pe_on and pe_prot is not None and not str(pe_entry_raw or "").strip():
            pe_entry_raw = str(int(pe_prot))
        if pe_on and pe_prot is not None and not str(pe_exit_raw or "").strip():
            pe_exit_raw = str(int(pe_prot) + 10)
        if ce_on and ce_prot is not None and not str(ce_entry_raw or "").strip():
            ce_entry_raw = str(int(ce_prot))
        if ce_on and ce_prot is not None and not str(ce_exit_raw or "").strip():
            ce_exit_raw = str(int(ce_prot) - 10)

    pe_entry = pe_exit = ce_entry = ce_exit = 0
    if pe_on:
        pe_entry, err = _nifty_level_offset(pe_entry_raw, "PE", "entry", pe_sell_strike)
        if err:
            return {"ok": False, "error": err}
        pe_exit, err = _nifty_level_offset(pe_exit_raw, "PE", "exit", pe_sell_strike)
        if err:
            return {"ok": False, "error": err}
    if ce_on:
        ce_entry, err = _nifty_level_offset(ce_entry_raw, "CE", "entry", ce_sell_strike)
        if err:
            return {"ok": False, "error": err}
        ce_exit, err = _nifty_level_offset(ce_exit_raw, "CE", "exit", ce_sell_strike)
        if err:
            return {"ok": False, "error": err}

    raw = payload.get("poll_interval", 2)
    try:
        poll = 2 if raw is None or raw == "" else int(raw)
    except (TypeError, ValueError):
        poll = 2
    if poll not in (0, 1, 2, 3, 4, 5, 10, 15):
        poll = 2

    ato_mon = str(payload.get("ato_mon") or "both").strip().lower()
    if pe_on and not ce_on:
        ato_mon = "pe"
    elif ce_on and not pe_on:
        ato_mon = "ce"
    elif ato_mon not in ("both", "ce", "pe"):
        ato_mon = "both"
    if ato_mon == "pe" and not pe_on:
        return {"ok": False, "error": "ATO cannot manage PE — PE was not registered."}
    if ato_mon == "ce" and not ce_on:
        return {"ok": False, "error": "ATO cannot manage CE — CE was not registered."}

    pe_managed = qty_to_lots(int(selected["pe_buy"]["qty"]), lot_size) if selected["pe_buy"] else None
    ce_managed = qty_to_lots(int(selected["ce_buy"]["qty"]), lot_size) if selected["ce_buy"] else None
    pe_ato_lots = (
        suggested_ato_lots(pe_managed, int(selected["pe_buy"]["qty"]), lot_size=lot_size)
        if selected["pe_buy"]
        else None
    )
    ce_ato_lots = (
        suggested_ato_lots(ce_managed, int(selected["ce_buy"]["qty"]), lot_size=lot_size)
        if selected["ce_buy"]
        else None
    )

    # Subscription gate — Directional uses tracker Dir caps; Batman uses Batman caps.
    from subscription.enforcement import (
        check_entry_allowed_for_strategy,
        register_requested_limits_from_legs,
    )

    pe_sell_lots_sub = qty_to_lots(int(selected["pe_sell"]["qty"]), lot_size) if selected.get("pe_sell") else 0
    ce_sell_lots_sub = qty_to_lots(int(selected["ce_sell"]["qty"]), lot_size) if selected.get("ce_sell") else 0
    req_sell, req_ato = register_requested_limits_from_legs(
        pe_sell_lots=pe_sell_lots_sub,
        ce_sell_lots=ce_sell_lots_sub,
        pe_ato_lots=int(pe_ato_lots or 0),
        ce_ato_lots=int(ce_ato_lots or 0),
    )
    sub_gate = check_entry_allowed_for_strategy(
        strategy="directional" if is_dir else "batman",
        requested_sell_lots=req_sell,
        requested_ato_lots=req_ato,
        action="register",
    )
    if not sub_gate.get("ok"):
        return {
            "ok": False,
            "error": str(sub_gate.get("error") or "Subscription blocked."),
            "subscription": sub_gate,
        }


    scope = build_registration_scope(
        pe_enabled=pe_on,
        ce_enabled=ce_on,
        pe_managed_lots=pe_managed,
        ce_managed_lots=ce_managed,
        pe_ato_lots=pe_ato_lots,
        ce_ato_lots=ce_ato_lots,
        lot_size=lot_size,
    )
    scope["expiry"] = expiry_raw
    scope["expiry_label"] = expiry_label

    ce_entry_dec = normalize_buffer_field(ce_entry)
    pe_entry_dec = normalize_buffer_field(pe_entry)
    ce_retrace_dec = normalize_buffer_field(ce_exit)
    pe_retrace_dec = normalize_buffer_field(pe_exit)

    pe_ato_sym = ce_ato_sym = None

    def _leg_expiry(leg: dict | None):
        if not isinstance(leg, dict):
            return None
        raw = str(leg.get("expiry") or "").strip() or str(expiry_raw or "").strip()
        if not raw:
            return None
        try:
            from core.nifty_option_expiry import parse_expiry_label

            return parse_expiry_label(raw[:10] if len(raw) >= 10 and raw[4] == "-" else raw)
        except Exception:
            try:
                from datetime import date as _date

                return _date.fromisoformat(raw[:10])
            except Exception:
                return None

    if selected["pe_sell"] and pe_prot:
        pe_ato_sym = build_ato_protect_symbol(
            selected["pe_sell"]["symbol"],
            int(pe_prot),
            "PE",
            expiry=_leg_expiry(selected["pe_sell"]),
        )
    if selected["ce_sell"] and ce_prot:
        ce_ato_sym = build_ato_protect_symbol(
            selected["ce_sell"]["symbol"],
            int(ce_prot),
            "CE",
            expiry=_leg_expiry(selected["ce_sell"]),
        )

    now = datetime.now()
    filename = f"batman_{now.strftime('%Y-%m-%d_%H-%M')}.json"
    dep_dir = deployments_dir(rt.root)
    dep_dir.mkdir(parents=True, exist_ok=True)
    filepath = dep_dir / filename

    data: dict[str, Any] = {
        "schema_version": 1.1,
        "strategy": "directional" if is_dir else "batman",
        "order_mode": normalize_order_mode(order_mode),
        "deployed_at": now.astimezone().isoformat(),
        "registered_at": now.strftime("%A %d-%b-%Y at %H:%M"),
        "file_name": filename,
        "source": "kavach_web",
        "retrace_points": int(legacy_int_from_buffer(ce_exit if ce_on else pe_exit, default=0)),
        "ato_manage_sides": ato_mon,
        "registration_scope": scope,
        "positions": selected,
        "ato": {
            "pe_protect_symbol": pe_ato_sym,
            "pe_protect_strike": pe_prot,
            "pe_protect_strike_mode": pe_prot_mode if pe_on else None,
            "ce_protect_symbol": ce_ato_sym,
            "ce_protect_strike": ce_prot,
            "ce_protect_strike_mode": ce_prot_mode if ce_on else None,
            "ato_step": ATO_STEP,
            "ce_entry_buffer": serialize_buffer_field(ce_entry_dec, BufferKind.CUSTOM),
            "pe_entry_buffer": serialize_buffer_field(pe_entry_dec, BufferKind.CUSTOM),
            "ce_retrace_buffer": serialize_buffer_field(ce_retrace_dec, BufferKind.CUSTOM),
            "pe_retrace_buffer": serialize_buffer_field(pe_retrace_dec, BufferKind.CUSTOM),
            "ce_entry_buffer_points": legacy_int_from_buffer(ce_entry),
            "pe_entry_buffer_points": legacy_int_from_buffer(pe_entry),
            "ce_retrace_points": legacy_int_from_buffer(ce_exit),
            "pe_retrace_points": legacy_int_from_buffer(pe_exit),
            "poll_interval_seconds": poll,
        },
        "status": "armed",
    }
    # Overnight / Hedge Box DTE source (Ratripal). Prefer registered week expiry.
    try:
        exp = str((scope or {}).get("expiry") or "").strip()
        if exp:
            data["calendar"] = {
                "expiry_date": exp[:10],
                "source": "registration_scope",
            }
    except Exception:
        pass

    try:
        from core.ato_working_profile import apply_working_profile_to_deploy

        apply_working_profile_to_deploy(data)
    except Exception as exc:
        logger.warning("working profile skip: %s", exc)

    with deployment_session("register_confirm"):
        moved = _archive_active_web()
        filepath.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        _sync_state(filepath, rt.state)
        if rt.event_bus:
            try:
                from core.event_bus import Event

                rt.event_bus.publish(Event.DEPLOYMENT_CONFIRMED, {"file": str(filepath)})
            except Exception:
                pass

    lines = [
        "Batman armed. Kavach is watching.",
        f"File: {filename}",
        f"Mode: {order_mode}",
        f"Register: {scope_choice}",
        f"ATO manage: {ato_mon}",
        f"Poll: {poll}s",
    ]
    if moved:
        lines.append("Archived: " + ", ".join(moved))
    if pe_on:
        lines.append(f"PE sell {pe_sell_strike}  protect {pe_prot}  {pe_ato_sym}")
    if ce_on:
        lines.append(f"CE sell {ce_sell_strike}  protect {ce_prot}  {ce_ato_sym}")
    return {"ok": True, "text": "\n".join(lines), "file": filename}

def _sync_state(filepath: Path, state: Any) -> None:
    from core.buffer_config.schema import normalize_buffer_field
    from core.position_scope import legacy_registration_scope_from_deployment

    if state is None:
        return
    dep = json.loads(filepath.read_text(encoding="utf-8"))
    positions = dep.get("positions") or {}
    ato = dep.get("ato") or {}
    scope = legacy_registration_scope_from_deployment(dep)
    for role, leg in positions.items():
        state.set(f"positions.{role}", leg, save=False)
    state.set("deployment.registration_scope", scope, save=False)
    state.set("deployment.strategy", dep.get("strategy") or "batman", save=False)
    state.set("ato.ce_protect_symbol", ato.get("ce_protect_symbol"), save=False)
    state.set("ato.ce_protect_strike", ato.get("ce_protect_strike"), save=False)
    state.set("ato.ce_protect_strike_mode", ato.get("ce_protect_strike_mode"), save=False)
    state.set("ato.pe_protect_symbol", ato.get("pe_protect_symbol"), save=False)
    state.set("ato.pe_protect_strike", ato.get("pe_protect_strike"), save=False)
    state.set("ato.pe_protect_strike_mode", ato.get("pe_protect_strike_mode"), save=False)
    state.set("ato.manage_sides", dep.get("ato_manage_sides", "both"), save=False)
    state.set("ato.poll_interval_seconds", ato.get("poll_interval_seconds"), save=False)
    ce_entry = ato.get("ce_entry_buffer", ato.get("ce_entry_buffer_points", 0))
    pe_entry = ato.get("pe_entry_buffer", ato.get("pe_entry_buffer_points", 0))
    ce_retrace = ato.get("ce_retrace_buffer", ato.get("ce_retrace_points", dep.get("retrace_points", 0)))
    pe_retrace = ato.get("pe_retrace_buffer", ato.get("pe_retrace_points", dep.get("retrace_points", 0)))
    state.set("ato.ce_entry_buffer_points", normalize_buffer_field(ce_entry), save=False)
    state.set("ato.pe_entry_buffer_points", normalize_buffer_field(pe_entry), save=False)
    state.set("ato.ce_retrace_points", normalize_buffer_field(ce_retrace), save=False)
    state.set("ato.pe_retrace_points", normalize_buffer_field(pe_retrace), save=False)
    has_dyn = bool(positions.get("pe_dyn_hedge") or positions.get("ce_dyn_hedge"))
    state.set("dyn_hedge.pe_exited_date", None, save=False)
    state.set("dyn_hedge.ce_exited_date", None, save=False)
    state.set("dyn_hedge.exit_enabled", has_dyn, save=False)
    state.set("ato.ce_triggered", False, save=False)
    state.set("ato.pe_triggered", False, save=False)
    state.set("ato.ce_ato_active", False, save=False)
    state.set("ato.pe_ato_active", False, save=False)
    state.set("ato.ce_awaiting_clearance", False, save=False)
    state.set("ato.pe_awaiting_clearance", False, save=False)
    try:
        from core.ato_side_state import clear_all_side_halts
        clear_all_side_halts(state, save=False)
    except Exception:
        state.set("ato.ce_side_halted", False, save=False)
        state.set("ato.pe_side_halted", False, save=False)
        state.set("ato.ce_halt_reason", None, save=False)
        state.set("ato.pe_halt_reason", None, save=False)
    state.set("deployment.confirmed", True, save=False)
    state.set("deployment.batman_complete", False, save=False)
    state.set("deployment.file", str(filepath), save=False)
    try:
        from core.overnight_handoff import reset_overnight_cycles

        reset_overnight_cycles(state)
    except Exception:
        pass
    # Overnight hedge (RATRIPAL): arm with this register so 15:15 plan can run today.
    has_sell = bool(positions.get("pe_sell") or positions.get("ce_sell"))
    state.set("modules.ratripal.enabled", has_sell, save=False)
    state.set("ratripal.last_run_date", None, save=False)
    state.set("ratripal.last_decision", None, save=False)
    state.set("ratripal.pending.request_id", None, save=False)
    state.set("ratripal.pending.response", None, save=False)
    state.set("overnight.hedge_active", False, save=False)
    state.set("overnight.morning_done_date", None, save=False)
    state.set("algo.paused", False, save=False)
    try:
        from core.pnl_exit_guard import clear_last_fire

        clear_last_fire(state, save=False)
    except Exception:
        state.set("pnl_exit.last_reason", None, save=False)
        state.set("pnl_exit.last_pnl", None, save=False)
        state.set("pnl_exit.last_at", None, save=False)
    try:
        from core.ato_working_profile import apply_working_profile_to_state

        apply_working_profile_to_state(state, save=False, for_deploy=True)
    except Exception:
        pass
    state.save()


def _sync_state_deploy_stub(filepath: Path, state: Any) -> None:
    """Load deployment file into state without arming Register (no deployment.confirmed)."""
    from core.buffer_config.schema import normalize_buffer_field
    from core.position_scope import legacy_registration_scope_from_deployment

    if state is None:
        return
    dep = json.loads(filepath.read_text(encoding="utf-8"))
    positions = dep.get("positions") or {}
    ato = dep.get("ato") or {}
    scope = legacy_registration_scope_from_deployment(dep)
    for role, leg in positions.items():
        state.set(f"positions.{role}", leg, save=False)
    state.set("deployment.registration_scope", scope, save=False)
    state.set("deployment.strategy", dep.get("strategy") or "directional", save=False)
    state.set("ato.ce_protect_symbol", ato.get("ce_protect_symbol"), save=False)
    state.set("ato.ce_protect_strike", ato.get("ce_protect_strike"), save=False)
    state.set("ato.pe_protect_symbol", ato.get("pe_protect_symbol"), save=False)
    state.set("ato.pe_protect_strike", ato.get("pe_protect_strike"), save=False)
    state.set("ato.manage_sides", dep.get("ato_manage_sides", "both"), save=False)
    state.set("deployment.confirmed", False, save=False)
    state.set("deployment.batman_complete", False, save=False)
    state.set("deployment.file", str(filepath), save=False)
    state.set("ato.ce_ato_active", False, save=False)
    state.set("ato.pe_ato_active", False, save=False)
    state.set("ato.ce_triggered", False, save=False)
    state.set("ato.pe_triggered", False, save=False)
    ce_entry = ato.get("ce_entry_buffer", ato.get("ce_entry_buffer_points", 0))
    pe_entry = ato.get("pe_entry_buffer", ato.get("pe_entry_buffer_points", 0))
    state.set("ato.ce_entry_buffer_points", normalize_buffer_field(ce_entry), save=False)
    state.set("ato.pe_entry_buffer_points", normalize_buffer_field(pe_entry), save=False)
    state.save()


def _week_expiry_from_payload(raw: Any) -> tuple[date | None, str, str | None]:
    from core.nifty_option_expiry import list_register_week_expiries

    text = str(raw or "").strip()
    options = list_register_week_expiries()
    if not text:
        return None, "", "Pick an expiry week before Deploy."
    try:
        exp_d = date.fromisoformat(text)
    except ValueError:
        return None, "", "Expiry week is not a valid date."
    label = next((str(o["label"]) for o in options if o["iso"] == text), "")
    if not label:
        return None, "", "Expiry is not in this week / next week / week after."
    return exp_d, label, None


def deploy_defaults(expiry: str | None = None) -> dict[str, Any]:
    from core.nifty_option_expiry import choose_default_register_expiry, list_register_week_expiries

    px = nifty_ltp()
    options = list_register_week_expiries()
    expiry_pub = [{"id": o["id"], "iso": o["iso"], "label": o["label"]} for o in options]
    chosen = str(expiry or "").strip()
    valid = {o["iso"] for o in expiry_pub}
    if chosen not in valid:
        chosen = choose_default_register_expiry([], options)
    expiry_label = next((o["label"] for o in expiry_pub if o["iso"] == chosen), chosen)
    return {
        "ok": True,
        "level": round_nifty_50(px) or "",
        "lots": 1,
        "nifty_ltp": px,
        "placeholder_level": "NIFTY center level (e.g. 24200)",
        "placeholder_lots": "Lots",
        "expiry": chosen,
        "expiry_label": expiry_label,
        "expiry_options": expiry_pub,
    }



def _atm_strike(level: float, step: int = 50) -> int:
    """Round NIFTY center to nearest strike step."""
    s = int(step) if int(step) > 0 else 50
    return int(round(float(level) / s) * s)


def _strike_choice_window(base_strike: int, *, step: int = 50, radius: int = 10) -> list[int]:
    """±radius strikes around a leg's default strike (dropdown choices)."""
    s = int(step) if int(step) > 0 else 50
    r = max(0, int(radius))
    base = int(base_strike)
    return [base + (i * s) for i in range(-r, r + 1)]


def _normalize_dir_view(raw: Any) -> str:
    v = str(raw or "").strip().lower()
    if v in ("bearish", "ce", "call", "calls"):
        return "bearish"
    if v in ("bullish", "pe", "put", "puts"):
        return "bullish"
    return ""


def _is_directional_payload(payload: dict[str, Any]) -> bool:
    if str(payload.get("strategy") or "").strip().lower() == "directional":
        return True
    return bool(_normalize_dir_view(payload.get("view")))


def build_directional_leg_specs(
    view: str,
    level: float,
    lots: int,
    *,
    strike_overrides: dict[str, Any] | None = None,
    strike_step: int = 50,
) -> list[dict[str, Any]]:
    """Default Directional 3-leg geometry (moneyness mirror for PE)."""
    atm = _atm_strike(level, strike_step)
    ov = strike_overrides or {}
    view_n = _normalize_dir_view(view) or "bearish"
    if view_n == "bearish":
        specs = [
            {"key": "ce_buy", "tag": "Core Buy", "side": "BUY", "opt": "CE", "strike": atm},
            {"key": "ce_margin_hedge", "tag": "Margin Hedge", "side": "BUY", "opt": "CE", "strike": atm + 500},
            {"key": "ce_sell", "tag": "CE Sell", "side": "SELL", "opt": "CE", "strike": atm - 200},
        ]
    else:
        specs = [
            {"key": "pe_buy", "tag": "Core Buy", "side": "BUY", "opt": "PE", "strike": atm},
            {"key": "pe_margin_hedge", "tag": "Margin Hedge", "side": "BUY", "opt": "PE", "strike": atm - 500},
            {"key": "pe_sell", "tag": "PE Sell", "side": "SELL", "opt": "PE", "strike": atm + 200},
        ]
    out: list[dict[str, Any]] = []
    for spec in specs:
        key = spec["key"]
        strike = spec["strike"]
        if key in ov and ov[key] not in (None, ""):
            try:
                strike = int(float(str(ov[key]).replace(",", "")))
            except (TypeError, ValueError):
                pass
        # also accept legs list style overrides by tag
        out.append({
            **spec,
            "strike": int(strike),
            "lots": int(lots),
            "qty": int(lots) * int(LOT_SIZE),
        })
    return out


def _selected_dir_leg_keys(payload: dict[str, Any]) -> set[str] | None:
    """If payload.legs is a non-empty list, return selected keys; else None (place all)."""
    legs = payload.get("legs")
    if not isinstance(legs, list) or not legs:
        return None
    keys: set[str] = set()
    for row in legs:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        if key:
            keys.add(key)
    return keys or None


def _filter_dir_specs_by_keys(
    specs: list[dict[str, Any]],
    selected: set[str] | None,
) -> list[dict[str, Any]]:
    if selected is None:
        return list(specs)
    return [s for s in specs if str(s.get("key") or "") in selected]


def _parse_dir_strike_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    ov: dict[str, Any] = {}
    raw_ov = payload.get("strikes") or payload.get("strike_overrides") or {}
    if isinstance(raw_ov, dict):
        for k, v in raw_ov.items():
            ov[str(k)] = v
    legs = payload.get("legs")
    if isinstance(legs, list):
        for row in legs:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip()
            if not key:
                continue
            if row.get("strike") not in (None, ""):
                ov[key] = row.get("strike")
    return ov


def _dir_buy_keys_before_sell(sell_key: str) -> tuple[str, ...]:
    return {
        "ce_sell": ("ce_buy", "ce_margin_hedge"),
        "pe_sell": ("pe_buy", "pe_margin_hedge"),
    }.get(sell_key, ())



def quote_directional_leg_ltp(payload: dict[str, Any]) -> dict[str, Any]:
    """Fresh LTP for one Directional preview strike (CE/PE) — Shoonya/feeder/Kite."""
    from bat_telegram.bots.kavach2.strategy import multileg_entry

    opt = str(payload.get("opt") or payload.get("option_type") or "").strip().upper()
    if opt not in ("CE", "PE"):
        return {"ok": False, "error": "opt must be CE or PE."}
    try:
        strike = int(float(str(payload.get("strike") or "").replace(",", "")))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Enter a valid strike."}
    if strike <= 0:
        return {"ok": False, "error": "Enter a valid strike."}
    exp_d, expiry_label, err = _week_expiry_from_payload(payload.get("expiry"))
    if err or exp_d is None:
        return {"ok": False, "error": err or "Pick an expiry week before quoting LTP."}
    exp = multileg_entry.resolve_expiry(exp_d)
    try:
        symbol, _ = multileg_entry.resolve_symbol(strike, opt, exp)
    except Exception as exc:
        return {"ok": False, "error": f"Symbol resolve failed: {exc}"}
    ltp = None
    try:
        rt = require_runtime()
        broker = getattr(rt, "broker", None)
        if broker is None:
            return {"ok": False, "error": _no_broker_error(), "symbol": symbol, "strike": strike, "opt": opt}
        # Prefer broker-native batch/quote (Shoonya get_ltp / feeder); Kite batch optional.
        getter = getattr(broker, "get_ltps_kite_batch", None)
        if callable(getter):
            try:
                batch = getter([symbol]) or {}
                ltp = batch.get(symbol)
            except TypeError:
                batch = None
        if ltp is None:
            try:
                ltp = broker.get_ltp([symbol]).get(symbol)
            except TypeError:
                # Some brokers accept kite_only kw; try without first (Shoonya).
                ltp = None
        if ltp is None:
            try:
                ltp = broker.get_ltp([symbol], kite_only=True).get(symbol)
            except TypeError:
                pass
    except Exception as exc:
        logger.warning("directional leg LTP quote failed: %s", exc)
        return {"ok": False, "error": str(exc), "symbol": symbol, "strike": strike, "opt": opt}
    if ltp is None:
        return {
            "ok": False,
            "error": "No LTP for this strike yet. Check expiry or try again.",
            "symbol": symbol,
            "strike": strike,
            "opt": opt,
        }
    return {
        "ok": True,
        "strategy": "directional",
        "symbol": symbol,
        "strike": strike,
        "opt": opt,
        "ltp": ltp,
        "expiry": exp.isoformat(),
        "expiry_label": expiry_label,
        "key": str(payload.get("key") or ""),
    }


def deploy_directional_preview(payload: dict[str, Any]) -> dict[str, Any]:
    from bat_telegram.bots.kavach2.strategy import multileg_entry
    from core.nifty_option_expiry import expiry_label_from_date
    from subscription.enforcement import check_directional_entry_allowed

    view = _normalize_dir_view(payload.get("view"))
    if not view:
        return {"ok": False, "error": "Pick Bearish View or Bullish View."}
    try:
        level = float(str(payload.get("level") or "").replace(",", ""))
        lots = int(payload.get("lots") or 1)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Enter a NIFTY center level and lots."}
    if lots < 1:
        return {"ok": False, "error": "Lots must be at least 1."}
    # Directional uses tracker Max Sell/ATO Directional (1:1), not Batman caps.
    _sub = check_directional_entry_allowed(
        requested_sell_lots=lots,
        requested_ato_lots=lots,
        action="deploy",
    )
    if not _sub.get("ok"):
        return {
            "ok": False,
            "error": str(_sub.get("error") or "Subscription blocked."),
            "subscription": _sub,
        }
    exp_d, expiry_label, err = _week_expiry_from_payload(payload.get("expiry"))
    if err or exp_d is None:
        return {"ok": False, "error": err or "Pick an expiry week before Deploy."}
    ov = _parse_dir_strike_overrides(payload)
    specs = build_directional_leg_specs(view, level, lots, strike_overrides=ov)
    atm = _atm_strike(level)
    strike_choices = _strike_choice_window(atm)
    exp = multileg_entry.resolve_expiry(exp_d)
    ltp_by_key: dict[str, Any] = {}
    try:
        rt = require_runtime()
        broker = getattr(rt, "broker", None)
        if broker is not None:
            names: list[str] = []
            meta: list[tuple[str, str]] = []
            for spec in specs:
                sym, _ = multileg_entry.resolve_symbol(int(spec["strike"]), str(spec["opt"]), exp)
                names.append(sym)
                meta.append((str(spec["key"]), sym))
            batch = {}
            getter = getattr(broker, "get_ltps_kite_batch", None)
            if callable(getter) and names:
                batch = getter(names) or {}
            for key, sym in meta:
                kite_px = batch.get(sym)
                if kite_px is None:
                    kite_px = broker.get_ltp([sym], kite_only=True).get(sym)
                ltp_by_key[key] = kite_px
    except Exception as exc:
        logger.warning("directional preview kite quote failed: %s", exc)

    place_rows = []
    for i, spec in enumerate(specs, start=1):
        place_rows.append({
            "sn": i,
            "type": spec["tag"],
            "key": spec["key"],
            "side": spec["side"],
            "opt": spec["opt"],
            "strike": int(spec["strike"]),
            "lots": int(spec["lots"]),
            "qty": int(spec["qty"]),
            "ltp": ltp_by_key.get(spec["key"]),
            "strike_choices": _strike_choice_window(int(spec["strike"])),
            "atm": atm,
        })
    exp_label = expiry_label_from_date(exp)
    title = "Bearish View" if view == "bearish" else "Bullish View"
    return {
        "ok": True,
        "strategy": "directional",
        "view": view,
        "view_label": title,
        "text": f"{title} preview ready.",
        "level": level,
        "atm": atm,
        "lots": lots,
        "expiry": exp.isoformat(),
        "expiry_label": exp_label or expiry_label,
        "place_rows": place_rows,
        "skipped_rows": [],
        "lot_size": LOT_SIZE,
        "strike_choices": strike_choices,
    }


def deploy_directional(payload: dict[str, Any]) -> dict[str, Any]:
    """Place Directional 3-leg basket (CE Bearish or PE Bullish)."""
    from bat_telegram.bots.kavach2.strategy import multileg_entry
    from bat_telegram.bots.kavach2.strategy.batman2_legs import LegPlan
    from core.nifty_option_expiry import expiry_label_from_date

    if payload.get("quote_ltp") or payload.get("ltp_quote"):
        return quote_directional_leg_ltp(payload)
    if not payload.get("confirm"):
        return deploy_directional_preview(payload)
    rt = require_runtime()
    if not rt.broker:
        return {"ok": False, "error": _no_broker_error()}
    view = _normalize_dir_view(payload.get("view"))
    if not view:
        return {"ok": False, "error": "Pick Bearish View or Bullish View."}
    try:
        level = float(str(payload.get("level") or "").replace(",", ""))
        lots = int(payload.get("lots") or 1)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Enter a NIFTY center level and lots."}
    if lots < 1:
        return {"ok": False, "error": "Lots must be at least 1."}
    from subscription.enforcement import check_directional_entry_allowed

    _sub = check_directional_entry_allowed(
        requested_sell_lots=lots,
        requested_ato_lots=lots,
        action="deploy",
    )
    if not _sub.get("ok"):
        return {
            "ok": False,
            "error": str(_sub.get("error") or "Subscription blocked."),
            "subscription": _sub,
        }
    exp_d, _expiry_label, err = _week_expiry_from_payload(payload.get("expiry"))
    if err or exp_d is None:
        return {"ok": False, "error": err or "Pick an expiry week before Deploy."}
    ov = _parse_dir_strike_overrides(payload)
    specs = build_directional_leg_specs(view, level, lots, strike_overrides=ov)
    selected_keys = _selected_dir_leg_keys(payload)
    specs = _filter_dir_specs_by_keys(specs, selected_keys)
    if not specs:
        return {"ok": False, "error": "No legs selected to place."}
    exp = multileg_entry.resolve_expiry(exp_d)
    legs_plan = [
        LegPlan(
            key=str(s["key"]),
            option_type=str(s["opt"]),
            side=str(s["side"]),
            strike=int(s["strike"]),
            qty=int(s["qty"]),
            role=str(s["tag"]),
        )
        for s in specs
    ]
    chase_timeout = 45.0
    chase_interval = 5.0
    buffer_pct = 10.0
    product = "MARGIN"
    filled: list[dict[str, Any]] = []
    pending_keys = [leg.key for leg in legs_plan if leg.qty > 0]
    status = "entry_only_done"
    error: str | None = None
    planned = {p.key: p for p in legs_plan}

    def _buys_missing(sell_key: str) -> list[str]:
        need = [k for k in _dir_buy_keys_before_sell(sell_key) if k in planned and int(planned[k].qty) > 0]
        filled_ok = {r["key"] for r in filled if r.get("status") == "filled"}
        return [k for k in need if k not in filled_ok]

    for leg in legs_plan:
        if leg.qty <= 0:
            continue
        if leg.key in ("ce_sell", "pe_sell"):
            missing = _buys_missing(leg.key)
            if missing:
                error = f"Cannot place {leg.key}: buy legs not fully filled yet ({', '.join(missing)})"
                filled.append({
                    "key": leg.key, "side": leg.side, "option_type": leg.option_type,
                    "strike": leg.strike, "qty": leg.qty, "role": leg.role,
                    "symbol": None, "order_id": None, "status": "skipped", "error": error,
                })
                status = "partial"
                break
        try:
            symbol, _ = multileg_entry.resolve_symbol(leg.strike, leg.option_type, exp)
        except Exception as exc:
            error = f"Symbol resolve failed for {leg.key}: {exc}"
            status = "partial"
            break
        try:
            if hasattr(rt.broker, "place_aggressive_limit"):
                order_id = rt.broker.place_aggressive_limit(
                    symbol=symbol, qty=leg.qty, side=leg.side,
                    buffer_pct=buffer_pct, chase_timeout_sec=chase_timeout,
                    chase_interval_sec=chase_interval, trade_type=product,
                )
            else:
                order_id = rt.broker.place_market_order(
                    symbol=symbol, qty=leg.qty, side=leg.side, trade_type=product,
                )
            filled.append({
                "key": leg.key, "side": leg.side, "option_type": leg.option_type,
                "strike": leg.strike, "qty": leg.qty, "role": leg.role,
                "symbol": symbol, "order_id": str(order_id), "status": "filled",
            })
            pending_keys = [k for k in pending_keys if k != leg.key]
        except Exception as exc:
            error = f"{leg.key} failed: {exc}"
            filled.append({
                "key": leg.key, "side": leg.side, "option_type": leg.option_type,
                "strike": leg.strike, "qty": leg.qty, "role": leg.role,
                "symbol": symbol, "order_id": None, "status": "failed", "error": str(exc),
            })
            for rest in legs_plan:
                if rest.key in pending_keys and rest.key != leg.key and rest.qty > 0:
                    filled.append({
                        "key": rest.key, "side": rest.side, "option_type": rest.option_type,
                        "strike": rest.strike, "qty": rest.qty, "role": rest.role,
                        "symbol": None, "order_id": None, "status": "skipped",
                    })
            status = "partial"
            break

    result = {
        "status": status,
        "strategy": "directional",
        "view": view,
        "center_level": float(level),
        "expiry": expiry_label_from_date(exp),
        "base_lots": lots,
        "lot_size": LOT_SIZE,
        "legs": filled,
    }
    if error:
        result["error"] = error
    filled_n = sum(1 for x in filled if x.get("status") == "filled")
    text = multileg_entry.format_result_text(result)
    ok = bool(filled_n > 0 and not result.get("error"))
    if not ok:
        return {
            "ok": False,
            "error": str(result.get("error") or "No legs filled — no broker orders sent."),
            "text": text,
            "result": result,
        }
    return {"ok": True, "text": text, "result": result}



def _overlay_saved_ato_levels_onto_register(
    reg: dict[str, Any],
    deploy_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep previously saved ATO NIFTY levels / protect when re-arming after qty add-on.

    Confirm Then Sync / Deploy+Register used to rebuild CE/PE entry-exit from auto
    protect defaults (e.g. 22850/22840), wiping Buffer Manager values the desk already
    set on the live armed book.
    """
    deploy_payload = deploy_payload or {}
    try:
        st = require_runtime().state
    except Exception:
        st = None
    if st and st.get("deployment.confirmed"):
        try:
            form = buffer_form()
        except Exception as exc:
            logger.warning("preserve ATO levels: buffer_form failed: %s", exc)
            form = {}
        scope = str(reg.get("reg_scope") or "").strip().lower()
        mon = str(reg.get("ato_mon") or "").strip().lower()
        want_ce = scope in ("ce", "both") or mon in ("ce", "both")
        want_pe = scope in ("pe", "both") or mon in ("pe", "both")
        if want_ce:
            ce_e = str(form.get("ce_entry") or "").strip()
            ce_x = str(form.get("ce_retrace") or "").strip()
            if ce_e:
                reg["ce_entry"] = ce_e
            if ce_x:
                reg["ce_exit"] = ce_x
            if str(form.get("ce_ato_mode") or "").strip().lower() == "custom":
                strike = str(form.get("ce_ato_strike") or "").strip()
                if strike:
                    reg["ce_ato_mode"] = "custom"
                    reg["ce_ato_strike"] = strike
        if want_pe:
            pe_e = str(form.get("pe_entry") or "").strip()
            pe_x = str(form.get("pe_retrace") or "").strip()
            if pe_e:
                reg["pe_entry"] = pe_e
            if pe_x:
                reg["pe_exit"] = pe_x
            if str(form.get("pe_ato_mode") or "").strip().lower() == "custom":
                strike = str(form.get("pe_ato_strike") or "").strip()
                if strike:
                    reg["pe_ato_mode"] = "custom"
                    reg["pe_ato_strike"] = strike
        try:
            dep = _load_dep() or {}
            poll = (dep.get("ato") or {}).get("poll_interval_seconds")
            if poll is not None and str(poll).strip() != "":
                reg["poll_interval"] = poll
        except Exception:
            pass

    # Explicit values from the web payload always win (desk typed them on this click).
    for src_key, dest_key in (
        ("ce_entry", "ce_entry"),
        ("ce_exit", "ce_exit"),
        ("ce_retrace", "ce_exit"),
        ("pe_entry", "pe_entry"),
        ("pe_exit", "pe_exit"),
        ("pe_retrace", "pe_exit"),
    ):
        raw = deploy_payload.get(src_key)
        if str(raw or "").strip():
            reg[dest_key] = raw
    for src_key in ("ce_ato_mode", "ce_ato_strike", "pe_ato_mode", "pe_ato_strike", "poll_interval"):
        raw = deploy_payload.get(src_key)
        if str(raw or "").strip():
            reg[src_key] = raw
    return reg


def _build_register_payload_from_directional(
    deploy_payload: dict[str, Any],
    deploy_result: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    legs = list((deploy_result or {}).get("legs") or [])
    required = [leg for leg in legs if int(leg.get("qty") or 0) > 0]
    if not required:
        return None, "No placeable legs found in deploy result."
    bad = [
        leg for leg in required
        if str(leg.get("status") or "").lower() != "filled" or not str(leg.get("symbol") or "").strip()
    ]
    if bad:
        bits = [f"{leg.get('key')}[{leg.get('status')}]" for leg in bad]
        return None, "Register not started because one or more legs were not fully filled: " + ", ".join(bits)
    by_key = {str(leg.get("key") or ""): leg for leg in required}
    view = _normalize_dir_view(deploy_payload.get("view") or deploy_result.get("view"))
    expiry_raw = str(deploy_payload.get("expiry") or "").strip()
    if not expiry_raw:
        return None, "Pick an expiry week before Register."
    if view == "bearish":
        must = ["ce_buy", "ce_sell"]
        missing = [k for k in must if k not in by_key]
        if missing:
            return None, "Register payload missing required deployed legs: " + ", ".join(missing)
        try:
            ce_buy_strike = int(by_key["ce_buy"].get("strike") or 0)
        except (TypeError, ValueError):
            return None, "Could not read CE buy strike from deploy result."
        from core.position_scope import auto_protect_strike as _aps
        ce_prot = int(_aps(ce_buy_strike, "CE", ato_step=ATO_STEP))
        reg = {
            "confirm": True,
            "strategy": "directional",
            "order_mode": str(_default_order_mode()),
            "expiry": expiry_raw,
            "reg_scope": "ce",
            "ce_buy": str(by_key["ce_buy"].get("symbol") or ""),
            "ce_margin_hedge": str(by_key.get("ce_margin_hedge", {}).get("symbol") or ""),
            "ce_dyn_hedge": "",
            "ce_sell": str(by_key["ce_sell"].get("symbol") or ""),
            "pe_buy": "",
            "pe_margin_hedge": "",
            "pe_dyn_hedge": "",
            "pe_sell": "",
            "ce_ato_mode": "auto",
            "pe_ato_mode": "auto",
            "ato_mon": "ce",
            "poll_interval": 2,
            "ce_entry": ce_prot,
            "ce_exit": ce_prot - 10,
            "pe_entry": "",
            "pe_exit": "",
        }
        return _overlay_saved_ato_levels_onto_register(reg, deploy_payload), None
    if view == "bullish":
        must = ["pe_buy", "pe_sell"]
        missing = [k for k in must if k not in by_key]
        if missing:
            return None, "Register payload missing required deployed legs: " + ", ".join(missing)
        try:
            pe_buy_strike = int(by_key["pe_buy"].get("strike") or 0)
        except (TypeError, ValueError):
            return None, "Could not read PE buy strike from deploy result."
        from core.position_scope import auto_protect_strike as _aps
        pe_prot = int(_aps(pe_buy_strike, "PE", ato_step=ATO_STEP))
        reg = {
            "confirm": True,
            "strategy": "directional",
            "order_mode": str(_default_order_mode()),
            "expiry": expiry_raw,
            "reg_scope": "pe",
            "pe_buy": str(by_key["pe_buy"].get("symbol") or ""),
            "pe_margin_hedge": str(by_key.get("pe_margin_hedge", {}).get("symbol") or ""),
            "pe_dyn_hedge": "",
            "pe_sell": str(by_key["pe_sell"].get("symbol") or ""),
            "ce_buy": "",
            "ce_margin_hedge": "",
            "ce_dyn_hedge": "",
            "ce_sell": "",
            "ce_ato_mode": "auto",
            "pe_ato_mode": "auto",
            "ato_mon": "pe",
            "poll_interval": 2,
            "pe_entry": pe_prot,
            "pe_exit": pe_prot + 10,
            "ce_entry": "",
            "ce_exit": "",
        }
        return _overlay_saved_ato_levels_onto_register(reg, deploy_payload), None
    return None, "Unknown Directional view for Register."


def deploy_preview(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("quote_ltp") or payload.get("ltp_quote"):
        return quote_directional_leg_ltp(payload)
    if _is_directional_payload(payload):
        return deploy_directional_preview(payload)
    from bat_telegram.bots.kavach2.strategy import multileg_entry
    from core.nifty_option_expiry import expiry_label_from_date

    try:
        level = float(str(payload.get("level") or "").replace(",", ""))
        lots = int(payload.get("lots") or 1)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Enter a NIFTY center level and lots."}
    if lots < 1:
        return {"ok": False, "error": "Lots must be at least 1."}
    from subscription.enforcement import check_batman_entry_allowed, deploy_requested_limits

    _sell_req, _ato_req = deploy_requested_limits(lots)
    _sub = check_batman_entry_allowed(
        requested_sell_lots=_sell_req,
        requested_ato_lots=_ato_req,
        action="deploy",
    )
    if not _sub.get("ok"):
        return {
            "ok": False,
            "error": str(_sub.get("error") or "Subscription blocked."),
            "subscription": _sub,
        }
    if lots < 1:
        return {"ok": False, "error": "Lots must be at least 1."}
    exp_d, expiry_label, err = _week_expiry_from_payload(payload.get("expiry"))
    if err or exp_d is None:
        return {"ok": False, "error": err or "Pick an expiry week before Deploy."}
    legs, preview = multileg_entry.build_preview(level, base_lots=lots, lot_size=LOT_SIZE, params={})
    exp = multileg_entry.resolve_expiry(exp_d)
    ltp_by_key: dict[str, Any] = {}
    try:
        from web.runtime import require_runtime

        rt = require_runtime()
        broker = getattr(rt, "broker", None)
        if broker is not None:
            names: list[str] = []
            meta: list[tuple[str, str]] = []
            for leg in legs:
                strike = int(leg.strike)
                opt = str(leg.option_type).upper()
                sym, _ = multileg_entry.resolve_symbol(strike, opt, exp)
                names.append(sym)
                meta.append((str(getattr(leg, "key", "")), sym))
            batch = {}
            getter = getattr(broker, "get_ltps_kite_batch", None)
            if callable(getter) and names:
                batch = getter(names) or {}
            for key, sym in meta:
                kite_px = batch.get(sym)
                if kite_px is None:
                    kite_px = broker.get_ltp([sym], kite_only=True).get(sym)
                ltp_by_key[key] = kite_px
        else:
            logger.warning("preview kite quote skipped: broker not connected")
    except Exception as exc:
        logger.warning("preview kite quote failed: %s", exc)

    def _table_row(sn: int, key: str, side: str, opt: str, strike: int, qty: int) -> dict[str, Any]:
        ls = int(LOT_SIZE) or 65
        return {
            "sn": sn,
            "type": key,
            "side": side,
            "opt": opt,
            "strike": int(strike),
            "lots": int(qty) // ls if ls else 0,
            "qty": int(qty),
            "ltp": ltp_by_key.get(key),
        }

    place_rows = [
        _table_row(
            i,
            str(row["key"]),
            str(row["side"]),
            str(row["type"]),
            int(row["strike"]),
            int(row.get("qty") or 0),
        )
        for i, row in enumerate(preview, start=1)
    ]
    shown = {str(row["key"]) for row in preview}
    skipped_legs = [leg for leg in legs if int(leg.qty or 0) <= 0 and leg.key not in shown]
    skipped_rows = [
        _table_row(i, leg.key, leg.side, leg.option_type, int(leg.strike), int(leg.qty or 0))
        for i, leg in enumerate(skipped_legs, start=1)
    ]
    exp_label = expiry_label_from_date(exp)
    text = multileg_entry.format_preview_text(
        level,
        preview,
        expiry_label=exp_label,
        base_lots=lots,
        all_legs=legs,
        lot_size=LOT_SIZE,
        ltp_by_key=ltp_by_key,
    )
    return {
        "ok": True,
        "text": text,
        "level": level,
        "lots": lots,
        "expiry": exp.isoformat(),
        "expiry_label": exp_label,
        "place_rows": place_rows,
        "skipped_rows": skipped_rows,
        "lot_size": LOT_SIZE,
    }


def deploy_batman(payload: dict[str, Any]) -> dict[str, Any]:
    from bat_telegram.bots.kavach2.strategy import multileg_entry

    if payload.get("quote_ltp") or payload.get("ltp_quote"):
        return quote_directional_leg_ltp(payload)
    if _is_directional_payload(payload):
        return deploy_directional(payload)
    if not payload.get("confirm"):
        return deploy_preview(payload)
    rt = require_runtime()
    if not rt.broker:
        return {"ok": False, "error": _no_broker_error()}
    try:
        level = float(str(payload.get("level") or "").replace(",", ""))
        lots = int(payload.get("lots") or 1)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Enter a NIFTY center level and lots."}
    exp_d, _expiry_label, err = _week_expiry_from_payload(payload.get("expiry"))
    if err or exp_d is None:
        return {"ok": False, "error": err or "Pick an expiry week before Deploy."}
    try:
        result = multileg_entry.deploy_multileg(
            rt.broker,
            center_level=level,
            base_lots=lots,
            lot_size=LOT_SIZE,
            expiry=exp_d,
        )
        filled_n = sum(1 for x in (result.get("legs") or []) if x.get("status") == "filled")
        ok = bool(filled_n > 0 and not result.get("error"))
        text = multileg_entry.format_result_text(result)
        if not ok:
            return {
                "ok": False,
                "error": str(result.get("error") or "No legs filled — no broker orders sent."),
                "text": text,
                "result": result,
            }
        return {"ok": True, "text": text, "result": result}
    except Exception as exc:
        logger.exception("web deploy failed")
        return {"ok": False, "error": str(exc)}


def _build_register_payload_from_deploy(
    deploy_payload: dict[str, Any],
    deploy_result: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Build a Register payload only when every placeable leg fully filled."""
    legs = list((deploy_result or {}).get("legs") or [])
    required = [leg for leg in legs if int(leg.get("qty") or 0) > 0]
    if not required:
        return None, "No placeable legs found in deploy result."
    bad = [
        leg for leg in required
        if str(leg.get("status") or "").lower() != "filled" or not str(leg.get("symbol") or "").strip()
    ]
    if bad:
        bits = [f"{leg.get('key')}[{leg.get('status')}]" for leg in bad]
        return None, "Register not started because one or more legs were not fully filled: " + ", ".join(bits)

    by_key = {str(leg.get('key') or ''): leg for leg in required}
    must = ['ce_buy', 'ce_sell', 'pe_buy', 'pe_sell']
    missing = [k for k in must if k not in by_key]
    if missing:
        return None, 'Register payload missing required deployed legs: ' + ', '.join(missing)

    try:
        ce_sell_strike = int(by_key['ce_sell'].get('strike') or 0)
        pe_sell_strike = int(by_key['pe_sell'].get('strike') or 0)
    except (TypeError, ValueError):
        return None, 'Could not read CE/PE sell strikes from deploy result.'
    if ce_sell_strike <= 0 or pe_sell_strike <= 0:
        return None, 'Could not read CE/PE sell strikes from deploy result.'

    expiry_raw = str(deploy_payload.get('expiry') or '').strip()
    if not expiry_raw:
        return None, 'Pick an expiry week before Register.'

    reg: dict[str, Any] = {
        'confirm': True,
        'order_mode': str(_default_order_mode()),
        'expiry': expiry_raw,
        'reg_scope': 'both',
        'ce_buy': str(by_key['ce_buy'].get('symbol') or ''),
        'ce_margin_hedge': str(by_key.get('ce_margin_hedge', {}).get('symbol') or ''),
        'ce_dyn_hedge': str(by_key.get('ce_dyn_hedge', {}).get('symbol') or ''),
        'ce_sell': str(by_key['ce_sell'].get('symbol') or ''),
        'pe_buy': str(by_key['pe_buy'].get('symbol') or ''),
        'pe_margin_hedge': str(by_key.get('pe_margin_hedge', {}).get('symbol') or ''),
        'pe_dyn_hedge': str(by_key.get('pe_dyn_hedge', {}).get('symbol') or ''),
        'pe_sell': str(by_key['pe_sell'].get('symbol') or ''),
        'ce_ato_mode': 'auto',
        'pe_ato_mode': 'auto',
        'ato_mon': 'both',
        'poll_interval': 2,
        'ce_entry': ce_sell_strike,
        'ce_exit': ce_sell_strike - 10,
        'pe_entry': pe_sell_strike,
        'pe_exit': pe_sell_strike + 10,
    }
    return reg, None


def deploy_and_register_batman(payload: dict[str, Any]) -> dict[str, Any]:
    is_dir = _is_directional_payload(payload)
    snapshot: dict[str, int] = {}
    if is_dir:
        try:
            snapshot = _book_abs_qty_by_symbol()
        except Exception as exc:
            logger.warning("directional deploy-register snapshot failed: %s", exc)
            snapshot = {}

    deploy_out = deploy_batman({**payload, 'confirm': True})
    if not bool(deploy_out.get('ok')):
        return deploy_out

    if is_dir:
        result = deploy_out.get('result') or {}
        legs = list(result.get('legs') or [])
        placeable = [leg for leg in legs if abs(int(leg.get('qty') or 0)) > 0]
        if not placeable:
            err = "No placeable legs found in deploy result."
            return {
                'ok': False,
                'error': err,
                'text': (deploy_out.get('text') or '') + '\n\n' + err,
                'deploy': deploy_out,
            }
        bad = [
            leg for leg in placeable
            if str(leg.get('status') or '').lower() != 'filled' or not str(leg.get('symbol') or '').strip()
        ]
        if bad:
            bits = [f"{leg.get('key')}[{leg.get('status')}]" for leg in bad]
            err = "Register not started because one or more legs were not fully filled: " + ", ".join(bits)
            return {
                'ok': False,
                'error': err,
                'text': (deploy_out.get('text') or '') + '\n\n' + err,
                'deploy': deploy_out,
            }
        ok_v, verr = _verify_directional_fills_in_book(placeable, snapshot)
        if not ok_v:
            err = str(verr or "Broker book verify failed after deploy.")
            return {
                'ok': False,
                'error': err,
                'text': (deploy_out.get('text') or '') + '\n\n' + err,
                'deploy': deploy_out,
                'verify_failed': True,
            }
        reg_payload, err = _build_register_payload_from_directional(payload, result)
    else:
        reg_payload, err = _build_register_payload_from_deploy(payload, deploy_out.get('result') or {})

    if err:
        return {
            'ok': False,
            'error': err,
            'text': (deploy_out.get('text') or '') + '\n\n' + err,
            'deploy': deploy_out,
        }
    reg_out = register_batman(reg_payload)
    if not bool(reg_out.get('ok')):
        return {
            'ok': False,
            'error': str(reg_out.get('error') or 'Register failed.'),
            'text': (deploy_out.get('text') or '') + '\n\nDeploy completed, but Register failed: ' + str(reg_out.get('error') or 'Register failed.'),
            'deploy': deploy_out,
            'register': reg_out,
            'register_payload': reg_payload,
        }

    size_bits: list[str] = []
    try:
        rt = require_runtime()
        scope = (rt.state.get("deployment.registration_scope") if rt.state else None) or {}
        for side in ("ce", "pe"):
            ml = scope.get(f"{side}_managed_lots")
            al = scope.get(f"{side}_ato_lots")
            if ml is None and al is None:
                continue
            size_bits.append(
                f"{side.upper()} managed={ml if ml is not None else '-'} lots, ATO={al if al is not None else '-'} lots"
            )
    except Exception:
        pass
    size_line = ("\nRegistered size: " + "; ".join(size_bits)) if size_bits else ""
    detail = (deploy_out.get('text') or '') + '\n\n' + (reg_out.get('text') or '') + size_line
    return {
        'ok': True,
        'text': 'Deploy sent and Register armed successfully.' + size_line,
        'detail_text': detail,
        'deploy': deploy_out,
        'register': reg_out,
        'register_payload': reg_payload,
    }


def sync_registered_lots_from_broker(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh managed/ATO lots from broker qty for the armed deployment. No orders."""
    from core.deployment_lock import deployment_session
    from core.position_scope import (
        build_registration_scope,
        qty_to_lots,
        suggested_ato_lots,
    )

    payload = payload or {}
    if not payload.get("confirm"):
        return {"ok": False, "error": "Confirm Sync lots from broker."}

    path = _active_deployment()
    if not path:
        return {"ok": False, "error": "No active deployment. Register first."}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"Could not read deployment: {exc}"}

    positions = dict(data.get("positions") or {})
    if not any(positions.get(k) for k in ("ce_buy", "pe_buy", "ce_sell", "pe_sell")):
        return {"ok": False, "error": "Deployment has no registered Core Buy / Sell legs."}

    lot_size = _lot_size()
    book, note = _load_register_book()
    if not book:
        return {"ok": False, "error": note or "No NIFTY option legs in the broker book."}

    before: dict[str, dict[str, Any]] = {}
    updated: dict[str, Any] = {}
    for role, leg in positions.items():
        if not isinstance(leg, dict) or not str(leg.get("symbol") or "").strip():
            updated[role] = leg
            continue
        sym = str(leg.get("symbol") or "").strip()
        pos = _find_book_leg(book, sym)
        if not pos:
            return {"ok": False, "error": f"{role} ({sym}) is not in the current broker book."}
        want_dir = str(leg.get("direction") or "").strip().upper()
        got_dir = str(pos.get("direction") or "").strip().upper()
        if want_dir and got_dir and want_dir != got_dir:
            return {
                "ok": False,
                "error": f"{role} ({sym}) direction mismatch: book={got_dir} registered={want_dir}.",
            }
        old_qty = abs(int(leg.get("qty") or 0))
        new_leg = _store_leg(pos, lot_size)
        # Keep prior avg if broker avg missing
        if not new_leg.get("avg_price") and leg.get("avg_price"):
            new_leg["avg_price"] = leg.get("avg_price")
        before[role] = {
            "qty": old_qty,
            "lots": qty_to_lots(old_qty, lot_size),
        }
        updated[role] = new_leg

    pe_on = bool(updated.get("pe_buy") and updated.get("pe_sell"))
    ce_on = bool(updated.get("ce_buy") and updated.get("ce_sell"))
    if not pe_on and not ce_on:
        return {"ok": False, "error": "Need Core Buy + Sell on at least one side after sync."}

    pe_managed = qty_to_lots(int(updated["pe_buy"]["qty"]), lot_size) if pe_on else None
    ce_managed = qty_to_lots(int(updated["ce_buy"]["qty"]), lot_size) if ce_on else None
    pe_ato_lots = (
        suggested_ato_lots(pe_managed, int(updated["pe_buy"]["qty"]), lot_size=lot_size)
        if pe_on else None
    )
    ce_ato_lots = (
        suggested_ato_lots(ce_managed, int(updated["ce_buy"]["qty"]), lot_size=lot_size)
        if ce_on else None
    )

    from subscription.enforcement import (
        check_entry_allowed_for_strategy,
        register_requested_limits_from_legs,
    )

    pe_sell_lots_sub = qty_to_lots(int(updated["pe_sell"]["qty"]), lot_size) if pe_on else 0
    ce_sell_lots_sub = qty_to_lots(int(updated["ce_sell"]["qty"]), lot_size) if ce_on else 0
    req_sell, req_ato = register_requested_limits_from_legs(
        pe_sell_lots=pe_sell_lots_sub,
        ce_sell_lots=ce_sell_lots_sub,
        pe_ato_lots=int(pe_ato_lots or 0),
        ce_ato_lots=int(ce_ato_lots or 0),
    )
    _sync_strat = "directional" if deployment_strategy() == "directional" else "batman"
    sub_gate = check_entry_allowed_for_strategy(
        strategy=_sync_strat,
        requested_sell_lots=req_sell,
        requested_ato_lots=req_ato,
        action="register",
    )
    if not sub_gate.get("ok"):
        return {
            "ok": False,
            "error": str(sub_gate.get("error") or "Subscription blocked."),
            "subscription": sub_gate,
        }

    old_scope = dict(data.get("registration_scope") or {})
    scope = build_registration_scope(
        pe_enabled=pe_on,
        ce_enabled=ce_on,
        pe_managed_lots=pe_managed,
        ce_managed_lots=ce_managed,
        pe_ato_lots=pe_ato_lots,
        ce_ato_lots=ce_ato_lots,
        lot_size=lot_size,
    )
    # Preserve expiry / labels from prior scope when present
    for key in ("expiry", "expiry_label"):
        if old_scope.get(key) and not scope.get(key):
            scope[key] = old_scope.get(key)
        elif old_scope.get(key):
            scope[key] = old_scope.get(key)

    data["positions"] = updated
    data["registration_scope"] = scope
    data["lots_synced_at"] = datetime.now().astimezone().isoformat()

    rt = require_runtime()
    with deployment_session("sync_lots_from_broker"):
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        st = rt.state
        if st is not None:
            for role, leg in updated.items():
                st.set(f"positions.{role}", leg, save=False)
            st.set("deployment.registration_scope", scope, save=False)
            st.set("deployment.file", str(path), save=False)
            try:
                st.save()
            except Exception:
                pass

    bits: list[str] = []
    for side, ml, al in (
        ("CE", ce_managed, ce_ato_lots),
        ("PE", pe_managed, pe_ato_lots),
    ):
        if ml is None and al is None:
            continue
        buy_role = f"{side.lower()}_buy"
        old = before.get(buy_role) or {}
        bits.append(
            f"{side} managed {old.get('lots', '-')} → {ml if ml is not None else '-'} lots; "
            f"ATO {old_scope.get(f'{side.lower()}_ato_lots', '-')} → {al if al is not None else '-'}"
        )
    text = "Synced lots from broker. " + "; ".join(bits)
    if note:
        text = text + f" ({note})"
    return {
        "ok": True,
        "text": text,
        "ce_managed_lots": ce_managed,
        "pe_managed_lots": pe_managed,
        "ce_ato_lots": ce_ato_lots,
        "pe_ato_lots": pe_ato_lots,
        "lot_size": lot_size,
        "file": path.name,
    }


def _gate_directional_confirm_then_sync(payload: dict[str, Any]) -> str | None:
    view = _normalize_dir_view(payload.get("view"))
    if not view:
        return "Pick Bearish View or Bullish View."
    keys = _selected_dir_leg_keys(payload)
    if keys is None:
        return None
    buy_key = "ce_buy" if view == "bearish" else "pe_buy"
    sell_key = "ce_sell" if view == "bearish" else "pe_sell"
    if buy_key not in keys or sell_key not in keys:
        return "Confirm then sync needs Core Buy and Sell checked. Margin Hedge is optional."
    return None


def _materialize_directional_deployment_stub(
    deploy_payload: dict[str, Any],
    deploy_result: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Write active deployment JSON from broker book after deploy (not Register / no ATO arm)."""
    from core.batman_mode import deployments_dir
    from core.buffer_config.schema import BufferKind, serialize_buffer_field
    from core.deployment_lock import deployment_session
    from core.order_mode import normalize_order_mode
    from core.position_scope import build_registration_scope, qty_to_lots, suggested_ato_lots

    view = _normalize_dir_view(deploy_payload.get("view") or deploy_result.get("view"))
    exp_d, expiry_label, err = _week_expiry_from_payload(deploy_payload.get("expiry"))
    if err or exp_d is None:
        return None, err or "Pick an expiry week before Deploy."

    legs = [leg for leg in (deploy_result.get("legs") or []) if int(leg.get("qty") or 0) > 0]
    if not legs:
        return None, "No placeable legs found in deploy result."

    book, note = _load_register_book()
    if not book:
        return None, note or "No NIFTY option legs in the broker book."

    lot_size = _lot_size()
    selected: dict[str, Any] = {
        "pe_buy": None,
        "pe_margin_hedge": None,
        "pe_dyn_hedge": None,
        "pe_sell": None,
        "ce_buy": None,
        "ce_margin_hedge": None,
        "ce_dyn_hedge": None,
        "ce_sell": None,
    }
    for leg in legs:
        key = str(leg.get("key") or "").strip()
        if key not in selected:
            continue
        sym = str(leg.get("symbol") or "").strip()
        if not sym:
            return None, f"{key} has no symbol in deploy result."
        pos = _find_book_leg(book, sym)
        if not pos:
            return None, f"{key} ({sym}) is not in the current broker book."
        selected[key] = _store_leg(pos, lot_size)

    pe_on = bool(selected.get("pe_buy") and selected.get("pe_sell"))
    ce_on = bool(selected.get("ce_buy") and selected.get("ce_sell"))
    if view == "bearish" and not ce_on:
        return None, "Confirm then sync needs CE Buy and Sell filled on the broker book."
    if view == "bullish" and not pe_on:
        return None, "Confirm then sync needs PE Buy and Sell filled on the broker book."

    pe_managed = qty_to_lots(int(selected["pe_buy"]["qty"]), lot_size) if pe_on else None
    ce_managed = qty_to_lots(int(selected["ce_buy"]["qty"]), lot_size) if ce_on else None
    pe_ato_lots = (
        suggested_ato_lots(pe_managed, int(selected["pe_buy"]["qty"]), lot_size=lot_size)
        if pe_on
        else None
    )
    ce_ato_lots = (
        suggested_ato_lots(ce_managed, int(selected["ce_buy"]["qty"]), lot_size=lot_size)
        if ce_on
        else None
    )

    expiry_raw = str(deploy_payload.get("expiry") or "").strip() or exp_d.isoformat()
    scope = build_registration_scope(
        pe_enabled=pe_on,
        ce_enabled=ce_on,
        pe_managed_lots=pe_managed,
        ce_managed_lots=ce_managed,
        pe_ato_lots=pe_ato_lots,
        ce_ato_lots=ce_ato_lots,
        lot_size=lot_size,
    )
    scope["expiry"] = expiry_raw
    scope["expiry_label"] = expiry_label

    ato_mon = "ce" if view == "bearish" else "pe"
    now = datetime.now()
    rt = require_runtime()
    filename = f"batman_{now.strftime('%Y-%m-%d_%H-%M')}.json"
    dep_dir = deployments_dir(rt.root)
    dep_dir.mkdir(parents=True, exist_ok=True)
    filepath = dep_dir / filename
    zero_buf = serialize_buffer_field(Decimal("0"), BufferKind.CUSTOM)
    data: dict[str, Any] = {
        "schema_version": 1.1,
        "strategy": "directional",
        "order_mode": normalize_order_mode(_default_order_mode()),
        "deployed_at": now.astimezone().isoformat(),
        "registered_at": now.strftime("%A %d-%b-%Y at %H:%M") + " (deploy stub, not registered)",
        "file_name": filename,
        "source": "kavach_web",
        "retrace_points": 0,
        "ato_manage_sides": ato_mon,
        "registration_scope": scope,
        "positions": selected,
        "ato": {
            "pe_protect_symbol": None,
            "pe_protect_strike": None,
            "pe_protect_strike_mode": None,
            "ce_protect_symbol": None,
            "ce_protect_strike": None,
            "ce_protect_strike_mode": None,
            "ato_step": ATO_STEP,
            "ce_entry_buffer": zero_buf,
            "pe_entry_buffer": zero_buf,
            "ce_retrace_buffer": zero_buf,
            "pe_retrace_buffer": zero_buf,
            "ce_entry_buffer_points": 0,
            "pe_entry_buffer_points": 0,
            "ce_retrace_points": 0,
            "pe_retrace_points": 0,
            "poll_interval_seconds": 2,
        },
        "status": "deployed",
        "calendar": {"expiry_date": expiry_raw[:10], "source": "registration_scope"},
    }

    with deployment_session("directional_deploy_stub"):
        moved = _archive_active_web()
        filepath.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        _sync_state_deploy_stub(filepath, rt.state)

    text = f"Deployment stub written: {filename}"
    if moved:
        text = text + " (archived: " + ", ".join(moved) + ")"
    if note:
        text = text + f" ({note})"
    return {"ok": True, "text": text, "file": filename}, None


def deploy_directional_confirm_then_sync(payload: dict[str, Any]) -> dict[str, Any]:
    """Deploy Directional legs, verify broker book, Register/arm ATO, sync lots."""
    gate_err = _gate_directional_confirm_then_sync(payload)
    if gate_err:
        return {"ok": False, "error": gate_err, "text": gate_err}
    if not _is_directional_payload(payload):
        return {"ok": False, "error": "Directional payload required.", "text": "Directional payload required."}

    snapshot: dict[str, int] = {}
    try:
        snapshot = _book_abs_qty_by_symbol()
    except Exception as exc:
        logger.warning("directional deploy-then-sync snapshot failed: %s", exc)
        snapshot = {}

    deploy_out = deploy_directional({**payload, "confirm": True})
    if not bool(deploy_out.get("ok")):
        return deploy_out

    result = deploy_out.get("result") or {}
    legs = list(result.get("legs") or [])
    placeable = [leg for leg in legs if abs(int(leg.get("qty") or 0)) > 0]
    if not placeable:
        err = "No placeable legs found in deploy result."
        return {
            "ok": False,
            "error": err,
            "text": (deploy_out.get("text") or "") + "\n\n" + err,
            "deploy": deploy_out,
        }
    bad = [
        leg
        for leg in placeable
        if str(leg.get("status") or "").lower() != "filled" or not str(leg.get("symbol") or "").strip()
    ]
    if bad:
        bits = [f"{leg.get('key')}[{leg.get('status')}]" for leg in bad]
        err = "Sync not started because one or more legs were not fully filled: " + ", ".join(bits)
        return {
            "ok": False,
            "error": err,
            "text": (deploy_out.get("text") or "") + "\n\n" + err,
            "deploy": deploy_out,
        }

    ok_v, verr = _verify_directional_fills_in_book(placeable, snapshot)
    if not ok_v:
        err = str(verr or "Broker book verify failed after deploy.")
        return {
            "ok": False,
            "error": err,
            "text": (deploy_out.get("text") or "") + "\n\n" + err,
            "deploy": deploy_out,
            "verify_failed": True,
        }

    # Arm Kavach (Register) so ATO is confirmed — stub-only left ATO BLOCKED.
    reg_payload, reg_err = _build_register_payload_from_directional(payload, result)
    if reg_err or not reg_payload:
        err = str(reg_err or "Could not build Register payload after deploy.")
        return {
            "ok": False,
            "error": err,
            "text": (deploy_out.get("text") or "") + "\n\n" + err,
            "deploy": deploy_out,
        }
    reg_out = register_batman(reg_payload)
    if not bool(reg_out.get("ok")):
        err = str(reg_out.get("error") or "Register failed after deploy.")
        return {
            "ok": False,
            "error": err,
            "text": (deploy_out.get("text") or "")
            + "\n\nDeploy completed, but Register failed: "
            + err,
            "deploy": deploy_out,
            "register": reg_out,
            "register_payload": reg_payload,
        }

    sync_out = sync_registered_lots_from_broker({"confirm": True})
    if not bool(sync_out.get("ok")):
        return {
            "ok": False,
            "error": str(sync_out.get("error") or "Sync lots failed."),
            "text": "\n\n".join(
                x
                for x in [
                    deploy_out.get("text"),
                    reg_out.get("text"),
                    sync_out.get("text") or sync_out.get("error"),
                ]
                if x
            ),
            "deploy": deploy_out,
            "register": reg_out,
            "sync": sync_out,
        }

    combined = "\n\n".join(
        x for x in [deploy_out.get("text"), reg_out.get("text"), sync_out.get("text")] if x
    )
    return {
        "ok": True,
        "text": combined,
        "detail_text": combined,
        "deploy": deploy_out,
        "register": reg_out,
        "sync": sync_out,
        "verify": {"ok": True},
    }
