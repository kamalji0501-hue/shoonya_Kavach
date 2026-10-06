from __future__ import annotations

from dataclasses import dataclass

from datafeedbot.models import RawTick, Status
from datafeedbot.sequence import now_ms


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    status: str
    reason: str = ""


def validate_tick(
    tick: RawTick,
    *,
    expected_instrument: str | None = None,
    max_age_ms: int = 5000,
    require_bbo: bool = False,
) -> ValidationResult:
    if expected_instrument and tick.instrument != expected_instrument:
        return ValidationResult(False, Status.DATA_INVALID.value, "wrong_instrument")
    if tick.ltp is None or float(tick.ltp) <= 0:
        return ValidationResult(False, Status.DATA_INVALID.value, "bad_ltp")
    if tick.source not in {
        "DHAN_WS",
        "ZERODHA_WS",
        "SHOONYA_WS",
        "DHAN_REST",
        "ZERODHA_REST",
        "SHOONYA_REST",
    }:
        return ValidationResult(False, Status.DATA_INVALID.value, "bad_source")
    bid, ask = tick.bid, tick.ask
    if bid is not None and float(bid) < 0:
        return ValidationResult(False, Status.DATA_INVALID.value, "bad_bid")
    if ask is not None and float(ask) < 0:
        return ValidationResult(False, Status.DATA_INVALID.value, "bad_ask")
    if bid is not None and ask is not None and float(bid) > float(ask):
        return ValidationResult(False, Status.DATA_INVALID.value, "bid_gt_ask")
    if require_bbo and (bid is None or ask is None):
        return ValidationResult(False, Status.DATA_INVALID.value, "missing_bbo")
    recv = int(tick.received_timestamp or 0)
    if recv <= 0:
        return ValidationResult(False, Status.DATA_INVALID.value, "missing_recv_ts")
    age = now_ms() - recv
    if age > max_age_ms and tick.source.endswith("_WS"):
        return ValidationResult(False, Status.STALE.value, f"age_ms={age}")
    status = Status.SNAPSHOT.value if tick.source.endswith("_REST") else Status.LIVE.value
    return ValidationResult(True, status, "")


def is_cross_broker_mismatch(
    incoming_ltp: float,
    current_ltp: float,
    *,
    max_pct: float = 0.35,
) -> bool:
    """True when two brokers likely subscribed different contracts (165 vs 257)."""
    if incoming_ltp <= 0 or current_ltp <= 0:
        return False
    base = min(incoming_ltp, current_ltp)
    if base <= 0:
        return False
    return abs(incoming_ltp - current_ltp) / base >= max_pct


def is_newer_than(
    incoming_market_ts: int,
    incoming_recv_ts: int,
    current_market_ts: int,
    current_recv_ts: int,
) -> bool:
    """Reject delayed packets that would overwrite newer unified state."""
    if incoming_market_ts > 0 and current_market_ts > 0:
        if incoming_market_ts < current_market_ts:
            return False
        if incoming_market_ts > current_market_ts:
            return True
    # equal/unknown market ts → use receive order
    return incoming_recv_ts >= current_recv_ts
