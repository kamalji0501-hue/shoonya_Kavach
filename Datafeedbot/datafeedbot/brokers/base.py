"""Broker market-data provider contract (plugin-ready for 4+ brokers)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from datafeedbot.models import FeedHealth, InstrumentRef, RawTick


@runtime_checkable
class MarketDataProvider(Protocol):
    """Adapter interface every broker feed must implement."""

    name: str

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def subscribe(self, instrument: InstrumentRef) -> None: ...

    async def unsubscribe(self, instrument: InstrumentRef) -> None: ...

    def get_health(self) -> FeedHealth: ...

    def get_latest(self) -> RawTick | None: ...
