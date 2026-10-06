"""Map Kavach / Kite / Dhan NIFTY names to Groww trading symbols.

Groww place-order uses the exchange trading symbol, for example
``NIFTY26SEP25000CE`` (year, month, strike, CE/PE). Weekly names keep the
Kite week code ``NIFTY26O0625000CE``. A rewrite that changes the expiry is refused.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from core.shoonya_instruments import (
    _KITE_MONTHLY,
    _KITE_WEEKLY_DATE,
    _MONTH_NAME,
    _MONTH_NUM,
    expiry_of_kavach_symbol,
    last_tuesday,
)
from core.zerodha_instruments import compact_nifty_symbol, parse_kite_weekly_expiry

logger = logging.getLogger("batman.groww.instruments")

_GROWW_MONTHLY = re.compile(
    r"^NIFTY(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{4,6})(CE|PE)$",
    re.I,
)
_GROWW_WEEKLY = _KITE_WEEKLY_DATE


def format_groww_monthly(expiry: date, strike: int, option_type: str) -> str:
    right = "PE" if str(option_type).upper() in {"PE", "PUT", "P"} else "CE"
    mon = _MONTH_NAME[expiry.month]
    return f"NIFTY{expiry.year % 100:02d}{mon}{int(strike)}{right}"


def expiry_of_groww_symbol(symbol: str) -> date | None:
    raw = (symbol or "").strip().upper()
    weekly = _GROWW_WEEKLY.fullmatch(raw)
    if weekly:
        return parse_kite_weekly_expiry(raw)
    monthly = _GROWW_MONTHLY.fullmatch(raw)
    if monthly:
        return last_tuesday(2000 + int(monthly.group(1)), _MONTH_NUM[monthly.group(2).upper()])
    return None


def groww_tsym_from_any(symbol: str) -> str:
    raw = (symbol or "").strip()
    if not raw:
        return raw
    upper = raw.upper()
    if expiry_of_groww_symbol(upper):
        return upper
    compact = compact_nifty_symbol(raw)
    if _GROWW_WEEKLY.fullmatch(compact):
        return compact
    if _GROWW_MONTHLY.fullmatch(compact) or _KITE_MONTHLY.fullmatch(compact):
        return compact.upper()
    src = expiry_of_kavach_symbol(raw)
    if src is None:
        return raw
    weekly = parse_kite_weekly_expiry(compact)
    parsed_strike = None
    parsed_right = None
    week_match = _KITE_WEEKLY_DATE.fullmatch(compact)
    month_match = _KITE_MONTHLY.fullmatch(compact)
    custom = re.fullmatch(
        r"NIFTY\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4,6})\s+(CALL|PUT|CE|PE)",
        " ".join(upper.split()),
    )
    if week_match:
        parsed_strike = int(week_match.group(4))
        parsed_right = week_match.group(5)
    elif month_match:
        parsed_strike = int(month_match.group(3))
        parsed_right = month_match.group(4)
    elif custom:
        parsed_strike = int(custom.group(3))
        parsed_right = custom.group(4)
    if parsed_strike is None:
        return raw
    if weekly is not None and weekly != last_tuesday(weekly.year, weekly.month):
        return compact if _KITE_WEEKLY_DATE.fullmatch(compact) else format_groww_monthly(src, parsed_strike, parsed_right)
    return format_groww_monthly(src, parsed_strike, parsed_right)



_groww_cache: set[str] | None = None


def load_groww_master(force: bool = False) -> set[str]:
    """Groww instrument file trading symbols."""
    global _groww_cache
    if _groww_cache is not None and not force:
        return _groww_cache
    import urllib.request

    url = "https://growwapi-assets.groww.in/instruments/instrument.csv"
    symbols: set[str] = set()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text_csv = resp.read().decode("utf-8", "replace")
        for line in text_csv.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) > 2:
                sym = parts[2].strip().upper()
                if sym:
                    symbols.add(sym)
    except Exception as exc:
        logger.warning("Groww instrument file unavailable: %s", exc)
    _groww_cache = symbols
    return symbols


def map_groww_order_symbol(symbol: str, master: set[str] | None = None) -> str:
    mapped = groww_tsym_from_any(symbol)
    src = expiry_of_kavach_symbol(symbol)
    dst = expiry_of_groww_symbol(mapped)
    if src is not None and dst is not None and src != dst:
        raise ValueError(f"Refusing to rewrite {symbol} -> {mapped} (expiry {src} vs {dst})")
    book = master if master is not None else load_groww_master()
    if book and mapped.upper() not in book and mapped.upper().startswith("NIFTY"):
        raise ValueError(f"Refusing {symbol} -> {mapped}: not in the Groww instrument file")
    if mapped != (symbol or "").strip():
        logger.info("Groww symbol map %s -> %s", symbol, mapped)
    return mapped
