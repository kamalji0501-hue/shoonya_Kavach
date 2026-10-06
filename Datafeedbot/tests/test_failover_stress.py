"""Additional failover / stress / instrument tests."""

from __future__ import annotations

import threading
from pathlib import Path

from datafeedbot.ipc import IpcClient, IpcServer
from datafeedbot.models import InstrumentRef, MarketData, RawTick, Source
from datafeedbot.normalization import normalize_raw
from datafeedbot.sequence import SequenceAssigner, now_ms
from datafeedbot.state import UnifiedState
from datafeedbot.validation import validate_tick


def test_rapid_ticks_sequence_unique():
    st = UnifiedState(SequenceAssigner(0))
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))
    seqs = []
    t0 = now_ms()
    for i in range(200):
        md = st.try_accept(
            normalize_raw(
                RawTick(
                    source=Source.DHAN_WS.value,
                    instrument="NIFTY_24500_CE",
                    ltp=100 + i * 0.01,
                    bid=99,
                    ask=101,
                    market_timestamp=t0 + i,
                    received_timestamp=t0 + i,
                )
            )
        )
        if md:
            seqs.append(md.sequence)
    assert len(seqs) == 200
    assert len(set(seqs)) == 200


def test_concurrent_publishers():
    st = UnifiedState(SequenceAssigner(0))
    st.set_active(InstrumentRef(strike=24500, option_type="CE"))

    def pub(source: str, base: int):
        for i in range(50):
            st.try_accept(
                normalize_raw(
                    RawTick(
                        source=source,
                        instrument="NIFTY_24500_CE",
                        ltp=1 + i,
                        market_timestamp=base + i,
                        received_timestamp=base + i,
                    )
                )
            )

    t1 = threading.Thread(target=pub, args=("DHAN_WS", 1_000_000))
    t2 = threading.Thread(target=pub, args=("ZERODHA_WS", 2_000_000))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert st.latest() is not None
    assert st.sequencer.current >= 50


def test_ipc_producer_restart(tmp_path: Path):
    sock = tmp_path / "r.sock"
    s1 = IpcServer(sock, lambda: {"n": 1})
    s1.set_control_handler(lambda msg: {"type": "pong", "ts": 1})
    s1.start()
    c = IpcClient(sock)
    c.connect()
    assert c.send_control("ping").get("type") == "pong"
    c.close()
    s1.stop()
    s2 = IpcServer(sock, lambda: {"n": 2})
    s2.set_control_handler(lambda msg: {"type": "state", "data": {"n": 2}})
    s2.start()
    c2 = IpcClient(sock)
    c2.connect()
    snap = c2.send_control("get_state")
    assert snap["data"]["n"] == 2
    c2.close()
    s2.stop()


def test_all_sources_invalid_when_no_ltp():
    r = validate_tick(
        RawTick(
            source="DHAN_WS",
            instrument="NIFTY_24500_CE",
            ltp=0,
            received_timestamp=now_ms(),
        )
    )
    assert not r.ok


def test_publish_many_clients(tmp_path: Path):
    sock = tmp_path / "multi.sock"
    server = IpcServer(sock, lambda: {})
    server.start()
    clients = []
    try:
        for _ in range(3):
            c = IpcClient(sock)
            c.connect()
            c.recv(timeout=1)
            clients.append(c)
        md = MarketData(
            instrument="NIFTY_24500_CE",
            ltp=1.0,
            bid=None,
            ask=None,
            market_timestamp=1,
            received_timestamp=2,
            source="DHAN_WS",
            status="LIVE",
            sequence=9,
        )
        server.publish(md)
        for c in clients:
            msg = c.recv(timeout=2)
            assert msg and msg["data"]["sequence"] == 9
    finally:
        for c in clients:
            c.close()
        server.stop()
