from __future__ import annotations

import threading
from typing import Any

from datafeedbot.models import FeedHealth, InstrumentRef, MarketData, Status
from datafeedbot.sequence import SequenceAssigner, now_ms
from datafeedbot.validation import is_newer_than


class UnifiedState:
    """In-RAM unified market state + per-broker health."""

    def __init__(self, sequencer: SequenceAssigner | None = None) -> None:
        self._lock = threading.RLock()
        self._latest: MarketData | None = None
        self._active: InstrumentRef | None = None
        self._health: dict[str, FeedHealth] = {
            "DHAN_WS": FeedHealth(broker="DHAN_WS"),
            "ZERODHA_WS": FeedHealth(broker="ZERODHA_WS"),
            "SHOONYA_WS": FeedHealth(broker="SHOONYA_WS"),
        }
        self.sequencer = sequencer or SequenceAssigner()
        self._spike_high = 0.0
        self._spike_low = 0.0

    def set_active(self, instrument: InstrumentRef | None) -> None:
        with self._lock:
            self._active = instrument
            self._latest = None
            self._spike_high = 0.0
            self._spike_low = 0.0

    def get_active(self) -> InstrumentRef | None:
        with self._lock:
            return self._active

    def update_health(self, health: FeedHealth) -> None:
        with self._lock:
            self._health[health.broker] = health

    def get_health(self) -> dict[str, FeedHealth]:
        with self._lock:
            return {k: FeedHealth(**v.to_dict()) for k, v in self._health.items()}

    def try_accept(self, md: MarketData) -> MarketData | None:
        """Assign sequence and publish if not delayed/stale overwrite."""
        with self._lock:
            if self._active is None:
                # Unsubscribed: do not keep leftover LTP for the next trade.
                return None
            if md.instrument != self._active.key:
                md.status = Status.DATA_INVALID.value
                return None
            cur = self._latest
            if cur is not None and not is_newer_than(
                md.market_timestamp,
                md.received_timestamp,
                cur.market_timestamp,
                cur.received_timestamp,
            ):
                return None
            md.sequence = self.sequencer.next()
            md.sequenced_timestamp = now_ms()
            self._latest = md
            self._spike_high = max(self._spike_high, md.ltp)
            if self._spike_low <= 0:
                self._spike_low = md.ltp
            else:
                self._spike_low = min(self._spike_low, md.ltp)
            return md

    def latest(self) -> MarketData | None:
        with self._lock:
            return self._latest

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": self._active.key if self._active else None,
                "latest": self._latest.to_dict() if self._latest else None,
                "health": {k: v.to_dict() for k, v in self._health.items()},
                "sequence": self.sequencer.current,
                "spike_high": self._spike_high,
                "spike_low": self._spike_low,
            }
