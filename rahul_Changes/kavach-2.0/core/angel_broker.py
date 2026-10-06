"""Angel One order adapter. Quote socket is separate and only opened by the feeder."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

from core.angel_credentials import load_session
from core.angel_instruments import lookup_angel_token, map_angel_order_symbol
from core.exceptions import BrokerAuthError, OrderPlacementError
from core.extra_broker_base import ExtraBroker, empty_positions, position_row

logger = logging.getLogger("batman.angel.broker")

_HOST = "https://apiconnect.angelone.in"


class AngelBroker(ExtraBroker):
    name = "angel"

    def __init__(self, client_code: str, api_key: str, jwt: str, feed_token: str = ""):
        super().__init__("angel")
        self._client = client_code.strip()
        self._api_key = api_key.strip()
        self._jwt = jwt.strip()
        self._feed = feed_token.strip()
        self._token_master: dict[str, str] | None = None
        self.last_payload: dict[str, Any] | None = None

    @classmethod
    def connect(cls, root=None) -> "AngelBroker":
        session = load_session(root)
        if not session["ok"]:
            raise BrokerAuthError(
                "Angel is the main order broker but no session is saved. "
                "Paste the client code, SmartAPI key, and jwt token on TOKEN."
            )
        return cls(session["client_code"], session["api_key"], session["jwt"], session.get("feed_token") or "")

    def needs_reauth(self) -> bool:
        return not (self._jwt and self._api_key and self._client)

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            _HOST + path,
            data=data,
            headers={
                "Authorization": f"Bearer {self._jwt}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-UserType": "USER",
                "X-SourceID": "WEB",
                "X-ClientLocalIP": "127.0.0.1",
                "X-ClientPublicIP": "3.111.226.250",
                "X-MACAddress": "00:00:00:00:00:00",
                "X-PrivateKey": self._api_key,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise OrderPlacementError(f"Angel HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OrderPlacementError(f"Angel network: {exc}") from exc
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise OrderPlacementError("Angel returned non-JSON") from exc
        if isinstance(parsed, dict) and parsed.get("status") is False:
            raise OrderPlacementError(parsed.get("message") or "Angel request failed")
        return parsed

    def _symbol(self, symbol: str) -> str:
        try:
            tsym = map_angel_order_symbol(symbol)
        except ValueError as exc:
            raise OrderPlacementError(str(exc)) from exc
        token = lookup_angel_token(tsym, self._token_master)
        if not token:
            raise OrderPlacementError(f"Angel symbol token missing for {tsym}. Refusing the order.")
        return tsym

    def _product(self, trade_type: str) -> str:
        kind = str(trade_type or "MARGIN").upper()
        if kind in {"MIS", "INTRADAY", "I"}:
            return "INTRADAY"
        return "CARRYFORWARD"

    def _order_type(self, kind: str) -> str:
        if kind in {"MARKET", "MKT"}:
            return "MARKET"
        if kind in {"SL", "SL-LMT", "STOPLIMIT", "STOP_LOSS"}:
            return "STOPLOSS_LIMIT"
        if kind in {"SL-M", "SL-MKT", "STOPMARKET", "STOP_LOSS_MARKET"}:
            return "STOPLOSS_MARKET"
        return "LIMIT"

    def _order_payload(self, **kwargs) -> dict[str, Any]:
        tsym = self._symbol(kwargs["symbol"])
        token = lookup_angel_token(tsym, self._token_master)
        kind = self._order_type(kwargs["order_type"])
        side = "BUY" if kwargs["transaction_type"] == "BUY" else "SELL"
        payload = {
            "variety": "NORMAL",
            "tradingsymbol": tsym,
            "symboltoken": token,
            "transactiontype": side,
            "exchange": "NFO",
            "ordertype": kind,
            "producttype": self._product(kwargs["trade_type"]),
            "duration": "DAY",
            "price": str(kwargs["price"] if kind != "MARKET" else 0),
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(int(kwargs["qty"])),
        }
        if kind.startswith("STOPLOSS"):
            payload["triggerprice"] = str(kwargs["trigger_price"] or kwargs["price"])
        self.last_payload = payload
        return payload

    def _submit(self, payload: dict[str, Any]) -> str:
        result = self._request("POST", "/rest/secure/angelbroking/order/v1/placeOrder", payload)
        data = result.get("data") if isinstance(result, dict) else {}
        oid = str((data or {}).get("orderid") or (data or {}).get("uniqueorderid") or "")
        if not oid:
            raise OrderPlacementError(f"Angel rejected {payload.get('tradingsymbol')}")
        return oid

    def _rest_nifty(self) -> float:
        result = self._request(
            "POST",
            "/rest/secure/angelbroking/market/v1/quote",
            {"mode": "LTP", "exchangeTokens": {"NSE": ["99926000"]}},
        )
        fetched = ((result.get("data") or {}).get("fetched") if isinstance(result, dict) else None) or []
        if fetched:
            return float(fetched[0].get("ltp") or 0)
        return 0.0

    def _positions_frame(self) -> pd.DataFrame:
        result = self._request("GET", "/rest/secure/angelbroking/order/v1/getPosition")
        rows = []
        for row in (result.get("data") if isinstance(result, dict) else None) or []:
            if not isinstance(row, dict):
                continue
            qty = int(float(row.get("netqty") or row.get("netquantity") or 0))
            rows.append(
                position_row(
                    str(row.get("tradingsymbol") or ""),
                    qty,
                    float(row.get("netprice") or row.get("avgnetprice") or 0),
                    str(row.get("symboltoken") or ""),
                    str(row.get("producttype") or ""),
                    float(row.get("unrealised") or row.get("pnl") or 0),
                )
            )
        return pd.DataFrame(rows) if rows else empty_positions()
