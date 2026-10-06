"""Dhan WS reconnect policy — one socket, 60s 429, no fake-success reset."""

from datafeedbot.brokers.dhan.provider import (
    _IDLE_GRACE_S,
    _MIN_429_BACKOFF_S,
    is_dhan_ws_429,
    next_ws_backoff,
)


def test_429_detects_http_and_too_many():
    assert is_dhan_ws_429("server rejected WebSocket connection: HTTP 429")
    assert is_dhan_ws_429("Too Many Requests")
    assert not is_dhan_ws_429("no close frame received or sent")


def test_429_backoff_starts_at_60s():
    assert next_ws_backoff(1.0, is_429=True, uptime_s=0.2) == _MIN_429_BACKOFF_S
    assert next_ws_backoff(8.0, is_429=True, uptime_s=0.0) == _MIN_429_BACKOFF_S


def test_429_backoff_doubles_and_caps():
    b = next_ws_backoff(60.0, is_429=True, uptime_s=0.5)
    assert b == 120.0
    assert next_ws_backoff(120.0, is_429=True, uptime_s=0.1) == 120.0


def test_unstable_connect_does_not_reset_to_one_second():
    b = next_ws_backoff(1.0, is_429=False, uptime_s=0.4)
    assert b == _MIN_429_BACKOFF_S


def test_stable_then_drop_uses_short_backoff():
    b = next_ws_backoff(1.0, is_429=False, uptime_s=30.0)
    assert 1.5 <= b <= 20.0


def test_idle_grace_covers_engine_subscribe_swap():
    assert _IDLE_GRACE_S >= 2.0


def test_mcx_feed_segment():
    from datafeedbot.instruments import dhan_feed_segment

    assert dhan_feed_segment("MCX", "M") == (5, "MCX_COMM")
    assert dhan_feed_segment("NSE", "D") == (2, "NSE_FNO")
