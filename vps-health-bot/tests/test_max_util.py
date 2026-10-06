from types import SimpleNamespace

from vps_health_bot.max_util import max_util_reasons


def test_max_util_quiet():
    snap = SimpleNamespace(
        nproc=2,
        mem_used_pct=44.0,
        cpu_steal_instant_pct=0.0,
        cpu_steal_pct=0.0,
        load1=0.05,
        swap_used_mb=150,
        disk_used_pct=28,
        bots=[SimpleNamespace(name="BlitzBot", active="active", cpu_pct=1.1, rss_mb=700)],
    )
    assert max_util_reasons(snap) == []


def test_max_util_ram_and_load():
    snap = SimpleNamespace(
        nproc=2,
        mem_used_pct=90.0,
        cpu_steal_instant_pct=12.0,
        load1=2.4,
        swap_used_mb=10,
        disk_used_pct=20,
        bots=[],
    )
    reasons = max_util_reasons(snap)
    assert any("RAM" in r for r in reasons)
    assert any("steal" in r for r in reasons)
    assert any("load" in r for r in reasons)
