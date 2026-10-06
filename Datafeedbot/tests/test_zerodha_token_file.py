"""Zerodha token JSON pickup (no live broker)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from datafeedbot.config import load_zerodha_access_token, zerodha_token_json_candidates
from datafeedbot.zerodha_token_watch import _newest_mtime, start_zerodha_token_watch


def _write_json(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"access_token": token, "saved_at": "2026-08-22T19:00:00+05:30"}),
        encoding="utf-8",
    )


def test_read_prefers_newer_json(tmp_path, monkeypatch):
    feeder = tmp_path / "feeder"
    blitz = tmp_path / "blitz"
    monkeypatch.setenv("DATAFEEDBOT_RUNTIME", str(feeder))
    monkeypatch.setenv("TRADING_RUNTIME", str(blitz))
    monkeypatch.delenv("ZERODHA_ACCESS_TOKEN", raising=False)

    fjson = feeder / "Data" / "shared" / "zerodha_access_token.json"
    bjson = blitz / "Data" / "data" / "shared" / "zerodha_access_token.json"
    _write_json(fjson, "FEEDER_OLD_TOKEN_AAAAAAAA")
    time.sleep(0.05)
    _write_json(bjson, "BLITZ_NEW_TOKEN_BBBBBBBB")

    tok = load_zerodha_access_token(feeder)
    assert tok == "BLITZ_NEW_TOKEN_BBBBBBBB"
    paths = zerodha_token_json_candidates(feeder)
    assert fjson in paths
    assert bjson in paths


def test_read_falls_back_to_env(tmp_path, monkeypatch):
    feeder = tmp_path / "feeder"
    feeder.mkdir()
    monkeypatch.setenv("DATAFEEDBOT_RUNTIME", str(feeder))
    monkeypatch.setenv("TRADING_RUNTIME", str(tmp_path / "missing_blitz"))
    monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "ENV_TOKEN_CCCCCCCCCCCC")
    assert load_zerodha_access_token(feeder) == "ENV_TOKEN_CCCCCCCCCCCC"


def test_watch_fires_on_new_file(tmp_path, monkeypatch):
    feeder = tmp_path / "feeder"
    (feeder / "Data" / "shared").mkdir(parents=True)
    monkeypatch.setenv("DATAFEEDBOT_RUNTIME", str(feeder))
    monkeypatch.setenv("TRADING_RUNTIME", str(tmp_path / "missing_blitz"))
    monkeypatch.delenv("ZERODHA_ACCESS_TOKEN", raising=False)

    seen: list[str] = []
    stop = start_zerodha_token_watch(
        runtime_root=feeder, on_token=seen.append, poll_seconds=0.05
    )
    try:
        _write_json(
            feeder / "Data" / "shared" / "zerodha_access_token.json",
            "WATCH_TOKEN_DDDDDDDDDD",
        )
        deadline = time.time() + 2.0
        while time.time() < deadline and not seen:
            time.sleep(0.05)
        assert seen == ["WATCH_TOKEN_DDDDDDDDDD"]
    finally:
        stop.set()


def test_newest_mtime_empty():
    assert _newest_mtime([]) == 0.0
    assert _newest_mtime([Path("/tmp/does-not-exist-zerodha-json")]) == 0.0
