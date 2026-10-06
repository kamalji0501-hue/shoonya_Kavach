from __future__ import annotations

import threading
import time
from typing import Protocol

from datafeedbot.models import FeedHealth, InstrumentRef, RawTick


class MarketDataProvider(Protocol):
    name: str

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def subscribe(self, instrument: InstrumentRef) -> None: ...

    async def unsubscribe(self, instrument: InstrumentRef) -> None: ...

    def get_health(self) -> FeedHealth: ...

    def get_latest(self) -> RawTick | None: ...


class SequenceAssigner:
    """Monotonic global sequence for accepted valid updates."""

    def __init__(self, start: int = 100_000) -> None:
        self._lock = threading.Lock()
        self._seq = int(start)

    def next(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    @property
    def current(self) -> int:
        with self._lock:
            return self._seq


def now_ms() -> int:
    return int(time.time() * 1000)


def mono_ms() -> float:
    return time.monotonic() * 1000.0
