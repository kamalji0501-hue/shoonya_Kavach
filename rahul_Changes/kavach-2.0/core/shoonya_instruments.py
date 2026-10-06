"""Map Kavach / Kite / Dhan NIFTY option names to Shoonya trading symbols.

Shoonya NFO master uses ``NIFTY29SEP26C25000`` (day, month, year, C/P, strike).
Kavach still speaks ``NIFTY 29 SEP 25000 CALL`` or Kite compact ``NIFTY2690824000CE``.
This is the same rewrite Dhan does in dhan_instruments.py, aimed at Shoonya's tsym.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from core.zerodha_instruments import compact_nifty_symbol, parse_kite_weekly_expiry

logger = logging.getLogger("batman.shoonya.instruments")

_MONTH_NUM = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MONTH_NAME = {v: k for k, v in _MONTH_NUM.items()}

_DHAN_CUSTOM = re.compile(
    r"^NIFTY\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4,6})\s+(CALL|PUT|CE|PE)$",
    re.I,
)
_KITE_MONTHLY = re.compile(
    r"^NIFTY(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{4,6})(CE|PE)$",
    re.I,
)
_SHOONYA = re.compile(
    r"^NIFTY(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})([CP])(\d{4,6})$",
    re.I,
)
_KITE_WEEKLY_DATE = re.compile(r"^NIFTY(\d{2})([1-9OND])(\d{2})(\d{4,5})(CE|PE)$", re.I)
_DHAN_DASH_DAY = re.compile(
    r"^NIFTY-(\d{1,2})([A-Za-z]{3})(\d{4})-(\d{4,5})-(CE|PE)$",
    re.I,
)
_DHAN_DASH_MONTH = re.compile(
    r"^NIFTY-([A-Za-z]{3})(\d{4})-(\d{4,5})-(CE|PE)$",
    re.I,
)

_INDEX_PASS = {"NIFTY", "NIFTY50", "NIFTY 50", "NSE:NIFTY 50", "INDIA VIX", "SENSEX"}


def last_tuesday(year: int, month: int) -> date:
    """NSE Nifty monthly expiry is the last Tuesday (holiday shifts checked later)."""
    if month == 12:
        day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        day = date(year, month + 1, 1) - timedelta(days=1)
    while day.weekday() != 1:
        day -= timedelta(days=1)
    return day


def _right_letter(raw: str) -> str:
    tok = str(raw or "").strip().upper()
    if tok in {"PE", "PUT", "P"}:
        return "P"
    return "C"


def format_shoonya_tsym(expiry: date, strike: int, option_type: str) -> str:
    mon = _MONTH_NAME[expiry.month]
    yy = expiry.year % 100
    return f"NIFTY{expiry.day:02d}{mon}{yy:02d}{_right_letter(option_type)}{int(strike)}"


def expiry_of_shoonya_tsym(symbol: str) -> date | None:
    match = _SHOONYA.fullmatch((symbol or "").strip().upper())
    if not match:
        return None
    day = int(match.group(1))
    month = _MONTH_NUM[match.group(2).upper()]
    year = 2000 + int(match.group(3))
    return date(year, month, day)


def expiry_of_kavach_symbol(symbol: str) -> date | None:
    """Expiry implied by a Kavach, Kite, Dhan, or Shoonya name. None if unknown."""
    raw = (symbol or "").strip()
    if not raw:
        return None
    shoonya = expiry_of_shoonya_tsym(raw)
    if shoonya is not None:
        return shoonya
    custom = _DHAN_CUSTOM.fullmatch(" ".join(raw.upper().split()))
    if custom:
        day = int(custom.group(1))
        month = _MONTH_NUM[custom.group(2).upper()]
        year = date.today().year
        # Dhan custom has no year. Weekly names in the current book are this year
        # or next year when the month has already passed.
        guess = date(year, month, day)
        if guess < date.today().replace(day=1):
            guess = date(year + 1, month, day)
        return guess
    compact = compact_nifty_symbol(raw)
    weekly = parse_kite_weekly_expiry(compact)
    if weekly is not None:
        return weekly
    monthly = _KITE_MONTHLY.fullmatch(compact)
    if monthly:
        year = 2000 + int(monthly.group(1))
        month = _MONTH_NUM[monthly.group(2).upper()]
        return last_tuesday(year, month)
    return None


def _strike_right_from_compact(compact: str) -> tuple[int, str] | None:
    weekly = _KITE_WEEKLY_DATE.fullmatch(compact)
    if weekly:
        return int(weekly.group(4)), weekly.group(5).upper()
    monthly = _KITE_MONTHLY.fullmatch(compact)
    if monthly:
        return int(monthly.group(3)), monthly.group(4).upper()
    return None


def shoonya_tsym_from_any(symbol: str) -> str:
    """Return a Shoonya NFO trading symbol, or the original if this is not NIFTY F&O."""
    raw = (symbol or "").strip()
    if not raw:
        return raw
    if raw.upper() in _INDEX_PASS:
        return "NIFTY 50" if "NIFTY" in raw.upper() and "VIX" not in raw.upper() else raw.upper()

    already = _SHOONYA.fullmatch(raw.upper())
    if already:
        return raw.upper()

    # Day-specific Dhan trading: NIFTY-06Oct2026-22950-CE
    dash_day = _DHAN_DASH_DAY.fullmatch(raw.replace(" ", ""))
    if dash_day:
        day = int(dash_day.group(1))
        month = _MONTH_NUM[dash_day.group(2).upper()]
        year = int(dash_day.group(3))
        strike = int(dash_day.group(4))
        return format_shoonya_tsym(date(year, month, day), strike, dash_day.group(5))

    # Month-only Dhan: NIFTY-Oct2026-22950-CE — ambiguous (weekly vs monthly).
    # Do not guess; callers must pass a day-specific symbol (Shoonya tsym).
    if _DHAN_DASH_MONTH.fullmatch(raw.replace(" ", "")):
        return raw

    custom = _DHAN_CUSTOM.fullmatch(" ".join(raw.upper().split()))
    if custom:
        exp = expiry_of_kavach_symbol(raw)
        if exp is None:
            return raw
        return format_shoonya_tsym(exp, int(custom.group(3)), custom.group(4))

    compact = compact_nifty_symbol(raw)
    weekly = parse_kite_weekly_expiry(compact)
    parsed = _strike_right_from_compact(compact)
    if weekly is not None and parsed:
        strike, right = parsed
        return format_shoonya_tsym(weekly, strike, right)

    monthly = _KITE_MONTHLY.fullmatch(compact)
    if monthly and parsed:
        year = 2000 + int(monthly.group(1))
        month = _MONTH_NUM[monthly.group(2).upper()]
        strike, right = parsed
        return format_shoonya_tsym(last_tuesday(year, month), strike, right)

    return raw



_shoonya_cache: set[str] | None = None


def load_shoonya_master(force: bool = False) -> set[str]:
    """Shoonya NFO master trading symbols."""
    global _shoonya_cache
    if _shoonya_cache is not None and not force:
        return _shoonya_cache
    import io
    import urllib.request
    import zipfile

    url = "https://api.shoonya.com/NFO_symbols.txt.zip"
    symbols: set[str] = set()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            blob = resp.read()
        raw = zipfile.ZipFile(io.BytesIO(blob)).read("NFO_symbols.txt").decode("utf-8", "replace")
        for line in raw.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) > 4:
                symbols.add(parts[4].strip().upper())
    except Exception as exc:
        logger.warning("Shoonya symbol file unavailable: %s", exc)
    _shoonya_cache = symbols
    return symbols


def map_shoonya_order_symbol(symbol: str, master: set[str] | None = None) -> str:
    """Map for orders and quotes. Refuses a rewrite that changes the expiry."""
    mapped = shoonya_tsym_from_any(symbol)
    src = expiry_of_kavach_symbol(symbol)
    dst = expiry_of_shoonya_tsym(mapped)
    if src is not None and dst is not None and src != dst:
        raise ValueError(f"Refusing to rewrite {symbol} -> {mapped} (expiry {src} vs {dst})")
    book = master if master is not None else load_shoonya_master()
    if book and mapped.upper().startswith("NIFTY") and mapped.upper() not in book:
        raise ValueError(f"Refusing {symbol} -> {mapped}: not in the Shoonya symbol file")
    if mapped != (symbol or "").strip():
        logger.info("Shoonya symbol map %s -> %s", symbol, mapped)
    return mapped
