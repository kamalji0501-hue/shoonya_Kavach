"""Symbol maps and order bodies for Groww, Angel, Fyers, and Kotak. No network orders."""

from datetime import date

import pytest

from core.angel_instruments import map_angel_order_symbol
from core.exceptions import BrokerAuthError, OrderPlacementError
from core.fyers_instruments import map_fyers_order_symbol
from core.groww_instruments import map_groww_order_symbol
from core.kotak_instruments import map_kotak_order_symbol


def test_groww_monthly_and_weekly():
    book = {"NIFTY26SEP25000CE", "NIFTY26O0625000CE"}
    assert map_groww_order_symbol("NIFTY 29 SEP 25000 CALL", book) == "NIFTY26SEP25000CE"
    assert map_groww_order_symbol("NIFTY26SEP25000CE", book) == "NIFTY26SEP25000CE"
    assert map_groww_order_symbol("NIFTY26O0625000CE", book) == "NIFTY26O0625000CE"


def test_groww_refuses_expiry_change(monkeypatch):
    monkeypatch.setattr(
        "core.groww_instruments.groww_tsym_from_any",
        lambda _symbol: "NIFTY26OCT25000CE",
    )
    with pytest.raises(ValueError):
        map_groww_order_symbol("NIFTY 29 SEP 25000 CALL")


def test_angel_symbol():
    assert map_angel_order_symbol("NIFTY 29 SEP 25000 CALL") == "NIFTY29SEP2625000CE"


def test_fyers_requires_master_hit():
    master = {"NSE:NIFTY26SEP25000CE", "NSE:NIFTY26O0625000CE"}
    assert map_fyers_order_symbol("NIFTY 29 SEP 25000 CALL", master) == "NSE:NIFTY26SEP25000CE"
    assert map_fyers_order_symbol("NIFTY26O0625000CE", master) == "NSE:NIFTY26O0625000CE"
    with pytest.raises(ValueError):
        map_fyers_order_symbol("NIFTY 29 SEP 24000 CALL", master)


def test_kotak_uses_finder_only():
    def finder(exp, strike, right):
        assert exp == date(2026, 9, 29)
        assert strike == 25000
        assert right == "CE"
        return "NIFTY29SEP2625000CE"

    assert map_kotak_order_symbol("NIFTY 29 SEP 25000 CALL", finder) == "NIFTY29SEP2625000CE"
    with pytest.raises(ValueError):
        map_kotak_order_symbol("NIFTY 29 SEP 25000 CALL", lambda *_a: "")


def test_connect_without_session_raises(tmp_path, monkeypatch):
    from core import angel_credentials, fyers_credentials, groww_credentials, kotak_credentials
    from core.angel_broker import AngelBroker
    from core.fyers_broker import FyersBroker
    from core.groww_broker import GrowwBroker
    from core.kotak_broker import KotakBroker

    monkeypatch.setattr(groww_credentials, "session_path", lambda root=None: tmp_path / "groww.json")
    monkeypatch.setattr(angel_credentials, "session_path", lambda root=None: tmp_path / "angel.json")
    monkeypatch.setattr(fyers_credentials, "session_path", lambda root=None: tmp_path / "fyers.json")
    monkeypatch.setattr(kotak_credentials, "session_path", lambda root=None: tmp_path / "kotak.json")
    with pytest.raises(BrokerAuthError):
        GrowwBroker.connect(tmp_path)
    with pytest.raises(BrokerAuthError):
        AngelBroker.connect(tmp_path)
    with pytest.raises(BrokerAuthError):
        FyersBroker.connect(tmp_path)
    with pytest.raises(BrokerAuthError):
        KotakBroker.connect(tmp_path)


def test_groww_place_body(monkeypatch):
    from core.groww_broker import GrowwBroker

    broker = GrowwBroker("token")
    monkeypatch.setattr(broker, "_request", lambda *a, **k: {"status": "SUCCESS", "payload": {"groww_order_id": "G1"}})
    oid = broker.place_order("NIFTY26SEP25000CE", qty=65, order_type="MARKET", transaction_type="BUY", trade_type="MIS")
    assert oid == "G1"
    assert broker.last_payload["segment"] == "FNO"
    assert broker.last_payload["trading_symbol"] == "NIFTY26SEP25000CE"
    assert broker.last_payload["order_type"] == "MARKET"
    assert broker.last_payload["product"] == "MIS"


def test_angel_place_needs_token(monkeypatch):
    from core.angel_broker import AngelBroker

    broker = AngelBroker("C1", "key", "jwt")
    broker._token_master = {}
    with pytest.raises(OrderPlacementError):
        broker.place_order("NIFTY 29 SEP 25000 CALL", qty=65, order_type="LIMIT", transaction_type="SELL", price=10)
    broker._token_master = {"NIFTY29SEP2625000CE": "999"}
    monkeypatch.setattr(broker, "_request", lambda *a, **k: {"status": True, "data": {"orderid": "A1"}})
    oid = broker.place_order("NIFTY 29 SEP 25000 CALL", qty=65, order_type="LIMIT", transaction_type="SELL", price=10, trade_type="NRML")
    assert oid == "A1"
    assert broker.last_payload["exchange"] == "NFO"
    assert broker.last_payload["tradingsymbol"] == "NIFTY29SEP2625000CE"
    assert broker.last_payload["symboltoken"] == "999"
    assert broker.last_payload["producttype"] == "CARRYFORWARD"


def test_fyers_place_body(monkeypatch):
    from core.fyers_broker import FyersBroker

    broker = FyersBroker("APP-100", "tok")
    broker._master = {"NSE:NIFTY26SEP25000CE"}
    monkeypatch.setattr(broker, "_request", lambda *a, **k: {"s": "ok", "id": "F1"})
    oid = broker.place_order("NIFTY 29 SEP 25000 CALL", qty=65, order_type="MARKET", transaction_type="BUY", trade_type="MIS")
    assert oid == "F1"
    assert broker.last_payload["symbol"] == "NSE:NIFTY26SEP25000CE"
    assert broker.last_payload["type"] == 2
    assert broker.last_payload["side"] == 1
    assert broker.last_payload["productType"] == "INTRADAY"


def test_kotak_market_becomes_limit(monkeypatch):
    from core.kotak_broker import KotakBroker

    broker = KotakBroker("access", "trade", "sid")
    broker._finder = lambda exp, strike, right: "NIFTY29SEP2625000CE"
    sent = {}

    def _submit(payload):
        sent["payload"] = payload
        return "K1"

    monkeypatch.setattr(broker, "_submit", _submit)
    monkeypatch.setattr(broker, "get_ltp", lambda names: {names[0]: 100.0})
    monkeypatch.setattr(broker, "get_order_status", lambda oid: "TRADED")
    oid = broker.place_order("NIFTY 29 SEP 25000 CALL", qty=65, order_type="MARKET", transaction_type="BUY", trade_type="MIS")
    assert oid == "K1"
    assert sent["payload"]["pt"] == "L"
    assert sent["payload"]["es"] == "nse_fo"
    assert sent["payload"]["ts"] == "NIFTY29SEP2625000CE"
    assert float(sent["payload"]["pr"]) > 100
    assert sent["payload"]["pc"] == "MIS"
