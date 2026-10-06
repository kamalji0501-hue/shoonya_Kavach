from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("datafeedbot.instruments")

_ID_COLS = ("SEM_SMST_SECURITY_ID", "SECURITY_ID", "security_id", "securityId")


@dataclass(frozen=True)
class ResolvedInstrument:
    security_id: str
    trading_symbol: str
    custom_symbol: str
    option_type: str
    strike: float
    expiry_label: str
    lot_size: int
    underlying: str
    exchange: str
    segment: str


def _row_security_id(row: dict[str, str]) -> str:
    for col in _ID_COLS:
        val = (row.get(col) or "").strip()
        if val:
            try:
                return str(int(float(val)))
            except ValueError:
                return val
    return ""


def _expiry_label(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw[:19] if len(raw) >= 19 else raw[:10], fmt)
            return dt.strftime("%d %b %Y")
        except ValueError:
            continue
    return raw


def _from_row(row: dict[str, str]) -> ResolvedInstrument | None:
    sid = _row_security_id(row)
    if not sid:
        return None
    opt = (row.get("SEM_OPTION_TYPE") or "").strip().upper()
    if opt in {"CALL", "C"}:
        opt = "CE"
    elif opt in {"PUT", "P"}:
        opt = "PE"
    try:
        strike = float(row.get("SEM_STRIKE_PRICE") or 0)
    except ValueError:
        strike = 0.0
    try:
        lot = int(float(row.get("SEM_LOT_UNITS") or 0))
    except ValueError:
        lot = 0
    underlying = (row.get("SM_SYMBOL_NAME") or "").strip()
    if not underlying:
        sym = row.get("SEM_TRADING_SYMBOL") or ""
        underlying = sym.split("-")[0].strip() if sym else ""
    return ResolvedInstrument(
        security_id=sid,
        trading_symbol=(row.get("SEM_TRADING_SYMBOL") or "").strip(),
        custom_symbol=(row.get("SEM_CUSTOM_SYMBOL") or "").strip(),
        option_type=opt,
        strike=strike,
        expiry_label=_expiry_label(row.get("SEM_EXPIRY_DATE") or ""),
        lot_size=lot,
        underlying=underlying.upper(),
        exchange=(row.get("SEM_EXM_EXCH_ID") or "").strip(),
        segment=(row.get("SEM_SEGMENT") or "D").strip(),
    )


