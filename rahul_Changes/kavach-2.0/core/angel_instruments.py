"""Map Kavach NIFTY names to Angel tradingsymbol plus a scrip-master token.

Angel place-order needs both. A typical option name is ``NIFTY29SEP2625000CE``.
If the expiry does not match, the order is refused before send.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from core.shoonya_instruments import _MONTH_NAME, expiry_of_kavach_symbol
from core.zerodha_instruments import compact_nifty_symbol

logger = logging.getLogger("batman.angel.instruments")

_ANGEL = re.compile(
    r"^NIFTY(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(\d{4,6})(CE|PE)$",
    re.I,
)


def format_angel_tsym(expiry: date, strike: int, option_type: str) -> str:
    right = "PE" if str(option_type).upper() in {"PE", "PUT", "P"} else "CE"
    mon = _MONTH_NAME[expiry.month]
    return f"NIFTY{expiry.day:02d}{mon}{expiry.year % 100:02d}{int(strike)}{right}"


def expiry_of_angel_tsym(symbol: str) -> date | None:
    match = _ANGEL.fullmatch((symbol or "").strip().upper())
    if not match:
        return None
    return date(2000 + int(match.group(3)), _month(match.group(2)), int(match.group(1)))


def _month(name: str) -> int:
    from core.shoonya_instruments import _MONTH_NUM

    return _MONTH_NUM[name.upper()]


def _strike_right(symbol: str) -> tuple[int, str] | None:
    from core.shoonya_instruments import _KITE_MONTHLY, _KITE_WEEKLY_DATE

    compact = compact_nifty_symbol(symbol)
    weekly = _KITE_WEEKLY_DATE.fullmatch(compact)
    if weekly:
        return int(weekly.group(4)), weekly.group(5).upper()
    monthly = _KITE_MONTHLY.fullmatch(compact)
    if monthly:
        return int(monthly.group(3)), monthly.group(4).upper()
    custom = re.fullmatch(
        r"NIFTY\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4,6})\s+(CALL|PUT|CE|PE)",
        " ".join((symbol or "").upper().split()),
    )
    if custom:
        return int(custom.group(3)), custom.group(4).upper()
    already = _ANGEL.fullmatch((symbol or "").strip().upper())
    if already:
        return int(already.group(4)), already.group(5).upper()
    return None


def angel_tsym_from_any(symbol: str) -> str:
    raw = (symbol or "").strip()
    if not raw:
        return raw
    if _ANGEL.fullmatch(raw.upper()):
        return raw.upper()
    exp = expiry_of_kavach_symbol(raw)
    parsed = _strike_right(raw)
    if exp is None or parsed is None:
        return raw
    strike, right = parsed
    return format_angel_tsym(exp, strike, right)


def map_angel_order_symbol(symbol: str) -> str:
    mapped = angel_tsym_from_any(symbol)
    src = expiry_of_kavach_symbol(symbol)
    dst = expiry_of_angel_tsym(mapped)
    if src is not None and dst is not None and src != dst:
        raise ValueError(f"Refusing to rewrite {symbol} -> {mapped} (expiry {src} vs {dst})")
    if mapped != (symbol or "").strip():
        logger.info("Angel symbol map %s -> %s", symbol, mapped)
    return mapped


_token_cache: dict[str, str] | None = None


def load_angel_master(force: bool = False) -> dict[str, str]:
    """Public Angel scrip master: trading symbol -> token."""
    global _token_cache
    if _token_cache is not None and not force:
        return _token_cache
    import json
    import urllib.request

    url = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
    out: dict[str, str] = {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            rows = json.loads(resp.read().decode("utf-8", "replace"))
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").upper()
            token = str(row.get("token") or "")
            if sym and token:
                out[sym] = token
    except Exception as exc:
        logger.warning("Angel scrip master unavailable: %s", exc)
    _token_cache = out
    return out


def lookup_angel_token(tsym: str, master: dict[str, str] | None = None) -> str:
    """Return the scrip-master token. Pass a dict to test without downloading."""
    book = load_angel_master() if master is None else master
    return str(book.get((tsym or "").upper()) or "")
