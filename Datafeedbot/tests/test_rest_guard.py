"""Tests for REST rate guard / 429 cooldown (GoBot lesson: no REST storms)."""

from __future__ import annotations

import time

from datafeedbot.rest_guard import RestRateGuard, RestQuoteOutcome


def test_rest_guard_blocks_within_min_interval():
    g = RestRateGuard(min_interval_ms=200, max_interval_ms=1000, cooldown_on_429_ms=500)
    a1 = g.try_acquire()
    assert a1.allowed
    a2 = g.try_acquire()
    assert not a2.allowed
    assert a2.reason in {"min_interval", "cooldown"}


def test_rest_guard_429_extends_cooldown():
    g = RestRateGuard(min_interval_ms=50, max_interval_ms=5000, cooldown_on_429_ms=200)
    assert g.try_acquire().allowed
    g.record_failure(is_429=True)
    blocked = g.try_acquire()
    assert not blocked.allowed
    assert g.stats()["http_429"] == 1
    assert g.stats()["current_interval_ms"] >= 200


def test_rest_guard_success_relaxes_interval():
    g = RestRateGuard(min_interval_ms=20, max_interval_ms=5000, cooldown_on_429_ms=20)
    assert g.try_acquire().allowed
    g.record_failure(is_429=True)
    # After 429: cooldown = max(min_interval,20)*2 = 40ms
    time.sleep(0.12)
    assert g.try_acquire().allowed
    g.record_success()
    assert g.stats()["success"] == 1
    assert g.stats()["current_interval_ms"] >= 20


def test_rest_quote_outcome_defaults():
    o = RestQuoteOutcome(error="x")
    assert o.tick is None
    assert o.is_429 is False
    assert o.error == "x"


def test_alerts_dedupe(monkeypatch):
    from datafeedbot.alerts import AlertSink

    sink = AlertSink(token="", chat_id="", min_repeat_seconds=60)
    assert sink.notify("k", "hello") is False  # no telegram creds
    assert sink.notify("k", "hello again") is False  # suppressed after first log mark
    assert sink.stats()["suppressed"] >= 1


def test_cross_broker_mismatch_detects_wrong_expiry_prices():
    from datafeedbot.validation import is_cross_broker_mismatch

    assert is_cross_broker_mismatch(257.0, 165.0) is True
    assert is_cross_broker_mismatch(166.0, 165.0) is False


def test_zerodha_cache_matches_labelled_expiry():
    from datetime import date, timedelta

    from datafeedbot.instruments import ZerodhaInstrumentCache

    today = date.today()
    near = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    far = (today + timedelta(days=12)).strftime("%Y-%m-%d")
    cache = ZerodhaInstrumentCache()
    cache.load_from_rows(
        [
            {
                "name": "NIFTY",
                "instrument_type": "CE",
                "strike": 24300,
                "instrument_token": 111,
                "expiry": near,
            },
            {
                "name": "NIFTY",
                "instrument_type": "CE",
                "strike": 24300,
                "instrument_token": 222,
                "expiry": far,
            },
        ]
    )
    from datetime import datetime

    label = datetime.strptime(far, "%Y-%m-%d").strftime("%d %b %Y")
    assert cache.resolve(24300, "CE", label) == 222
