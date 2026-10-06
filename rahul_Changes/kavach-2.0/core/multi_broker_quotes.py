"""Nifty quotes from every broker that has a saved token.

The selected main broker is tried first. A broker with no token is skipped.
Dhan is not given a second websocket: its price stays on the existing feeder cache.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("batman.multi_broker_quotes")

_CACHE: dict[str, tuple[float, float]] = {}
_TTL = 2.0


def _fresh(name: str) -> float:
    hit = _CACHE.get(name)
    if not hit:
        return 0.0
    price, when = hit
    if time.time() - when > _TTL:
        return 0.0
    return price


def _store(name: str, price: float) -> float:
    if price > 0:
        _CACHE[name] = (price, time.time())
    return price


def session_ready(name: str, root: Path | None = None) -> bool:
    try:
        if name == "groww":
            from core.groww_credentials import load_session
            return bool(load_session(root).get("ok"))
        if name == "angel":
            from core.angel_credentials import load_session
            return bool(load_session(root).get("ok"))
        if name == "fyers":
            from core.fyers_credentials import load_session
            return bool(load_session(root).get("ok"))
        if name == "kotak":
            from core.kotak_credentials import load_session
            return bool(load_session(root).get("ok"))
        if name == "shoonya":
            from core.shoonya_credentials import load_session
            return bool(load_session(root).get("ok"))
        if name == "dhan":
            from core.order_broker_select import dhan_client_and_token
            _cc, tok = dhan_client_and_token(root)
            return bool(tok)
        if name == "zerodha":
            from core.zerodha_credentials import load_zerodha_order_creds
            return bool(load_zerodha_order_creds(root).ok)
    except Exception as exc:
        logger.debug("session check %s failed: %s", name, exc)
    return False


def brokers_with_tokens(root: Path | None = None) -> list[str]:
    from core.order_broker_select import get_main_order_broker

    main = get_main_order_broker(root)
    order = ["zerodha", "dhan", "shoonya", "groww", "angel", "fyers", "kotak"]
    if main in order:
        order = [main] + [name for name in order if name != main]
    return [name for name in order if session_ready(name, root)]


def _quote_broker(name: str, root: Path | None) -> float:
    cached = _fresh(name)
    if cached > 0:
        return cached
    if name in {"dhan", "zerodha"}:
        return 0.0
    if name == "shoonya":
        from core.shoonya_broker import ShoonyaBroker
        broker = ShoonyaBroker.connect(root=root)
        return _store(name, float(broker._ltp_rest("NIFTY") or 0))
    if name == "groww":
        from core.groww_broker import GrowwBroker
        return _store(name, float(GrowwBroker.connect(root=root)._rest_nifty() or 0))
    if name == "angel":
        from core.angel_broker import AngelBroker
        return _store(name, float(AngelBroker.connect(root=root)._rest_nifty() or 0))
    if name == "fyers":
        from core.fyers_broker import FyersBroker
        return _store(name, float(FyersBroker.connect(root=root)._rest_nifty() or 0))
    if name == "kotak":
        from core.kotak_broker import KotakBroker
        return _store(name, float(KotakBroker.connect(root=root)._rest_nifty() or 0))
    return 0.0


def nifty_ltp_from_saved_brokers(root: Path | None = None, skip: str = "") -> tuple[float, str]:
    for name in brokers_with_tokens(root):
        if name == skip:
            continue
        try:
            price = float(_quote_broker(name, root) or 0)
        except Exception as exc:
            logger.debug("quote %s failed: %s", name, exc)
            continue
        if price > 0:
            return price, name
    return 0.0, ""
