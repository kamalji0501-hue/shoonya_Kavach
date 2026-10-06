"""Shoonya NFO symbol master → token lookup for NIFTY options."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime
from functools import lru_cache
from typing import Any
from urllib.request import Request, urlopen

logger = logging.getLogger("datafeedbot.brokers.shoonya.instruments")

_NFO_ZIP = "https://api.shoonya.com/NFO_symbols.txt.zip"
_NSE_ZIP = "https://api.shoonya.com/NSE_symbols.txt.zip"
NIFTY_INDEX_TOKEN = "26000"


def _parse_expiry(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return raw


@lru_cache(maxsize=2)
def load_nfo_rows() -> tuple[dict[str, Any], ...]:
    try:
        req = Request(_NFO_ZIP, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=60) as resp:
            blob = resp.read()
        raw = zipfile.ZipFile(io.BytesIO(blob)).read("NFO_symbols.txt").decode("utf-8", "replace")
    except Exception as exc:
        logger.warning("Shoonya NFO master download failed: %s", exc)
        return tuple()
    lines = raw.splitlines()
    if not lines:
        return tuple()
    headers = [h.strip() for h in lines[0].split(",")]
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < len(headers):
            continue
        row = {headers[i]: parts[i].strip() for i in range(len(headers))}
        sym = (row.get("Symbol") or row.get("symbol") or "").upper()
        if sym != "NIFTY":
            continue
        # Option rows have OptionType / StrikePrice
        opt = (row.get("OptionType") or row.get("optiontype") or "").upper()
        if opt in {"CE", "PE", "CA", "PA"}:
            if opt == "CA":
                opt = "CE"
            if opt == "PA":
                opt = "PE"
        else:
            continue
        try:
            strike = float(row.get("StrikePrice") or row.get("strikeprice") or 0)
        except ValueError:
            continue
        token = str(row.get("Token") or row.get("token") or "").strip()
        tsym = str(row.get("TradingSymbol") or row.get("tradingsymbol") or "").strip()
        expiry = _parse_expiry(row.get("Expiry") or row.get("expiry") or "")
        if not token or strike <= 0:
            continue
        rows.append(
            {
                "token": token,
                "tsym": tsym.upper(),
                "option_type": opt,
                "strike": int(strike) if float(strike).is_integer() else strike,
                "expiry": expiry,
                "exch": "NFO",
            }
        )
    logger.info("Shoonya NFO NIFTY options loaded count=%s", len(rows))
    return tuple(rows)


def resolve_nifty_option(strike: int, option_type: str, expiry: str = "") -> dict[str, Any] | None:
    opt = (option_type or "CE").upper()
    if opt in {"CALL", "C"}:
        opt = "CE"
    if opt in {"PUT", "P"}:
        opt = "PE"
    want_exp = (expiry or "").strip().upper()
    hits = [
        r
        for r in load_nfo_rows()
        if int(r["strike"]) == int(strike) and r["option_type"] == opt
    ]
    if not hits:
        return None

    def _exp_dt(raw: str):
        raw = (raw or "").strip()
        for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    if want_exp:
        # ISO yyyy-mm-dd → normalize to master label style
        want_dt = _exp_dt(want_exp) or _exp_dt(want_exp.title())
        for r in hits:
            label = str(r.get("expiry") or "")
            if want_exp in label.upper().replace("  ", " "):
                return r
            r_dt = _exp_dt(label)
            if want_dt and r_dt and want_dt.date() == r_dt.date():
                return r
            compact = "".join(ch for ch in label.upper() if ch.isalnum())
            want_c = "".join(ch for ch in want_exp if ch.isalnum())
            if want_c and want_c == compact:
                return r
        # explicit expiry given but no match — do not silently return wrong week
        return None

    # No expiry: nearest upcoming (not arbitrary master-file first row, e.g. Nov)
    today = datetime.utcnow().date()
    dated = []
    for r in hits:
        dt = _exp_dt(str(r.get("expiry") or ""))
        if dt is not None:
            dated.append((dt.date(), r))
    upcoming = sorted((d, r) for d, r in dated if d >= today)
    if upcoming:
        return upcoming[0][1]
    if dated:
        return sorted(dated, key=lambda x: x[0])[-1][1]
    return hits[0]
