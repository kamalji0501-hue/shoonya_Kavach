"""Fyers v3 order adapter. The data socket is not opened here."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

from core.exceptions import BrokerAuthError, OrderPlacementError
from core.extra_broker_base import ExtraBroker, empty_positions, position_row
from core.fyers_credentials import load_session
from core.fyers_instruments import map_fyers_order_symbol

logger = logging.getLogger("batman.fyers.broker")

_HOST = "https://api-t1.fyers.in"


class FyersBroker(ExtraBroker):
    name = "fyers"

    def __init__(self, app_id: str, access_token: str):
        super().__init__("fyers")
        self._app_id = app_id.strip()
        self._token = access_token.strip()
        self._master: set[str] | None = None
        self.last_payload: dict[str, Any] | None = None

    @classmethod
    def connect(cls, root=None) -> "FyersBroker":
        session = load_session(root)
        if not session["ok"]:
            raise BrokerAuthError(
                "Fyers is the main order broker but no session is saved. "
                "Paste the Fyers app id and access token on TOKEN."
            )
        return cls(session["app_id"], session["access_token"])

    def needs_reauth(self) -> bool:
        return not (self._app_id and self._token)

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            _HOST + path,
            data=data,
            headers={
                "Authorization": f"{self._app_id}:{self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise OrderPlacementError(f"Fyers HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OrderPlacementError(f"Fyers network: {exc}") from exc
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise OrderPlacementError("Fyers returned non-JSON") from exc
        if isinstance(parsed, dict) and str(parsed.get("s") or "").lower() == "error":
            raise OrderPlacementError(parsed.get("message") or "Fyers request failed")
        return parsed

    def _symbol(self, symbol: str) -> str:
        try:
            return map_fyers_order_symbol(symbol, self._master)
        except ValueError as exc:
            raise OrderPlacementError(str(exc)) from exc

    def _product(self, trade_type: str) -> str:
        kind = str(trade_type or "MARGIN").upper()
        if kind in {"MIS", "INTRADAY", "I"}:
            return "INTRADAY"
        return "MARGIN"

    def _type_code(self, kind: str) -> int:
        if kind in {"MARKET", "MKT"}:
            return 2
        if kind in {"SL-M", "SL-MKT", "STOPMARKET", "STOP_LOSS_MARKET"}:
            return 3
        if kind in {"SL", "SL-LMT", "STOPLIMIT", "STOP_LOSS"}:
            return 4
        return 1

    def _order_payload(self, **kwargs) -> dict[str, Any]:
        kind = str(kwargs["order_type"]).upper()
        code = self._type_code(kind)
        payload = {
            "symbol": self._symbol(kwargs["symbol"]),
            "qty": int(kwargs["qty"]),
            "type": code,
            "side": 1 if kwargs["transaction_type"] == "BUY" else -1,
            "productType": self._product(kwargs["trade_type"]),
            "limitPrice": float(kwargs["price"]) if code in {1, 4} else 0,
            "stopPrice": float(kwargs["trigger_price"] or 0) if code in {3, 4} else 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }
        self.last_payload = payload
        return payload

    def _submit(self, payload: dict[str, Any]) -> str:
        result = self._request("POST", "/api/v3/orders/sync", payload)
        oid = str((result or {}).get("id") or "")
        if not oid:
            raise OrderPlacementError(f"Fyers rejected {payload.get('symbol')}")
        return oid

    def _rest_nifty(self) -> float:
        result = self._request("GET", "/api/v3/quotes?symbols=NSE:NIFTY50-INDEX")
        rows = (result or {}).get("d") or []
        if rows:
            value = rows[0].get("v") or {}
            return float(value.get("lp") or 0)
        return 0.0

    def _positions_frame(self) -> pd.DataFrame:
        result = self._request("GET", "/api/v3/positions")
        rows = []
        net = ((result or {}).get("netPositions") or (result or {}).get("overall") or [])
        if isinstance(result, dict) and isinstance(result.get("netPositions"), dict):
            net = result["netPositions"].get("netPositions") or []
        for row in net or []:
            if not isinstance(row, dict):
                continue
            rows.append(
                position_row(
                    str(row.get("symbol") or ""),
                    int(row.get("netQty") or row.get("qty") or 0),
                    float(row.get("netAvg") or row.get("avgPrice") or 0),
                    "",
                    str(row.get("productType") or ""),
                    float(row.get("pl") or row.get("unrealized_profit") or 0),
                )
            )
        return pd.DataFrame(rows) if rows else empty_positions()
