from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Source(str, Enum):
    DHAN_WS = "DHAN_WS"
    ZERODHA_WS = "ZERODHA_WS"
    SHOONYA_WS = "SHOONYA_WS"
    DHAN_REST = "DHAN_REST"
    ZERODHA_REST = "ZERODHA_REST"
    SHOONYA_REST = "SHOONYA_REST"


class Status(str, Enum):
    LIVE = "LIVE"
    SNAPSHOT = "SNAPSHOT"
    STALE = "STALE"
    DATA_INVALID = "DATA_INVALID"
    NO_DATA = "NO_DATA"


@dataclass(slots=True)
class InstrumentRef:
    underlying: str = "NIFTY"
    strike: int = 0
    option_type: str = "CE"  # CE | PE
    expiry: str = ""
    dhan_security_id: str | None = None
    zerodha_instrument_token: int | None = None
    shoonya_token: str | None = None  # NFO/NSE token id as string
    shoonya_exchange: str = "NFO"
    dhan_exchange_code: int = 2  # 2=NSE_FNO, 5=MCX
    dhan_exchange_segment: str = "NSE_FNO"

    @property
    def key(self) -> str:
        return f"{self.underlying}_{self.strike}_{self.option_type}"


@dataclass(slots=True)
class MarketData:
    instrument: str
    ltp: float
    bid: float | None
    ask: float | None
    market_timestamp: int
    received_timestamp: int
    source: str
    status: str
    sequence: int = 0
    volume: int | None = None
    parsed_timestamp: int | None = None
    normalized_timestamp: int | None = None
    sequenced_timestamp: int | None = None
    ipc_publish_timestamp: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarketData:
        return cls(
            instrument=str(d["instrument"]),
            ltp=float(d["ltp"]),
            bid=(float(d["bid"]) if d.get("bid") is not None else None),
            ask=(float(d["ask"]) if d.get("ask") is not None else None),
            market_timestamp=int(d.get("market_timestamp") or 0),
            received_timestamp=int(d.get("received_timestamp") or 0),
            source=str(d.get("source") or ""),
            status=str(d.get("status") or Status.LIVE.value),
            sequence=int(d.get("sequence") or 0),
            volume=(int(d["volume"]) if d.get("volume") is not None else None),
            parsed_timestamp=d.get("parsed_timestamp"),
            normalized_timestamp=d.get("normalized_timestamp"),
            sequenced_timestamp=d.get("sequenced_timestamp"),
            ipc_publish_timestamp=d.get("ipc_publish_timestamp"),
        )


@dataclass
class FeedHealth:
    broker: str
    connected: bool = False
    last_market_timestamp: int = 0
    last_received_timestamp: int = 0
    age_ms: int = 0
    packet_count: int = 0
    status: str = "DISCONNECTED"
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RawTick:
    source: str
    instrument: str
    ltp: float
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    market_timestamp: int = 0
    received_timestamp: int = 0
    broker_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
