"""Shoonya adapter sends limit orders, never a raw market order."""

from unittest.mock import MagicMock

import pytest

from core.exceptions import BrokerAuthError, OrderPlacementError
from core.shoonya_broker import ShoonyaBroker


def test_connect_requires_a_session(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SHOONYA_USER_ID", raising=False)
    monkeypatch.delenv("SHOONYA_SUSERTKEN", raising=False)
    with pytest.raises(BrokerAuthError):
        ShoonyaBroker.connect(tmp_path)


def test_market_request_is_sent_as_limit(monkeypatch) -> None:
    broker = ShoonyaBroker("FA123", "session-token-abcd")
    seen: list[dict] = []

    def _call(endpoint, payload):
        if endpoint == "SearchScrip":
            return {"values": [{"tsym": payload["stext"], "token": "74365"}]}
        if endpoint == "GetQuotes":
            return {"stat": "Ok", "lp": "100.0"}
        if endpoint == "PlaceOrder":
            seen.append(payload)
            return {"stat": "Ok", "norenordno": "1"}
        if endpoint == "SingleOrdHist":
            return [{"status": "COMPLETE"}]
        return {"stat": "Ok"}

    monkeypatch.setattr(broker, "_call", _call)
    monkeypatch.setattr(broker, "get_order_status", lambda _oid: "TRADED")
    oid = broker.place_market_order("NIFTY 29 SEP 25000 CALL", 65, "BUY")
    assert oid == "1"
    assert seen
    assert seen[0]["prctyp"] == "LMT"
    assert seen[0]["tsym"] == "NIFTY29SEP26C25000"
    assert seen[0]["trantype"] == "B"
    assert float(seen[0]["prc"]) > 100


def test_limit_rejection_raises() -> None:
    broker = ShoonyaBroker("FA123", "session-token-abcd")
    broker._call = MagicMock(return_value={"stat": "Not_Ok", "emsg": "Invalid Symbol"})
    with pytest.raises(OrderPlacementError):
        broker.place_order(
            "NIFTY29SEP26C25000",
            qty=65,
            price=10,
            order_type="LIMIT",
            transaction_type="BUY",
        )
