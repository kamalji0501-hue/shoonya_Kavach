"""Unit tests that do not need a live VPS or Telegram token."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vps_health_bot.collectors import Snapshot, in_market_hours
from vps_health_bot import reports

IST = timezone(timedelta(hours=5, minutes=30))


def test_market_hours_weekday_open():
    noon = datetime(2026, 8, 13, 12, 30, tzinfo=IST)  # Thursday
    assert in_market_hours(noon) is True


def test_market_hours_weekend_closed():
    sat = datetime(2026, 8, 15, 12, 30, tzinfo=IST)
    assert in_market_hours(sat) is False


def test_overview_contains_hostname():
    snap = Snapshot(
        ts=datetime(2026, 8, 13, 12, 30, tzinfo=IST),
        hostname="KamaljiAlgo",
        uptime="up 1 day",
        load="0.10 0.20 0.30 1/100 1",
        nproc=2,
        load1=0.1,
        cpu_idle_pct=90.0,
        cpu_iowait_pct=0.1,
        cpu_steal_pct=0.2,
        cpu_iowait_instant_pct=0.1,
        cpu_steal_instant_pct=0.2,
        mem_total_mb=3800,
        mem_available_mb=2000,
        mem_used_pct=40.0,
        swap_used_mb=10,
        swap_total_mb=2048,
        disk_used_pct=28.0,
        disk_avail="38G free",
        inode_pct=5.0,
        market_open=True,
        network={"public_ip": "13.126.134.192"},
        went_well=["Datafeedbot is active"],
        concerns=[],
    )
    text = reports.overview(snap)
    assert "KamaljiAlgo" in text
    assert "MARKET OPEN" in text
    assert "Datafeedbot is active" in text
    assert "None right now" in text
