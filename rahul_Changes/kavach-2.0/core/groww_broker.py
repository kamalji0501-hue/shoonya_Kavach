"""Groww order adapter. MARKET is sent as MARKET. Quotes stay inside Groww's rate limit."""

from __future__ import annotations

import json
import logging
import secrets
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

from core.exceptions import BrokerAuthError, OrderPlacementError
from core.extra_broker_base import ExtraBroker, empty_positions, position_row
from core.groww_credentials import load_session
from core.groww_instruments import map_groww_order_symbol

logger = logging.getLogger("batman.groww.broker")

_HOST = "https://api.groww.in"


def _ref() -> str:
    return "KV" + secrets.token_hex(6)


class GrowwBroker(ExtraBroker):
    name = "groww"

    def __init__(self, token: str):
        super().__init__("groww")
        self._token = token.strip()
        self.last_payload: dict[str, Any] | None = None

    @classmethod
    def connect(cls, root=None) -> "GrowwBroker":
        session = load_session(root)
        if not session["ok"]:
            raise BrokerAuthError(
                "Groww is the main order broker but no token is saved. "
                "Paste a Groww access token on TOKEN."
            )
        return cls(session["token"])

    def needs_reauth(self) -> bool:
        return not self._token

    def _request(self, method: str, path: str, body: dict | None = None, query: str = "") -> Any:
        url = _HOST + path + (("?" + query) if query else "")
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "X-API-VERSION": "1.0",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise OrderPlacementError(f"Groww HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OrderPlacementError(f"Groww network: {exc}") from exc
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise OrderPlacementError("Groww returned non-JSON") from exc
        if isinstance(parsed, dict) and str(parsed.get("status") or "").upper() == "FAILURE":
            err = parsed.get("error") or {}
            raise OrderPlacementError(err.get("message") or "Groww request failed")
        return parsed

    def _symbol(self, symbol: str) -> str:
        try:
            return map_groww_order_symbol(symbol)
        except ValueError as exc:
            raise OrderPlacementError(str(exc)) from exc

    def _product(self, trade_type: str) -> str:
        kind = str(trade_type or "MARGIN").upper()
        if kind in {"MIS", "INTRADAY", "I"}:
            return "MIS"
        return "NRML"

    def _order_type(self, kind: str) -> str:
        if kind in {"MARKET", "MKT"}:
            return "MARKET"
        if kind in {"SL", "SL-LMT", "STOPLIMIT", "STOP_LOSS"}:
            return "SL"
        if kind in {"SL-M", "SL-MKT", "STOPMARKET", "STOP_LOSS_MARKET"}:
            return "SL_M"
        return "LIMIT"

    def _order_payload(self, **kwargs) -> dict[str, Any]:
        kind = self._order_type(kwargs["order_type"])
        payload = {
            "trading_symbol": self._symbol(kwargs["symbol"]),
            "quantity": int(kwargs["qty"]),
            "validity": "DAY",
            "exchange": "NSE",
            "segment": "FNO",
            "product": self._product(kwargs["trade_type"]),
            "order_type": kind,
            "transaction_type": kwargs["transaction_type"],
            "order_reference_id": _ref(),
        }
        if kind in {"LIMIT", "SL"}:
            payload["price"] = float(kwargs["price"])
        if kind in {"SL", "SL_M"}:
            payload["trigger_price"] = float(kwargs["trigger_price"] or kwargs["price"])
        self.last_payload = payload
        return payload

    def _submit(self, payload: dict[str, Any]) -> str:
        result = self._request("POST", "/v1/order/create", payload)
        data = result.get("payload") if isinstance(result, dict) else {}
        oid = str((data or {}).get("groww_order_id") or "")
        if not oid:
            raise OrderPlacementError(f"Groww rejected {payload.get('trading_symbol')}")
        return oid

    def _rest_nifty(self) -> float:
        result = self._request(
            "GET",
            "/v1/live-data/ltp",
            query="segment=CASH&exchange_symbols=NSE_NIFTY",
        )
        payload = result.get("payload") if isinstance(result, dict) else {}
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, dict) and value.get("ltp"):
                    return float(value["ltp"])
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _ltp_symbol(self, symbol: str) -> float:
        if str(symbol).upper() in {"NIFTY", "NIFTY 50"}:
            return self._rest_nifty()
        tsym = self._symbol(symbol)
        result = self._request(
            "GET",
            "/v1/live-data/ltp",
            query=f"segment=FNO&exchange_symbols=NSE_{tsym}",
        )
        payload = result.get("payload") if isinstance(result, dict) else {}
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, dict):
                    return float(value.get("ltp") or 0)
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _positions_frame(self) -> pd.DataFrame:
        result = self._request("GET", "/v1/positions/user", query="segment=FNO")
        rows = []
        payload = result.get("payload") if isinstance(result, dict) else result
        positions = payload.get("positions") if isinstance(payload, dict) else payload
        for row in positions or []:
            if not isinstance(row, dict):
                continue
            rows.append(
                position_row(
                    str(row.get("trading_symbol") or ""),
                    int(row.get("quantity") or 0),
                    float(row.get("net_price") or 0),
                    "",
                    str(row.get("product") or ""),
                    float(row.get("realised_pnl") or 0),
                )
            )
        return pd.DataFrame(rows) if rows else empty_positions()
