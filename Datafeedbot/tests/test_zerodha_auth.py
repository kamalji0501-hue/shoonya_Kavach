"""Zerodha token helper unit tests (no live broker required)."""

from __future__ import annotations

from pathlib import Path

import datafeedbot.auth.zerodha_token as zt


def test_validate_empty_token():
    assert zt.validate_access_token("", "") is None
    assert zt.validate_access_token("k", "") is None


def test_extract_request_token():
    url = "http://127.0.0.1/?request_token=AbCdEf123456&status=success"
    assert zt._extract_request_token(url) == "AbCdEf123456"


def test_load_credentials_from_env_file(tmp_path, monkeypatch):
    env = tmp_path / "Credentials" / "zerodha" / "zerodha.env"
    env.parent.mkdir(parents=True)
    env.write_text(
        "ZERODHA_API_KEY=abc\n"
        "ZERODHA_API_SECRET=def\n"
        "ZERODHA_USER_ID=WAF140\n"
        "ZERODHA_PASSWORD=secret\n"
        "ZERODHA_TOTP_SECRET=BASE32SECRET\n"
        "ZERODHA_ACCESS_TOKEN=tok123\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ZERODHA_API_KEY", raising=False)
    monkeypatch.delenv("ZERODHA_ACCESS_TOKEN", raising=False)
    creds = zt.load_zerodha_credentials(tmp_path)
    assert creds.api_key == "abc"
    assert creds.api_secret == "def"
    assert creds.user_id == "WAF140"
    assert creds.can_autologin is True
    assert creds.access_token == "tok123"


def test_upsert_env_token(tmp_path):
    env = tmp_path / "zerodha.env"
    env.write_text("ZERODHA_API_KEY=k\nZERODHA_API_SECRET=s\n", encoding="utf-8")
    zt._upsert_env_token(env, "NEWTOKEN", user_id="WAF140")
    text = env.read_text(encoding="utf-8")
    assert "ZERODHA_ACCESS_TOKEN=NEWTOKEN" in text
    assert "ZERODHA_USER_ID=WAF140" in text
    assert (tmp_path / "kite_session.json").is_file()


def test_ensure_uses_valid_cached_token(monkeypatch, tmp_path):
    env = tmp_path / "Credentials" / "zerodha" / "zerodha.env"
    env.parent.mkdir(parents=True)
    env.write_text(
        "ZERODHA_API_KEY=k\nZERODHA_API_SECRET=s\nZERODHA_ACCESS_TOKEN=good\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        zt,
        "validate_access_token",
        lambda api, tok, timeout=15.0: {"user_id": "WAF140"},
    )
    monkeypatch.setattr(
        zt,
        "autologin_access_token",
        lambda creds, timeout=20.0: (_ for _ in ()).throw(RuntimeError("should not login")),
    )
    token = zt.ensure_zerodha_access_token(tmp_path)
    assert token == "good"
