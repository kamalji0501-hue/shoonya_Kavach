"""Unit tests for subscription validator / limits / grant store."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from subscription.enforcement import deploy_requested_limits
from subscription.grant_store import read_grant, write_grant
from subscription.models import DayGrant, SubscriptionRecord, normalize_server_id
from subscription.validator import (
    validate_directional_trade_limits,
    validate_record,
    validate_trade_limits,
)


SERVER = "http://3.111.226.250:8789/"


def _rec(**kwargs):
    today = date.today()  # tests patch today_ist
    base = dict(
        serial_number="001",
        name="Rahul",
        server_id=SERVER,
        start_date=today - timedelta(days=10),
        end_date=today + timedelta(days=20),
        max_sell_lots_directional=0,
        max_ato_lots_directional=0,
        max_sell_lots_batman=10,
        max_ato_lots_batman=5,
        server_alias="Rahul aws",
        status="ACTIVE",
    )
    base.update(kwargs)
    return SubscriptionRecord(**base)


class NormalizeTests(unittest.TestCase):
    def test_trailing_slash(self):
        self.assertEqual(
            normalize_server_id("http://3.111.226.250:8789/"),
            normalize_server_id("http://3.111.226.250:8789"),
        )


class ValidatorTests(unittest.TestCase):
    def test_active(self):
        with mock.patch("subscription.validator.today_ist", return_value=date(2026, 9, 26)):
            d = validate_record(
                _rec(
                    start_date=date(2026, 9, 1),
                    end_date=date(2026, 12, 31),
                ),
                algo_server_id=SERVER,
            )
        self.assertTrue(d.allowed)
        self.assertEqual(d.ui_status, "ACTIVE")

    def test_missing(self):
        d = validate_record(None, algo_server_id=SERVER)
        self.assertFalse(d.allowed)
        self.assertEqual(d.ui_status, "USER_NOT_REGISTERED")

    def test_server_mismatch(self):
        d = validate_record(_rec(server_id="http://other/"), algo_server_id=SERVER)
        self.assertFalse(d.allowed)
        self.assertEqual(d.ui_status, "SERVER_NOT_AUTHORIZED")

    def test_blocked(self):
        with mock.patch("subscription.validator.today_ist", return_value=date(2026, 9, 26)):
            d = validate_record(
                _rec(status="BLOCKED", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
                algo_server_id=SERVER,
            )
        self.assertFalse(d.allowed)
        self.assertEqual(d.ui_status, "NOT_AUTHORIZED")

    def test_not_started(self):
        with mock.patch("subscription.validator.today_ist", return_value=date(2026, 9, 26)):
            d = validate_record(
                _rec(start_date=date(2026, 10, 1), end_date=date(2026, 12, 31)),
                algo_server_id=SERVER,
            )
        self.assertFalse(d.allowed)

    def test_expired(self):
        with mock.patch("subscription.validator.today_ist", return_value=date(2026, 9, 26)):
            d = validate_record(
                _rec(start_date=date(2026, 1, 1), end_date=date(2026, 9, 1)),
                algo_server_id=SERVER,
            )
        self.assertFalse(d.allowed)
        self.assertEqual(d.ui_status, "EXPIRED")

    def test_sell_at_ceiling(self):
        ok, _ = validate_trade_limits(
            max_sell_lots_batman=10,
            max_ato_lots_batman=5,
            requested_sell_lots=10,
            requested_ato_lots=5,
        )
        self.assertTrue(ok)

    def test_sell_over(self):
        ok, err = validate_trade_limits(
            max_sell_lots_batman=10,
            max_ato_lots_batman=5,
            requested_sell_lots=11,
            requested_ato_lots=1,
        )
        self.assertFalse(ok)
        self.assertIn("10 Batman SELL lots", err)

    def test_ato_over(self):
        ok, err = validate_trade_limits(
            max_sell_lots_batman=10,
            max_ato_lots_batman=5,
            requested_sell_lots=1,
            requested_ato_lots=6,
        )
        self.assertFalse(ok)
        self.assertIn("5 Batman ATO lots", err)

    def test_deploy_math(self):
        sell, ato = deploy_requested_limits(2)
        self.assertEqual(sell, 8)
        self.assertEqual(ato, 4)


class GrantStoreTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            g = DayGrant(
                valid_for_date="2026-09-26",
                server_id=SERVER,
                validated_at="2026-09-26T08:00:00+05:30",
                allowed=True,
                reason="ok",
                ui_status="ACTIVE",
                max_sell_lots_batman=10,
                max_ato_lots_batman=5,
            )
            write_grant(Path(td), g)
            loaded = read_grant(Path(td), "2026-09-26")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertTrue(loaded.allowed)
            self.assertEqual(loaded.max_sell_lots_batman, 10)


class FileRepoTests(unittest.TestCase):
    def test_file_repo(self):
        from subscription.config import SubscriptionConfig
        from subscription.repository import FileRepository

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fix.json"
            path.write_text(
                json.dumps(
                    {
                        "subscriptions": [
                            {
                                "serial_number": "001",
                                "name": "Rahul",
                                "server_id": SERVER,
                                "start_date": "2026-01-01",
                                "end_date": "2026-12-31",
                                "max_sell_lots_directional": 0,
                                "max_ato_lots_directional": 0,
                                "max_sell_lots_batman": 10,
                                "max_ato_lots_batman": 5,
                                "server_alias": "Rahul aws",
                                "status": "ACTIVE",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cfg = SubscriptionConfig(
                algo_server_id=SERVER,
                source="file",
                api_url="",
                api_key="",
                file_path=path,
                validate_hour_ist=8,
                api_timeout_sec=5,
                api_retry_count=0,
                grant_dir=Path(td) / "g",
                audit_dir=Path(td) / "a",
                expiring_soon_days=7,
            )
            rec = FileRepository(cfg).fetch_for_server("http://3.111.226.250:8789")
            self.assertIsNotNone(rec)
            with mock.patch("subscription.validator.today_ist", return_value=date(2026, 9, 26)):
                d = validate_record(rec, algo_server_id=SERVER)
            self.assertTrue(d.allowed)


    def test_directional_limits_use_dir_caps_not_batman(self):
        ok, _ = validate_directional_trade_limits(
            max_sell_lots_directional=60,
            max_ato_lots_directional=60,
            requested_sell_lots=60,
            requested_ato_lots=60,
        )
        self.assertTrue(ok)
        ok, err = validate_directional_trade_limits(
            max_sell_lots_directional=60,
            max_ato_lots_directional=60,
            requested_sell_lots=61,
            requested_ato_lots=1,
        )
        self.assertFalse(ok)
        self.assertIn("60 Directional SELL lots", err)
        # Batman validator must still reject 60 when batman max is 48
        ok_b, err_b = validate_trade_limits(
            max_sell_lots_batman=48,
            max_ato_lots_batman=48,
            requested_sell_lots=60,
            requested_ato_lots=60,
        )
        self.assertFalse(ok_b)
        self.assertIn("48 Batman SELL lots", err_b)

if __name__ == "__main__":
    unittest.main()
