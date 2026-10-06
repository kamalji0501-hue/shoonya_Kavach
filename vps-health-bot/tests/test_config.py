from vps_health_bot.config import _parse_ids


def test_parse_ids_mixed():
    ids = _parse_ids("260692573, -1001234567890; 42")
    assert 260692573 in ids
    assert -1001234567890 in ids
    assert 42 in ids