def _is_nifty_index_option(inst: ResolvedInstrument) -> bool:
    """True for NIFTY index options (exclude BANKNIFTY / FINNIFTY / NIFTYNXT50)."""
    u = (inst.underlying or "").upper()
    sym = (inst.trading_symbol or "").upper()
    custom = (inst.custom_symbol or "").upper()
    if u in {"BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}:
        return False
    if any(x in sym or x in custom for x in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")):
        return False
    if u == "NIFTY":
        return True
    # SM_SYMBOL_NAME often blank for OPTIDX — trading symbol NIFTY-Aug2026-24450-CE
    return sym.startswith("NIFTY-") or custom.startswith("NIFTY ")


@lru_cache(maxsize=4)
def _load_rows(csv_path: str) -> tuple[ResolvedInstrument, ...]:
    path = Path(csv_path)
    out: list[ResolvedInstrument] = []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Drop unnamed pandas index column if present
            clean = {k: (v or "") for k, v in row.items() if k and k.strip()}
            inst = _from_row(clean)
            if (
                inst
                and inst.option_type in {"CE", "PE"}
                and _is_nifty_index_option(inst)
            ):
                # Normalize underlying for keys
                if inst.underlying != "NIFTY":
                    inst = ResolvedInstrument(
                        security_id=inst.security_id,
                        trading_symbol=inst.trading_symbol,
                        custom_symbol=inst.custom_symbol,
                        option_type=inst.option_type,
                        strike=inst.strike,
                        expiry_label=inst.expiry_label,
                        lot_size=inst.lot_size,
                        underlying="NIFTY",
                        exchange=inst.exchange,
                        segment=inst.segment,
                    )
                out.append(inst)
    logger.info("Loaded %d NIFTY option rows from %s", len(out), path)
    return tuple(out)


def _parse_expiry_sort_key(label: str) -> datetime:
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(label.strip()[:11], fmt)
        except ValueError:
            continue
    return datetime.max


def resolve_dhan(
    csv_path: Path,
    *,
    strike: int,
    option_type: str,
    expiry: str = "",
) -> ResolvedInstrument | None:
    opt = option_type.upper().strip()
    rows = _load_rows(str(csv_path))
    candidates = [
        r
        for r in rows
        if int(r.strike) == int(strike) and r.option_type == opt
    ]
    if expiry:
        exp = expiry.strip().lower()
        narrowed = [
            r
            for r in candidates
            if exp in r.expiry_label.lower() or exp in (r.trading_symbol or "").lower()
        ]
        if narrowed:
            candidates = narrowed
    if not candidates:
        return None
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    def sort_key(r: ResolvedInstrument) -> tuple[int, datetime]:
        dt = _parse_expiry_sort_key(r.expiry_label)
        # Prefer future/today expiries; expired go last
        past = 1 if dt.date() < today.date() else 0
        return (past, dt)

    candidates.sort(key=sort_key)
    return candidates[0]


def resolve_by_security_id(csv_path: Path, security_id: str) -> ResolvedInstrument | None:
    sid = str(security_id).strip()
    for r in _load_rows(str(csv_path)):
        if r.security_id == sid:
            return r
    return resolve_any_by_security_id(csv_path, sid)


def dhan_feed_segment(exchange: str, segment: str = "") -> tuple[int, str]:
    """Map instrument master exchange → Dhan MarketFeed code + REST/WS segment."""
    exch = (exchange or "").strip().upper()
    seg = (segment or "").strip().upper()
    if seg in {"I", "INDEX", "IDX_I"} or exch in {"IDX", "INDEX"}:
        return 0, "IDX_I"
    if exch == "MCX" or seg in {"M", "MCX", "MCX_COMM"}:
        return 5, "MCX_COMM"
    return 2, "NSE_FNO"


@lru_cache(maxsize=4)
def _load_all_by_sid(csv_path: str) -> dict[str, ResolvedInstrument]:
    path = Path(csv_path)
    out: dict[str, ResolvedInstrument] = {}
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            clean = {k: (v or "") for k, v in row.items() if k and k.strip()}
            inst = _from_row(clean)
            if inst:
                out[inst.security_id] = inst
    logger.info("Indexed %d instruments by SID from %s", len(out), path)
    return out


def resolve_any_by_security_id(
    csv_path: Path, security_id: str
) -> ResolvedInstrument | None:
    sid = str(security_id).strip()
    if not sid:
        return None
    try:
        sid = str(int(float(sid)))
    except ValueError:
        pass
    return _load_all_by_sid(str(csv_path)).get(sid)


class ZerodhaInstrumentCache:
    """Resolve Zerodha instrument_token from kite instruments dump / API list."""

    def __init__(self) -> None:
        self._by_key: dict[str, int] = {}
        self._by_strike: dict[str, list[tuple[datetime, int, str]]] = {}

    def load_from_rows(self, rows: list[dict]) -> None:
        # Expected kite instruments dict fields: instrument_token, name, strike, instrument_type, expiry, tradingsymbol
        self._by_key.clear()
        self._by_strike.clear()
        for row in rows:
            name = str(row.get("name") or "").upper()
            if name != "NIFTY":
                continue
            itype = str(row.get("instrument_type") or "").upper()
            if itype not in {"CE", "PE"}:
                continue
            try:
                strike = int(float(row.get("strike") or 0))
                token = int(row.get("instrument_token"))
            except (TypeError, ValueError):
                continue
            key = f"NIFTY_{strike}_{itype}"
            exp = str(row.get("expiry") or "")
            exp_dt = _parse_expiry_sort_key(exp) if exp else datetime.max
            self._by_strike.setdefault(key, []).append((exp_dt, token, exp))
            if exp:
                self._by_key[f"{key}_{exp}"] = token
                # also index Dhan-style "25 Aug 2026"
                try:
                    label = exp_dt.strftime("%d %b %Y")
                    self._by_key[f"{key}_{label}"] = token
                    self._by_key[f"{key}_{label.upper()}"] = token
                except Exception:
                    pass
        # default key = nearest future expiry (not arbitrary first row)
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for key, items in self._by_strike.items():
            items.sort(key=lambda x: (0 if x[0] >= today else 1, x[0]))
            self._by_key[key] = items[0][1]

    def resolve(self, strike: int, option_type: str, expiry: str = "") -> int | None:
        opt = option_type.upper().strip()
        key = f"NIFTY_{int(strike)}_{opt}"
        if expiry:
            exp = expiry.strip()
            for cand in (
                f"{key}_{exp}",
                f"{key}_{exp.upper()}",
                f"{key}_{exp.title()}",
            ):
                tok = self._by_key.get(cand)
                if tok:
                    return tok
            # parse and nearest-match
            want = _parse_expiry_sort_key(exp)
            items = self._by_strike.get(key) or []
            if items and want != datetime.max:
                best = min(items, key=lambda x: abs((x[0] - want).days))
                if abs((best[0] - want).days) <= 2:
                    return best[1]
        return self._by_key.get(key)
