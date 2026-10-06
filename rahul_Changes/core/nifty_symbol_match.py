"""Match NIFTY option symbols across Kite / Dhan label formats."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

_MONTH_NUM = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MONTH_NAME = {v: k for k, v in _MONTH_NUM.items()}

_DHAN_DAY = re.compile(
    r"^NIFTY-(\d{1,2})([A-Za-z]{3})(\d{4})-(\d{4,5})-(CE|PE)$", re.I
)
_DHAN_MONTH = re.compile(
    r"^NIFTY-([A-Za-z]{3})(\d{4})-(\d{4,5})-(CE|PE)$", re.I
)
_DHAN_CUSTOM = re.compile(
    r"^NIFTY\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4,5})\s+(CALL|PUT|CE|PE)$", re.I
)
_DHAN_DISPLAY = re.compile(
    r"^NIFTY\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{4,5})\s+(CALL|PUT|CE|PE)$",
    re.I,
)
_KITE_WEEKLY = re.compile(
    r"^NIFTY(\d{2})([1-9OND])(\d{2})(\d{4,5})(CE|PE)$", re.I
)
_KITE_LETTER = {"O": 10, "N": 11, "D": 12}


def _opt(raw: str) -> str:
    tok = str(raw or "").strip().upper()
    if tok in {"PE", "PUT", "P"}:
        return "PE"
    return "CE"


def _parse_row_expiry(row: Any) -> date | None:
    if row is None or not hasattr(row, "get"):
        return None
    for key in ("drvExpiryDate", "expiry", "expiryDate", "ExpiryDate"):
        raw = row.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            if len(text) >= 10 and text[4] == "-":
                return date.fromisoformat(text[:10])
        except ValueError:
            pass
        try:
            from core.nifty_option_expiry import parse_expiry_label

            return parse_expiry_label(text)
        except Exception:
            continue
    return None


def parse_nifty_option_identity(
    symbol: str,
    *,
    row: Any = None,
) -> tuple[date | None, int | None, str | None]:
    """Return (expiry_or_None, strike, CE|PE) from a NIFTY option label."""
    raw = (symbol or "").strip()
    if not raw:
        return None, None, None
    compact = raw.replace(" ", "")

    m = _DHAN_DAY.fullmatch(compact)
    if m:
        return (
            date(int(m.group(3)), _MONTH_NUM[m.group(2).upper()], int(m.group(1))),
            int(m.group(4)),
            _opt(m.group(5)),
        )

    m = _DHAN_DISPLAY.fullmatch(" ".join(raw.split()))
    if m:
        return (
            date(int(m.group(3)), _MONTH_NUM[m.group(2).upper()], int(m.group(1))),
            int(m.group(4)),
            _opt(m.group(5)),
        )

    m = _DHAN_CUSTOM.fullmatch(" ".join(raw.upper().split()))
    if m:
        day = int(m.group(1))
        month = _MONTH_NUM[m.group(2).upper()]
        strike = int(m.group(3))
        opt = _opt(m.group(4))
        row_exp = _parse_row_expiry(row)
        if row_exp is not None:
            return date(row_exp.year, month, day), strike, opt
        today = date.today()
        year = today.year
        try:
            cand = date(year, month, day)
        except ValueError:
            cand = date(year, month, 1)
        if cand < (today - timedelta(days=45)):
            year += 1
        try:
            return date(year, month, day), strike, opt
        except ValueError:
            return None, strike, opt

    m = _KITE_WEEKLY.fullmatch(compact.upper())
    if m:
        yy = 2000 + int(m.group(1))
        mon_tok = m.group(2).upper()
        month = int(mon_tok) if mon_tok.isdigit() else _KITE_LETTER[mon_tok]
        return date(yy, month, int(m.group(3))), int(m.group(4)), _opt(m.group(5))

    m = _DHAN_MONTH.fullmatch(compact)
    if m:
        month = _MONTH_NUM[m.group(1).upper()]
        year = int(m.group(2))
        strike = int(m.group(3))
        opt = _opt(m.group(4))
        row_exp = _parse_row_expiry(row)
        if row_exp is not None and row_exp.year == year and row_exp.month == month:
            return row_exp, strike, opt
        return None, strike, opt

    tail = re.search(r"(\d{4,5})(CE|PE|CALL|PUT)$", compact, re.I)
    if tail:
        return _parse_row_expiry(row), int(tail.group(1)), _opt(tail.group(2))
    return None, None, None


def format_dhan_day_symbol(expiry: date, strike: int, opt: str) -> str:
    return f"NIFTY-{expiry.day:02d}{expiry.strftime('%b%Y')}-{int(strike)}-{_opt(opt)}"


def format_dhan_custom(expiry: date, strike: int, opt: str) -> str:
    right = "PUT" if _opt(opt) == "PE" else "CALL"
    return f"NIFTY {expiry.day:02d} {_MONTH_NAME[expiry.month]} {int(strike)} {right}"


def nifty_option_symbols_match(
    want: str,
    have: str,
    *,
    have_row: Any = None,
) -> bool:
    """True when want/have refer to the same NIFTY option contract."""
    a = (want or "").strip()
    b = (have or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if a.replace(" ", "").upper() == b.replace(" ", "").upper():
        return True

    we, ws, wo = parse_nifty_option_identity(a)
    he, hs, ho = parse_nifty_option_identity(b, row=have_row)
    if ws is None or hs is None or wo is None or ho is None:
        return False
    if int(ws) != int(hs) or wo != ho:
        return False
    if we is not None and he is not None:
        return we == he
    # Ambiguous month-only without day on either side — do not match.
    return False
