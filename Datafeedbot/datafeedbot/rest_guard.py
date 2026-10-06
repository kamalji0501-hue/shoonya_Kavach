#!/usr/bin/env python3
"""Central REST recovery guard — prevents GoBot-style 429 storms."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("datafeedbot.failover")


@dataclass
class RestQuoteOutcome:
    """REST recovery result — preserves 429 signal for RestRateGuard."""

    tick: Any = None
    is_429: bool = False
    error: str = ""


@dataclass
class RestAttemptResult:
    allowed: bool
    reason: str = ""
    wait_ms: int = 0


class RestRateGuard:
    """Serialize + cool down broker REST quote calls.

    Lessons from GoBot 12-Aug logs: WS drop → REST flood → marketfeed 429.
    Datafeed owns ALL recovery REST; consumers must not hammer quote APIs.
    """

    def __init__(
        self,
        *,
        min_interval_ms: int = 3000,
        max_interval_ms: int = 60_000,
        cooldown_on_429_ms: int = 30_000,
    ) -> None:
        self.min_interval_ms = max(1, int(min_interval_ms))
        self.max_interval_ms = max(self.min_interval_ms, int(max_interval_ms))
        self.cooldown_on_429_ms = max(1, int(cooldown_on_429_ms))
        self._lock = threading.Lock()
        self._last_attempt_mono = 0.0
        self._next_allowed_mono = 0.0
        self._current_interval_ms = self.min_interval_ms
        self._consecutive_failures = 0
        self._consecutive_429 = 0
        self._total_attempts = 0
        self._total_blocked = 0
        self._total_429 = 0
        self._total_success = 0

    def try_acquire(self) -> RestAttemptResult:
        now = time.monotonic()
        with self._lock:
            if now < self._next_allowed_mono:
                wait_ms = int((self._next_allowed_mono - now) * 1000)
                self._total_blocked += 1
                return RestAttemptResult(False, "cooldown", wait_ms)
            since_last = (now - self._last_attempt_mono) * 1000.0
            if self._last_attempt_mono > 0 and since_last < self._current_interval_ms:
                wait_ms = int(self._current_interval_ms - since_last)
                self._total_blocked += 1
                return RestAttemptResult(False, "min_interval", wait_ms)
            self._last_attempt_mono = now
            self._total_attempts += 1
            return RestAttemptResult(True, "ok", 0)

    def record_success(self) -> None:
        with self._lock:
            self._total_success += 1
            self._consecutive_failures = 0
            self._consecutive_429 = 0
            # gently relax toward min interval
            self._current_interval_ms = max(
                self.min_interval_ms,
                int(self._current_interval_ms * 0.7),
            )
            self._next_allowed_mono = time.monotonic() + (
                self._current_interval_ms / 1000.0
            )

    def record_failure(self, *, is_429: bool = False) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if is_429:
                self._consecutive_429 += 1
                self._total_429 += 1
                # exponential cooldown on rate-limit (cap at max)
                factor = min(8, 1 + self._consecutive_429)
                cool = min(self.max_interval_ms, self.cooldown_on_429_ms * factor)
                self._current_interval_ms = cool
                self._next_allowed_mono = time.monotonic() + (cool / 1000.0)
                log_fn = logger.warning if cool >= 1000 else logger.debug
                log_fn(
                    "REST 429 cooldown=%sms consecutive_429=%s",
                    cool,
                    self._consecutive_429,
                )
            else:
                self._current_interval_ms = min(
                    self.max_interval_ms,
                    int(self._current_interval_ms * 1.5) + 500,
                )
                self._next_allowed_mono = time.monotonic() + (
                    self._current_interval_ms / 1000.0
                )

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "attempts": self._total_attempts,
                "blocked": self._total_blocked,
                "success": self._total_success,
                "http_429": self._total_429,
                "consecutive_failures": self._consecutive_failures,
                "consecutive_429": self._consecutive_429,
                "current_interval_ms": self._current_interval_ms,
            }
