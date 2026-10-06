from __future__ import annotations

from datafeedbot.models import MarketData, RawTick, Status
from datafeedbot.sequence import now_ms


def normalize_raw(tick: RawTick, *, status: str | None = None) -> MarketData:
    ts = now_ms()
    st = status or (
        Status.SNAPSHOT.value if tick.source.endswith("_REST") else Status.LIVE.value
    )
    return MarketData(
        instrument=tick.instrument,
        ltp=float(tick.ltp),
        bid=(float(tick.bid) if tick.bid is not None else None),
        ask=(float(tick.ask) if tick.ask is not None else None),
        market_timestamp=int(tick.market_timestamp or 0),
        received_timestamp=int(tick.received_timestamp or ts),
        source=tick.source,
        status=st,
        volume=tick.volume,
        parsed_timestamp=ts,
        normalized_timestamp=ts,
    )
