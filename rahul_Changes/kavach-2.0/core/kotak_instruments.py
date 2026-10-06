"""Resolve a Kavach NIFTY name to Kotak's pTrdSymbol from the scrip master.

The string is not invented. ``finder`` must return the master row's trading
symbol for that expiry, strike, and CE/PE, or the order is refused.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Callable

from core.shoonya_instruments import _KITE_MONTHLY, _KITE_WEEKLY_DATE, expiry_of_kavach_symbol
from core.zerodha_instruments import compact_nifty_symbol

logger = logging.getLogger("batman.kotak.instruments")

Finder = Callable[[date, int, str], str]


def _strike_right(symbol: str) -> tuple[int, str] | None:
    compact = compact_nifty_symbol(symbol)
    weekly = _KITE_WEEKLY_DATE.fullmatch(compact)
    if weekly:
        return int(weekly.group(4)), "PE" if weekly.group(5).upper() == "PE" else "CE"
    monthly = _KITE_MONTHLY.fullmatch(compact)
    if monthly:
        return int(monthly.group(3)), "PE" if monthly.group(4).upper() == "PE" else "CE"
    custom = re.fullmatch(
        r"NIFTY\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4,6})\s+(CALL|PUT|CE|PE)",
        " ".join((symbol or "").upper().split()),
    )
    if custom:
        right = custom.group(4).upper()
        return int(custom.group(3)), "PE" if right in {"PE", "PUT"} else "CE"
    return None


def map_kotak_order_symbol(symbol: str, finder: Finder | None) -> str:
    exp = expiry_of_kavach_symbol(symbol)
    parsed = _strike_right(symbol)
    if exp is None or parsed is None:
        raise ValueError(f"Refusing Kotak order for {symbol}: expiry or strike is unknown")
    if finder is None:
        raise ValueError(f"Refusing Kotak order for {symbol}: scrip master lookup is not set")
    strike, right = parsed
    found = str(finder(exp, strike, right) or "").strip()
    if not found:
        raise ValueError(
            f"Refusing Kotak order for {symbol}: no nse_fo row for {exp.strftime('%d%b%Y').upper()} {strike} {right}"
        )
    logger.info("Kotak symbol map %s -> %s", symbol, found)
    return found
