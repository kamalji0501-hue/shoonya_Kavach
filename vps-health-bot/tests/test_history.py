from datetime import datetime, timedelta, timezone

from vps_health_bot.history import concern_signature, peaks_today, record_sample, rss_trend


IST = timezone(timedelta(hours=5, minutes=30))


class _Bot:
    def __init__(self, name: str, rss: float):
        self.name = name
        self.active = "active"
        self.rss_mb = rss


class _Snap:
    def __init__(self, minute: int, rss: float, load: float = 0.1):
        self.ts = datetime(2026, 8, 13, 13, minute, tzinfo=IST)
        self.load1 = load
        self.mem_used_pct = 44.0
        self.cpu_steal_instant_pct = 0.0
        self.cpu_steal_pct = 0.0
        self.cpu_iowait_instant_pct = 0.0
        self.cpu_iowait_pct = 0.0
        self.swap_used_mb = 151.0
        self.hostname = "KamaljiAlgo"
        self.bots = [_Bot("BlitzBot", rss), _Bot("Datafeedbot", 300)]
        self.hft = {"dhan_429": 40, "dhan_ws_err": 80}
        self.concerns = [f"Dhan WS HTTP 429 x{40 + minute} in last 15m"]


def test_concern_signature_buckets_429_counts():
    a = concern_signature(["Dhan WS HTTP 429 x46 in last 15m"])
    b = concern_signature(["Dhan WS HTTP 429 x12 in last 15m"])
    assert a == b == "dhan_429_storm"


def test_samples_and_trend(tmp_path, monkeypatch):
    monkeypatch.setenv("VPS_HEALTH_RUNTIME", str(tmp_path))
    from vps_health_bot import runtime_paths as rp

    rp.ensure_layout()
    record_sample(_Snap(8, 699))
    record_sample(_Snap(12, 817))
    peaks = peaks_today(_Snap(12, 817).ts)
    assert peaks["samples"] == 2
    assert peaks["max_rss_mb"]["value"] == 817.0
    trend = rss_trend("blitz_rss_mb", window=8)
    assert trend["delta"] == 118.0
