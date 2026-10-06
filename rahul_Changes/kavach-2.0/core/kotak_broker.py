"""Kotak Neo order adapter. Market requests are sent as our limit price.

Kotak rewrites retail market orders. Quantity is in units. The scrip symbol
comes from a master lookup, not a guessed string.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Callable

import pandas as pd

from core.exceptions import BrokerAuthError, OrderPlacementError
from core.extra_broker_base import ExtraBroker, empty_positions, position_row
from core.kotak_credentials import load_session
from core.kotak_instruments import map_kotak_order_symbol

logger = logging.getLogger("batman.kotak.broker")


class KotakBroker(ExtraBroker):
    name = "kotak"

    def __init__(self, access_token: str, trade_token: str, sid: str, ucc: str = ""):
        super().__init__("kotak")
        self._access = access_token.strip()
        self._trade = trade_token.strip()
        self._sid = sid.strip()
        self._ucc = ucc.strip()
        self._finder: Callable[[date, int, str], str] | None = None
        self.last_payload: dict[str, Any] | None = None

    @classmethod
    def connect(cls, root=None) -> "KotakBroker":
        session = load_session(root)
        if not session["ok"]:
            raise BrokerAuthError(
                "Kotak is the main order broker but no trade session is saved. "
                "Paste the access token, mobile, UCC, TOTP, and MPIN on TOKEN."
            )
        return cls(session["access_token"], session["trade_token"], session["sid"], session["ucc"])

    def needs_reauth(self) -> bool:
        return not (self._trade and self._sid)

    def _market_becomes_limit(self) -> bool:
        return True

    def _request(self, method: str, url: str, body: dict | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": self._trade,
                "Sid": self._sid,
                "Auth": self._trade,
                "neo-fin-key": "neotradeapi",
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
            raise OrderPlacementError(f"Kotak HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OrderPlacementError(f"Kotak network: {exc}") from exc
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise OrderPlacementError("Kotak returned non-JSON") from exc

    def _symbol(self, symbol: str) -> str:
        try:
            return map_kotak_order_symbol(symbol, self._finder)
        except ValueError as exc:
            raise OrderPlacementError(str(exc)) from exc

    def _order_type(self, kind: str) -> str:
        if kind in {"SL-M", "SL-MKT", "STOPMARKET", "STOP_LOSS_MARKET"}:
            return "SL-M"
        if kind in {"SL", "SL-LMT", "STOPLIMIT", "STOP_LOSS"}:
            return "SL"
        return "L"

    def _order_payload(self, **kwargs) -> dict[str, Any]:
        payload = {
            "es": "nse_fo",
            "ts": self._symbol(kwargs["symbol"]),
            "tt": "B" if kwargs["transaction_type"] == "BUY" else "S",
            "pt": self._order_type(kwargs["order_type"]),
            "pr": str(kwargs["price"] or 0),
            "qt": str(int(kwargs["qty"])),
            "pc": self._product(kwargs["trade_type"]),
            "am": "NO",
            "rt": "DAY",
            "tp": str(kwargs["trigger_price"] or 0),
        }
        self.last_payload = payload
        return payload

    def _submit(self, payload: dict[str, Any]) -> str:
        result = self._request(
            "POST",
            "https://gw-napi.kotaksecurities.com/Orders/2.0/quick/user/orders",
            payload,
        )
        data = result.get("data") if isinstance(result, dict) else {}
        oid = ""
        if isinstance(data, dict):
            oid = str(data.get("nOrdNo") or data.get("orderId") or "")
        if not oid:
            raise OrderPlacementError(f"Kotak rejected {payload.get('ts')}")
        return oid

    def _rest_nifty(self) -> float:
        return 0.0

    def _positions_frame(self) -> pd.DataFrame:
        result = self._request("GET", "https://gw-napi.kotaksecurities.com/Portfolio/1.0/portfolio/v1/positions")
        rows = []
        data = result.get("data") if isinstance(result, dict) else []
        for row in data or []:
            if not isinstance(row, dict):
                continue
            rows.append(
                position_row(
                    str(row.get("trdSym") or row.get("sym") or ""),
                    int(float(row.get("netQty") or row.get("qty") or 0)),
                    float(row.get("avgPrice") or 0),
                    str(row.get("tok") or ""),
                    str(row.get("prod") or ""),
                    float(row.get("urMtm") or 0),
                )
            )
        return pd.DataFrame(rows) if rows else empty_positions()
