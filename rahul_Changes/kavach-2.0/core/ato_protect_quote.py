"""ATO protect premium: disk JWT + Dhan securityId, cached, no Tradehull.

The live punch used to die because Tradehull get_ltp trips the circuit breaker
and marketfeed bursts return 401/429. This module keeps a last-good LTP so a
breach can still price (or fall back) without hammering Dhan.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("batman.ato_protect_quote")

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, float]] = {}
_SID: dict[str, int] = {}
_LAST_FETCH = 0.0
_MIN_INTERVAL = 8.0
_TOKEN_PATHS = (
    Path("/home/ubuntu/Trading_Runtime_Rahul/Data/data/shared/access_token.json"),
    Path("/home/ubuntu/Trading_Runtime_Rahul/data/shared/access_token.json"),
    Path("/home/ubuntu/rahul_Changes/kavach-2.0/data/access_token.json"),
    Path("/home/ubuntu/Datafeedbot_Runtime/Data/shared/access_token.json"),
)


def remember(symbol: str, ltp: float) -> None:
    try:
        px = float(ltp)
    except (TypeError, ValueError):
        return
    if px <= 0:
        return
    with _LOCK:
        _CACHE[str(symbol)] = (px, time.time())


def last(symbol: str, max_age_s: float = 300.0) -> float | None:
    with _LOCK:
        hit = _CACHE.get(str(symbol))
    if not hit:
        return None
    px, ts = hit
    if px <= 0:
        return None
    if max_age_s > 0 and (time.time() - ts) > float(max_age_s):
        return None
    return float(px)


def _load_disk_token() -> tuple[str, str]:
    client = ""
    token = ""
    try:
        from dotenv import dotenv_values

        vals = dict(dotenv_values("/home/ubuntu/Trading_Runtime_Rahul/Credentials/config/dhan.env"))
        client = str(vals.get("DHAN_CLIENT_CODE") or vals.get("DHAN_CLIENT_ID") or "").strip()
    except Exception:
        pass
    newest: tuple[float, str] | None = None
    for path in _TOKEN_PATHS:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            tok = str((data or {}).get("access_token") or "").strip()
            if not tok:
                continue
            mtime = path.stat().st_mtime
            if newest is None or mtime > newest[0]:
                newest = (mtime, tok)
        except Exception:
            continue
    if newest:
        token = newest[1]
    return client, token


def _creds(broker: Any | None) -> tuple[str, str]:
    disk_c, disk_t = _load_disk_token()
    br_c = str(getattr(broker, "_client_code", "") or "").strip() if broker is not None else ""
    br_t = str(getattr(broker, "_access_token", "") or "").strip() if broker is not None else ""
    return (br_c or disk_c), (disk_t or br_t)


def _security_id(symbol: str) -> int | None:
    key = str(symbol)
    with _LOCK:
        if key in _SID:
            return _SID[key]
    import re

    m = re.search(r"-(\d{4,5})-(CE|PE)$", key, re.I)
    if not m:
        return None
    strike, opt = int(m.group(1)), m.group(2).upper()
    if not strike or not opt:
        return None
    try:
        from backtest_engine.resolver.instrument_master import (
            load_instrument_master,
            resolve_nifty_option,
        )
        from core.nifty_option_expiry import resolve_symbol_expiry_date

        exp = resolve_symbol_expiry_date(key)
        if exp is None:
            return None
        inst = resolve_nifty_option(
            strike=int(strike),
            option_type=str(opt),
            expiry_date=exp,
            master=load_instrument_master(),
        )
        sid = int(inst.security_id)
        if sid > 0:
            with _LOCK:
                _SID[key] = sid
            return sid
    except Exception as exc:
        logger.debug("ATO protect securityId failed for %s: %s", symbol, exc)
    return None


def quote_protect_ltp(
    symbol: str,
    broker: Any | None = None,
    *,
    force: bool = False,
    max_stale_s: float = 180.0,
) -> float | None:
    """Return a usable protect LTP. Fresh quote when possible, else last-good."""
    sym = str(symbol or "").strip()
    if not sym:
        return None
    cached = last(sym, max_age_s=12.0 if not force else 0.0)
    if cached is not None and not force:
        return cached

    global _LAST_FETCH
    now = time.time()
    with _LOCK:
        too_soon = (now - _LAST_FETCH) < _MIN_INTERVAL
    if too_soon and not force:
        return last(sym, max_age_s=max_stale_s)

    sid = _security_id(sym)
    client, token = _creds(broker)
    px = None
    if sid and client and token:
        try:
            from core.dhan_market_quote import fetch_fno_ltp_rest

            with _LOCK:
                _LAST_FETCH = time.time()
            prices = fetch_fno_ltp_rest(client, token, [sid])
            raw = (prices or {}).get(sid) or (prices or {}).get(int(sid))
            if raw is not None and float(raw) > 0:
                px = float(raw)
        except Exception as exc:
            msg = str(exc)
            logger.warning("ATO protect REST LTP failed %s: %s", sym, exc)
            if "401" in msg or "DH-906" in msg or "Invalid Token" in msg or "Authentication" in msg:
                try:
                    from core.dhan_totp import load_credentials, renew_and_save

                    creds = load_credentials()
                    if getattr(creds, "configured", False):
                        token = renew_and_save()
                        from core.dhan_market_quote import fetch_fno_ltp_rest

                        prices = fetch_fno_ltp_rest(client, token, [sid])
                        raw = (prices or {}).get(sid)
                        if raw is not None and float(raw) > 0:
                            px = float(raw)
                except Exception as exc2:
                    logger.warning("ATO protect remint/retry failed: %s", exc2)

    if px is not None:
        remember(sym, px)
        return px
    return last(sym, max_age_s=max_stale_s)


def prewarm_from_state(state: Any, broker: Any | None = None) -> None:
    """Keep CE/PE protect premiums warm while ATO is armed."""
    if state is None:
        return
    try:
        get = state.get
    except Exception:
        return
    for key in ("ato.ce_protect_symbol", "ato.pe_protect_symbol"):
        try:
            sym = str(get(key) or "").strip()
        except Exception:
            sym = ""
        if not sym:
            continue
        if last(sym, max_age_s=10.0) is not None:
            continue
        try:
            quote_protect_ltp(sym, broker, force=False)
        except Exception as exc:
            logger.debug("ATO prewarm %s failed: %s", sym, exc)
