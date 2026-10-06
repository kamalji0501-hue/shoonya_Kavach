from vps_health_bot.peaks import peaks_today, record_sample


class _Snap:
    def __init__(self):
        from datetime import datetime, timedelta, timezone

        ist = timezone(timedelta(hours=5, minutes=30))
        self.ts = datetime(2026, 8, 13, 13, 5, tzinfo=ist)
        self.load1 = 1.25
        self.mem_used_pct = 41.0
        self.cpu_steal_pct = 0.5
        self.cpu_steal_instant_pct = 2.0
        self.cpu_iowait_instant_pct = 0.1
        self.swap_used_mb = 10.0
        self.bots = []
        self.hft = {"dhan_429": 4}


def test_record_and_peak(tmp_path, monkeypatch):
    monkeypatch.setenv("VPS_HEALTH_RUNTIME", str(tmp_path))
    from vps_health_bot import runtime_paths as rp

    rp.ensure_layout()
    record_sample(_Snap())
    peaks = peaks_today(_Snap().ts)
    assert peaks["samples"] == 1
    assert peaks["load1"]["value"] == 1.25
    assert peaks["dhan_429"]["value"] == 4
