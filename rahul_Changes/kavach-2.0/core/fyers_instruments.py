"""Map Kavach NIFTY names to Fyers v3 symbols and require a master-file hit.

Monthly rows in the live file look like ``NSE:NIFTY26SEP25000CE``.
Weekly names use the Kite week code with an NSE prefix, for example
``NSE:NIFTY26O0625000CE``. If that exact string is not in the master, refuse.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from core.groww_instruments import groww_tsym_from_any
from core.shoonya_instruments import expiry_of_kavach_symbol

logger = logging.getLogger("batman.fyers.instruments")

_MASTER_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"
_FYERS = re.compile(r"^NSE:NIFTY(\d{2})([A-Z0-9]{3,})(\d{4,6})(CE|PE)$", re.I)

_master_cache: set[str] | None = None


def fyers_symbol_from_any(symbol: str) -> str:
    raw = (symbol or "").strip()
    if raw.upper().startswith("NSE:"):
        return raw.upper()
    body = groww_tsym_from_any(raw)
    if body.upper().startswith("NIFTY"):
        return "NSE:" + body.upper()
    return raw


def load_fyers_master(force: bool = False) -> set[str]:
    """Download the Fyers NSE FO file once. Tests replace this function."""
    global _master_cache
    if _master_cache is not None and not force:
        return _master_cache
    import urllib.request

    symbols: set[str] = set()
    try:
        with urllib.request.urlopen(_MASTER_URL, timeout=20) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        logger.warning("Fyers symbol file unavailable: %s", exc)
        _master_cache = set()
        return _master_cache
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 10:
            continue
        sym = parts[9].strip().upper()
        if sym.startswith("NSE:"):
            symbols.add(sym)
    _master_cache = symbols
    return symbols


def map_fyers_order_symbol(symbol: str, master: set[str] | None = None) -> str:
    mapped = fyers_symbol_from_any(symbol)
    src = expiry_of_kavach_symbol(symbol)
    if src is None and not mapped.upper().startswith("NSE:NIFTY"):
        return mapped
    book = master if master is not None else load_fyers_master()
    if mapped.upper() not in {item.upper() for item in book}:
        raise ValueError(f"Refusing {symbol} -> {mapped}: not in the Fyers symbol file")
    if mapped != (symbol or "").strip():
        logger.info("Fyers symbol map %s -> %s", symbol, mapped)
    return mapped
