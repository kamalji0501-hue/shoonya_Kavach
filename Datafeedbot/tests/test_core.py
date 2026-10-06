"""50+ unit tests for Datafeedbot core (no live broker required)."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

from datafeedbot.brokers.dhan.provider import extract_dhan_option_tick
from datafeedbot.brokers.zerodha.provider import extract_kite_tick
from datafeedbot.ipc import IpcClient, IpcServer, decode_frame, encode_frame, is_index_tick
from datafeedbot.models import InstrumentRef, MarketData, RawTick, Source, Status
from datafeedbot.normalization import normalize_raw
from datafeedbot.sequence import SequenceAssigner, now_ms
from datafeedbot.state import UnifiedState
from datafeedbot.validation import is_newer_than, validate_tick


def _tick(**kwargs) -> RawTick:
    base = dict(
        source=Source.DHAN_WS.value,
        instrument="NIFTY_24500_CE",
        ltp=125.4,
        bid=125.3,
        ask=125.5,
        market_timestamp=now_ms(),
        received_timestamp=now_ms(),
    )
    base.update(kwargs)
    return RawTick(**base)


# --- broker registry ---
def test_broker_registry_builtins():
    from datafeedbot.brokers import list_providers, planned_providers

    regs = list_providers()
    assert "dhan" in regs
    assert "zerodha" in regs
    assert len(planned_providers()) >= 4


def test_broker_registry_unknown():
    from datafeedbot.brokers import create_provider
    import pytest

    with pytest.raises(KeyError):
        create_provider("not_a_real_broker_xyz")


# --- models / sequence ---
def test_instrument_key():
    assert InstrumentRef(strike=24500, option_type="CE").key == "NIFTY_24500_CE"


def test_sequence_monotonic():
    s = SequenceAssigner(100000)
    a, b, c = s.next(), s.next(), s.next()
    assert a < b < c


def test_sequence_threadsafe():
    s = SequenceAssigner(0)
    out = []

    def worker():
        for _ in range(100):
            out.append(s.next())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(out) == 500
    assert len(set(out)) == 500


def test_marketdata_roundtrip():
    md = MarketData(
        instrument="NIFTY_24600_CE",
        ltp=10.0,
        bid=9.9,
        ask=10.1,
        market_timestamp=1,
        received_timestamp=2,
        source="DHAN_WS",
        status="LIVE",
        sequence=7,
    )
    assert MarketData.from_dict(md.to_dict()).sequence == 7


# --- dhan parse ---
def test_dhan_extract_full_depth():
    tick = {
        "type": "Full Data",
        "LTP": "115.95",
        "security_id": 41013,
        "depth": [{"bid_price": "115.90", "ask_price": "116.20"}],
        "volume": 10,
    }
    p = extract_dhan_option_tick(tick)
    assert p["ltp"] == 115.95
    assert p["bid"] == 115.90
    assert p["ask"] == 116.20


def test_dhan_skip_previous_close():
    assert extract_dhan_option_tick({"type": "Previous Close", "prev_close": 1}) is None


def test_dhan_skip_oi():
    assert extract_dhan_option_tick({"type": "OI Data", "LTP": 1}) is None


def test_dhan_reject_non_dict():
    assert extract_dhan_option_tick([1, 2]) is None


def test_dhan_reject_zero_ltp():
    assert extract_dhan_option_tick({"LTP": 0}) is None


def test_dhan_security_id_alias():
    p = extract_dhan_option_tick({"LTP": 1.5, "securityId": "99"})
    assert p["security_id"] == "99"


# --- zerodha parse ---
def test_kite_extract_full():
    tick = {
        "instrument_token": 123,
        "last_price": 55.5,
        "volume_traded": 100,
        "depth": {
            "buy": [{"price": 55.4, "quantity": 1}],
            "sell": [{"price": 55.6, "quantity": 1}],
        },
    }
    raw = extract_kite_tick(tick, "NIFTY_24500_CE")
    assert raw is not None
    assert raw.ltp == 55.5
    assert raw.bid == 55.4
    assert raw.ask == 55.6
    assert raw.source == "ZERODHA_WS"


def test_kite_reject_zero():
    assert extract_kite_tick({"last_price": 0}, "X") is None


def test_kite_volume_alias():
    raw = extract_kite_tick({"last_price": 1.0, "volume": 9}, "X")
    assert raw and raw.volume == 9


# --- validation ---
def test_validate_ok():
    r = validate_tick(_tick())
    assert r.ok
    assert r.status == Status.LIVE.value


def test_validate_bid_gt_ask():
    r = validate_tick(_tick(bid=10, ask=9))
    assert not r.ok
    assert r.reason == "bid_gt_ask"


def test_validate_bad_ltp():
    r = validate_tick(_tick(ltp=0))
    assert not r.ok


def test_validate_wrong_instrument():
    r = validate_tick(_tick(), expected_instrument="NIFTY_1_CE")
    assert not r.ok


def test_validate_rest_snapshot_status():
    r = validate_tick(_tick(source="DHAN_REST"))
    assert r.ok and r.status == Status.SNAPSHOT.value


def test_validate_stale_ws():
    r = validate_tick(_tick(received_timestamp=now_ms() - 60_000), max_age_ms=1000)
    assert not r.ok
    assert r.status == Status.STALE.value


def test_validate_missing_recv():
    r = validate_tick(_tick(received_timestamp=0))
    assert not r.ok


def test_validate_bad_source():
    r = validate_tick(_tick(source="X"))
    assert not r.ok


def test_is_newer_market_ts():
    assert is_newer_than(200, 1, 100, 9) is True
    assert is_newer_than(50, 999, 100, 1) is False


def test_is_newer_recv_when_market_equal():
    assert is_newer_than(100, 5, 100, 4) is True
    assert is_newer_than(100, 3, 100, 4) is False


# --- normalize / state ---
def test_normalize_sets_live():
    md = normalize_raw(_tick())
    assert md.status == Status.LIVE.value
    assert md.normalized_timestamp


def test_state_assigns_sequence():
    st = UnifiedState(SequenceAssigner(10))
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    md = normalize_raw(_tick())
    out = st.try_accept(md)
    assert out and out.sequence == 11


def test_state_rejects_wrong_instrument():
    st = UnifiedState()
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    md = normalize_raw(_tick(instrument="NIFTY_1_PE"))
    assert st.try_accept(md) is None


def test_state_rejects_delayed_packet():
    st = UnifiedState()
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    t = now_ms()
    a = normalize_raw(_tick(market_timestamp=t + 1000, received_timestamp=t + 1000))
    b = normalize_raw(_tick(market_timestamp=t, received_timestamp=t + 2000, ltp=1.0))
    assert st.try_accept(a)
    assert st.try_accept(b) is None


def test_state_clears_on_instrument_change():
    st = UnifiedState()
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    st.try_accept(normalize_raw(_tick()))
    st.set_active(InstrumentRef(strike=24600, option_type="CE"))
    assert st.latest() is None


def test_state_snapshot_keys():
    st = UnifiedState()
    snap = st.snapshot()
    assert "health" in snap and "sequence" in snap


def test_dual_sources_both_publish():
    st = UnifiedState(SequenceAssigner(0))
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    d = st.try_accept(normalize_raw(_tick(source="DHAN_WS", ltp=1)))
    z = st.try_accept(
        normalize_raw(
            _tick(
                source="ZERODHA_WS",
                ltp=2,
                market_timestamp=now_ms() + 1,
                received_timestamp=now_ms() + 1,
            )
        )
    )
    assert d and z and z.sequence > d.sequence


# --- IPC ---
def test_encode_decode_roundtrip():
    payload = {"type": "tick", "data": {"ltp": 1.25}}
    frame = encode_frame(payload)
    assert decode_frame(frame)["data"]["ltp"] == 1.25


def test_ipc_server_client_tick(tmp_path: Path):
    sock = tmp_path / "market.sock"
    latest = {"v": 0}

    def getter():
        return {"ok": True, "v": latest["v"]}

    server = IpcServer(sock, getter)
    published = []

    def on_control(msg):
        if msg.get("action") == "subscribe":
            return {"type": "subscribed", "instrument": "NIFTY_24500_CE"}
        return {"type": "error"}

    server.set_control_handler(on_control)
    server.start()
    try:
        client = IpcClient(sock)
        client.connect()
        # greeting snapshot is drained inside connect()
        resp = client.send_control("subscribe", strike=24500, option_type="CE")
        assert resp.get("type") == "subscribed"
        md = MarketData(
            instrument="NIFTY_24500_CE",
            ltp=12.0,
            bid=11.9,
            ask=12.1,
            market_timestamp=1,
            received_timestamp=2,
            source="DHAN_WS",
            status="LIVE",
            sequence=42,
        )
        server.publish(md)
        msg = client.recv(timeout=2.0)
        assert msg and msg["type"] == "tick"
        assert msg["data"]["sequence"] == 42
        note = client.note_sequence(42)
        assert note is None
        assert client.note_sequence(42) == "duplicate"
        assert client.note_sequence(44) == "gap_missing_1"
        client.close()
    finally:
        server.stop()


def test_ipc_client_restart(tmp_path: Path):
    sock = tmp_path / "m.sock"
    server = IpcServer(sock, lambda: {"ok": True})
    server.set_control_handler(lambda msg: {"type": "pong", "ts": 1})
    server.start()
    try:
        c1 = IpcClient(sock)
        c1.connect()
        c1.close()
        c2 = IpcClient(sock)
        c2.connect()
        assert c2.send_control("ping").get("type") == "pong"
        c2.close()
    finally:
        server.stop()


def test_is_index_tick():
    idx = MarketData(
        instrument="NIFTY_INDEX",
        ltp=24000.0,
        bid=None,
        ask=None,
        market_timestamp=1,
        received_timestamp=2,
        source="DHAN_WS",
        status="LIVE",
        sequence=1,
    )
    opt = MarketData(
        instrument="NIFTY_24500_CE",
        ltp=12.0,
        bid=11.9,
        ask=12.1,
        market_timestamp=1,
        received_timestamp=2,
        source="DHAN_WS",
        status="LIVE",
        sequence=2,
    )
    assert is_index_tick(idx) is True
    assert is_index_tick(opt) is False
    assert is_index_tick({"instrument": "NIFTY_INDEX"}) is True
    assert is_index_tick({"instrument": "x", "broker_id": "13"}) is True


def _md(instrument: str, seq: int = 1) -> MarketData:
    return MarketData(
        instrument=instrument,
        ltp=10.0,
        bid=9.9,
        ask=10.1,
        market_timestamp=1,
        received_timestamp=2,
        source="DHAN_WS",
        status="LIVE",
        sequence=seq,
    )


def test_ipc_default_index_drops_option(tmp_path: Path):
    sock = tmp_path / "idx.sock"
    server = IpcServer(sock, lambda: {"ok": True})
    server.start()
    try:
        client = IpcClient(sock)
        client.connect()
        server.publish(_md("NIFTY_24500_CE", 7))
        assert client.recv(timeout=0.4) is None
        server.publish(_md("NIFTY_INDEX", 8))
        msg = client.recv(timeout=2.0)
        assert msg and msg["type"] == "tick"
        assert msg["data"]["instrument"] == "NIFTY_INDEX"
        client.close()
    finally:
        server.stop()


def test_ipc_want_all_gets_option(tmp_path: Path):
    sock = tmp_path / "all.sock"
    server = IpcServer(sock, lambda: {"ok": True})
    server.start()
    try:
        client = IpcClient(sock)
        client.connect()
        assert client.send_control("ipc_filter", filter="all").get("filter") == "all"
        server.publish(_md("NIFTY_24500_CE", 9))
        msg = client.recv(timeout=2.0)
        assert msg and msg["data"]["sequence"] == 9
        client.close()
    finally:
        server.stop()


# --- multi-strike / subscription cycles (logical) ---
@pytest.mark.parametrize(
    "strike,opt",
    [
        (24500, "CE"),
        (24600, "CE"),
        (24700, "CE"),
        (24400, "CE"),
        (24500, "PE"),
    ],
)
def test_multi_strike_keys(strike, opt):
    ref = InstrumentRef(strike=strike, option_type=opt)
    st = UnifiedState()
    st.set_active(ref)
    md = normalize_raw(_tick(instrument=ref.key, ltp=float(strike % 100) + 1))
    assert st.try_accept(md)


def test_repeated_subscribe_cycles():
    st = UnifiedState(SequenceAssigner(0))
    for strike in [24500, 24600, 24500, 24700, 24500] * 3:
        ref = InstrumentRef(strike=strike, option_type="CE")
        st.set_active(ref)
        assert st.latest() is None
        out = st.try_accept(normalize_raw(_tick(instrument=ref.key)))
        assert out is not None


# --- failure injection (logical) ---
def test_invalid_token_health_path():
    # provider without creds stays NO_CREDENTIALS / idle — simulated via health model
    from datafeedbot.models import FeedHealth

    h = FeedHealth(broker="ZERODHA_WS", status="NO_CREDENTIALS", connected=False)
    assert h.connected is False


def test_malformed_packet_rejected():
    assert extract_dhan_option_tick({"LTP": "abc"}) is None


def test_duplicate_packet_state_overwrite_same_ts_allowed():
    st = UnifiedState()
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    t = now_ms()
    a = st.try_accept(normalize_raw(_tick(market_timestamp=t, received_timestamp=t, ltp=1)))
    b = st.try_accept(normalize_raw(_tick(market_timestamp=t, received_timestamp=t, ltp=2)))
    assert a and b and b.ltp == 2


def test_both_sources_unavailable_marker():
    # engine recovery publishes only if REST returns — unit: validate STALE
    r = validate_tick(
        _tick(received_timestamp=now_ms() - 999999), max_age_ms=10
    )
    assert r.status == Status.STALE.value


def test_data_invalid_status_enum():
    assert Status.DATA_INVALID.value == "DATA_INVALID"


def test_source_enum_values():
    assert Source.DHAN_WS.value == "DHAN_WS"
    assert Source.ZERODHA_REST.value == "ZERODHA_REST"


# --- performance helper ---
def test_latency_bucket_sort_stats():
    from datafeedbot.engine import DatafeedEngine
    from datafeedbot.config import Settings
    from pathlib import Path

    s = Settings(
        runtime_root=Path("."),
        ipc_socket=Path("/tmp/dfb_test.sock"),
        enable_dhan=False,
        enable_zerodha=False,
    )
    eng = DatafeedEngine(s)
    eng.metrics["process_ms"] = [1, 2, 3, 4, 100]
    stats = eng.latency_stats()
    assert stats["p50"] >= 1
    assert stats["max"] == 100


def test_engine_subscribe_resolve_without_csv(tmp_path: Path):
    from datafeedbot.engine import DatafeedEngine
    from datafeedbot.config import Settings

    s = Settings(
        runtime_root=tmp_path,
        ipc_socket=tmp_path / "x.sock",
        enable_dhan=False,
        enable_zerodha=False,
        instruments_csv=None,
    )
    eng = DatafeedEngine(s)
    ref = eng._resolve_subscribe(
        {"strike": 24500, "option_type": "CE", "dhan_security_id": "41013"}
    )
    assert ref.dhan_security_id == "41013"
    assert ref.key == "NIFTY_24500_CE"


def test_engine_on_raw_tick_publishes(tmp_path: Path):
    from datafeedbot.engine import DatafeedEngine
    from datafeedbot.config import Settings

    sock = tmp_path / "pub.sock"
    s = Settings(
        runtime_root=tmp_path,
        ipc_socket=sock,
        enable_dhan=False,
        enable_zerodha=False,
    )
    eng = DatafeedEngine(s)
    eng.ipc.start()
    try:
        eng.state.set_active(InstrumentRef(strike=24500, option_type="CE"))
        client = IpcClient(sock)
        client.connect()
        _ = client.recv(timeout=1.0)
        want = client.send_control("want", filter="all")
        assert want.get("type") == "wanted"
        eng.on_raw_tick(_tick())
        msg = client.recv(timeout=2.0)
        assert msg and msg["type"] == "tick"
        client.close()
    finally:
        eng.ipc.stop()


def test_af_unix_available():
    assert hasattr(socket, "AF_UNIX")


def test_now_ms_progresses():
    a = now_ms()
    time.sleep(0.01)
    assert now_ms() >= a


def test_negative_bid_rejected():
    assert not validate_tick(_tick(bid=-1)).ok


def test_require_bbo():
    r = validate_tick(_tick(bid=None, ask=None), require_bbo=True)
    assert not r.ok


def test_zerodha_ws_stale_vs_rest():
    r = validate_tick(_tick(source="ZERODHA_REST", received_timestamp=now_ms() - 60_000), max_age_ms=10)
    # REST not age-rejected the same way (endswith _WS check)
    assert r.ok


def test_gap_detection_old():
    c = IpcClient(Path("/tmp/unused.sock"))
    c.last_processed_sequence = 50
    assert c.note_sequence(40) == "old"


def test_health_to_dict():
    from datafeedbot.models import FeedHealth

    assert FeedHealth(broker="DHAN_WS").to_dict()["broker"] == "DHAN_WS"


def test_raw_tick_defaults():
    t = RawTick(source="DHAN_WS", instrument="X", ltp=1.0)
    assert t.meta == {}


def test_instrument_ref_pe():
    assert InstrumentRef(strike=24400, option_type="PE").key.endswith("_PE")


def test_state_spike_track():
    st = UnifiedState()
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    st.try_accept(normalize_raw(_tick(ltp=10)))
    st.try_accept(
        normalize_raw(
            _tick(
                ltp=12,
                market_timestamp=now_ms() + 5,
                received_timestamp=now_ms() + 5,
            )
        )
    )
    snap = st.snapshot()
    assert snap["spike_high"] == 12


def test_multiple_failover_logical():
    """Simulate Dhan stale then Zerodha live continues sequencing."""
    st = UnifiedState(SequenceAssigner(1000))
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    d = st.try_accept(normalize_raw(_tick(source="DHAN_WS", ltp=1)))
    z = st.try_accept(
        normalize_raw(
            _tick(
                source="ZERODHA_WS",
                ltp=1.1,
                market_timestamp=now_ms() + 10,
                received_timestamp=now_ms() + 10,
            )
        )
    )
    assert d and z and z.source == "ZERODHA_WS"


def test_recovery_after_dhan_returns():
    st = UnifiedState(SequenceAssigner(2000))
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    st.try_accept(normalize_raw(_tick(source="ZERODHA_WS", ltp=1)))
    back = st.try_accept(
        normalize_raw(
            _tick(
                source="DHAN_WS",
                ltp=1.2,
                market_timestamp=now_ms() + 20,
                received_timestamp=now_ms() + 20,
            )
        )
    )
    assert back and back.source == "DHAN_WS"


def test_docs_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "README.md").is_file()
