"""Shared order-broker methods for Groww, Angel, Fyers, and Kotak.

Strategy code keeps calling place_order / place_market_order / get_positions.
Each subclass only fills in the broker's own HTTP body.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from core.exceptions import OrderCancelError, OrderPlacementError, PositionError
from core.models import LTPQuote
from core.resilience import CircuitBreaker

logger = logging.getLogger("batman.extra_broker")

_TERMINAL = {"TRADED", "COMPLETE", "REJECTED", "CANCELLED", "CANCELED", "EXPIRED"}


def map_order_status(raw: str) -> str:
    status = str(raw or "").strip().upper()
    if status in {"COMPLETE", "TRADED", "FILLED", "EXECUTED"}:
        return "TRADED"
    if status in {"CANCELLED", "CANCELED"}:
        return "CANCELLED"
    if status in {"REJECTED", "FAILED"}:
        return "REJECTED"
    if status == "EXPIRED":
        return "EXPIRED"
    return "PENDING"


class ExtraBroker:
    name = "extra"

    def __init__(self, label: str):
        self._label = label
        self._auth_time = datetime.now()
        self._lock = threading.Lock()
        self._runtime_mode_provider: Callable[[], str] | None = None
        self._circuit = CircuitBreaker(name=label, failure_threshold=5, recovery_timeout=60.0)
        self._orders: dict[str, dict[str, Any]] = {}

    def hot_reload_token(self, client_code: str, access_token: str) -> None:
        del client_code, access_token
        self._auth_time = datetime.now()

    def needs_reauth(self) -> bool:
        return False

    def token_age_hours(self) -> float:
        return (datetime.now() - self._auth_time).total_seconds() / 3600.0

    def set_runtime_mode_provider(self, provider: Callable[[], str] | None) -> None:
        self._runtime_mode_provider = provider

    def _runtime_mode(self) -> str:
        if self._runtime_mode_provider:
            try:
                return str(self._runtime_mode_provider() or "live")
            except Exception:
                return "live"
        return "live"

    def _enforce_live_mode_for_orders(self) -> None:
        if self._runtime_mode() == "mock":
            raise OrderPlacementError(f"Live {self.name} orders are blocked (runtime mode=mock / not prod).")

    def _symbol(self, symbol: str) -> str:
        raise NotImplementedError

    def _rest_nifty(self) -> float:
        raise NotImplementedError

    def _submit(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def _modify(self, order_id: str, price: float | None, qty: int | None) -> str:
        return str(order_id)

    def _status(self, order_id: str) -> str:
        return "PENDING"

    def _cancel(self, order_id: str) -> str:
        return str(order_id)

    def _positions_frame(self) -> pd.DataFrame:
        return pd.DataFrame()

    def _orderbook(self) -> list[dict[str, Any]]:
        return []

    def _balance(self) -> dict[str, Any]:
        return {}

    def _product(self, trade_type: str) -> str:
        kind = str(trade_type or "MARGIN").upper()
        if kind in {"MIS", "INTRADAY", "I"}:
            return "MIS"
        return "NRML"

    def _side_word(self, transaction_type: str) -> str:
        side = str(transaction_type or "").upper()
        if side in {"B", "BUY"}:
            return "BUY"
        if side in {"S", "SELL"}:
            return "SELL"
        raise OrderPlacementError(f"Invalid side: {transaction_type}")

    def get_nifty_ltp(self) -> float:
        own = 0.0
        try:
            own = float(self._rest_nifty() or 0)
        except Exception as exc:
            logger.debug("%s nifty quote failed: %s", self.name, exc)
        if own > 0:
            return own
        try:
            from core.multi_broker_quotes import nifty_ltp_from_saved_brokers

            other, _src = nifty_ltp_from_saved_brokers(skip=self.name)
            if other > 0:
                return other
        except Exception as exc:
            logger.debug("%s other-broker quote failed: %s", self.name, exc)
        try:
            from core.nifty_ltp_feed import read_nifty_ltp_cache

            snap = read_nifty_ltp_cache()
            if snap is not None and float(snap.ltp) > 0:
                return float(snap.ltp)
        except Exception as exc:
            logger.debug("%s feeder cache miss: %s", self.name, exc)
        return 0.0

    def get_nifty_quote(self) -> LTPQuote:
        return LTPQuote(symbol="NIFTY", ltp=self.get_nifty_ltp())

    def get_ltp(self, names: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for name in names or []:
            try:
                out[name] = float(self._ltp_symbol(name) or 0)
            except Exception as exc:
                logger.debug("%s ltp failed %s: %s", self.name, name, exc)
                out[name] = 0.0
        return out

    def _ltp_symbol(self, symbol: str) -> float:
        if str(symbol).upper() in {"NIFTY", "NIFTY 50"}:
            return self._rest_nifty()
        return 0.0

    def get_fno_ltp_by_security_ids(self, security_ids: dict) -> dict:
        return {}

    def get_nifty_option_ltps(self, legs, expiry_date=None) -> dict:
        out = {}
        for strike, opt in legs or []:
            key = (int(strike), str(opt).upper())
            try:
                from core.feeder_ipc import peek_option_quote

                quote = peek_option_quote(strike=key[0], option_type=key[1])
                if quote and float(quote.get("ltp") or 0) > 0:
                    out[key] = float(quote["ltp"])
                    continue
            except Exception:
                pass
            out[key] = 0.0
        return out

    def get_option_chain(self):
        return None

    def atm_strike(self, underlying: str = "NIFTY", expiry=None) -> int:
        ltp = self.get_nifty_ltp()
        if ltp <= 0:
            return 0
        return int(round(ltp / 50.0) * 50)

    def otm_strike(self, underlying: str, expiry, offset: int) -> int:
        return self.atm_strike(underlying, expiry) + int(offset) * 50

    def place_order(
        self,
        symbol: str,
        exchange: str = "NFO",
        qty: int = 65,
        price: float = 0,
        trigger_price: float = 0,
        order_type: str = "MARKET",
        transaction_type: str = "BUY",
        trade_type: str = "MARGIN",
        *,
        confirm: bool | None = None,
    ) -> str:
        del confirm
        self._enforce_live_mode_for_orders()
        kind = str(order_type or "MARKET").upper()
        if kind in {"MARKET", "MKT"} and self._market_becomes_limit():
            side = self._side_word(transaction_type)
            return self.place_aggressive_limit(
                symbol, int(qty), side, trade_type=trade_type, exchange=exchange
            )
        payload = self._order_payload(
            symbol=symbol,
            exchange=exchange,
            qty=int(qty),
            price=float(price or 0),
            trigger_price=float(trigger_price or 0),
            order_type=kind,
            transaction_type=self._side_word(transaction_type),
            trade_type=trade_type,
        )
        oid = self._submit(payload)
        self._orders[str(oid)] = {"symbol": payload.get("symbol") or payload.get("trading_symbol") or symbol, "qty": int(qty)}
        logger.info("%s order %s %s id=%s", self.name, transaction_type, symbol, oid)
        return str(oid)

    def _market_becomes_limit(self) -> bool:
        return False

    def _order_payload(self, **kwargs) -> dict[str, Any]:
        raise NotImplementedError

    def place_market_order(self, symbol: str, qty: int, side: str, trade_type: str = "MARGIN", exchange: str = "NFO") -> str:
        return self.place_order(
            symbol=symbol,
            exchange=exchange,
            qty=qty,
            order_type="MARKET",
            transaction_type=side,
            trade_type=trade_type,
        )

    def place_aggressive_limit(
        self,
        symbol: str,
        qty: int,
        side: str,
        *,
        buffer_pct: float = 10.0,
        tick_size: float = 0.05,
        chase_timeout_sec: float = 45.0,
        chase_interval_sec: float = 5.0,
        trade_type: str = "MARGIN",
        exchange: str = "NFO",
    ) -> str:
        from core.order_pricing import aggressive_limit_price

        self._enforce_live_mode_for_orders()
        side_u = str(side).upper()

        def _quote_limit() -> float:
            ltp = float(self.get_ltp([symbol]).get(symbol) or 0)
            if ltp <= 0:
                ltp = float(self.get_nifty_ltp() or 0)
            if ltp <= 0:
                raise OrderPlacementError(f"No positive LTP for {symbol}")
            return aggressive_limit_price(ltp, side_u, buffer_pct=buffer_pct, tick=tick_size)

        limit_px = _quote_limit()
        order_id = self.place_order(
            symbol=symbol,
            exchange=exchange,
            qty=qty,
            price=limit_px,
            order_type="LIMIT",
            transaction_type=side_u,
            trade_type=trade_type,
            confirm=False,
        )
        deadline = time.time() + max(0.0, float(chase_timeout_sec))
        next_chase = time.time() + max(0.5, float(chase_interval_sec))
        while time.time() < deadline:
            status = self.get_order_status(order_id)
            if status in _TERMINAL:
                return order_id
            if time.time() >= next_chase:
                try:
                    self.modify_order(order_id, price=_quote_limit())
                except Exception as exc:
                    logger.debug("%s chase modify %s: %s", self.name, order_id, exc)
                next_chase = time.time() + max(0.5, float(chase_interval_sec))
            time.sleep(0.4)
        return order_id

    def modify_order(self, order_id: str, price: float | None = None, qty: int | None = None, **_ignored) -> str:
        self._enforce_live_mode_for_orders()
        return self._modify(str(order_id), price, qty)

    def get_order_status(self, order_id: str) -> str:
        return map_order_status(self._status(str(order_id)))

    def cancel_order(self, order_id: str) -> str:
        self._enforce_live_mode_for_orders()
        return self._cancel(str(order_id))

    def cancel_all_intraday(self) -> None:
        for row in self.get_orderbook() or []:
            if map_order_status(str(row.get("status") or "")) in _TERMINAL:
                continue
            oid = row.get("order_id")
            if oid:
                try:
                    self.cancel_order(str(oid))
                except Exception as exc:
                    logger.warning("%s cancel-all skip %s: %s", self.name, oid, exc)

    def get_positions(self) -> pd.DataFrame:
        try:
            return self._positions_frame()
        except PositionError:
            raise
        except Exception as exc:
            raise PositionError(str(exc)) from exc

    def get_live_pnl(self) -> float:
        frame = self.get_positions()
        if frame is None or frame.empty or "pnl" not in frame.columns:
            return 0.0
        return float(frame["pnl"].sum())

    def get_balance(self) -> dict[str, Any]:
        return self._balance()

    def get_orderbook(self) -> list[dict[str, Any]]:
        return self._orderbook()

    def get_holdings(self) -> list:
        return []

    def get_lot_size(self, symbol: str) -> int:
        del symbol
        return 65

    def close_position(self, symbol: str, qty: int, side: str, trade_type: str = "MARGIN", exchange: str = "NFO") -> str:
        close_side = "SELL" if str(side).upper() in {"B", "BUY"} else "BUY"
        return self.place_market_order(symbol, qty, close_side, trade_type=trade_type, exchange=exchange)

    def close_all_positions(self, trade_type: str = "MARGIN") -> None:
        frame = self.get_positions()
        if frame is None or frame.empty:
            return
        for row in frame.to_dict("records"):
            qty = int(row.get("netQty") or 0)
            if qty == 0:
                continue
            side = "SELL" if qty > 0 else "BUY"
            try:
                self.place_market_order(str(row.get("tradingsymbol") or ""), abs(qty), side, trade_type=trade_type)
            except Exception as exc:
                logger.warning("%s close-all skip: %s", self.name, exc)

    def send_alert(self, message: str, chat_id: str = "", bot_token: str = "") -> None:
        del message, chat_id, bot_token

    def circuit_status(self) -> dict[str, Any]:
        return {"name": self.name, "open": False}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"


def empty_positions() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "tradingsymbol", "tradingSymbol", "exchange", "quantity", "netQty",
            "average_price", "avgPrice", "instrument_token", "instrumentToken",
            "securityId", "product", "pnl", "buyQty", "sellQty", "expiry",
        ]
    )


def position_row(tsym: str, qty: int, avg: float, token: str, product: str, pnl: float) -> dict[str, Any]:
    return {
        "tradingsymbol": tsym,
        "tradingSymbol": tsym,
        "exchange": "NFO",
        "quantity": qty,
        "netQty": qty,
        "average_price": avg,
        "avgPrice": avg,
        "instrument_token": token,
        "instrumentToken": token,
        "securityId": token,
        "product": product,
        "pnl": pnl,
        "buyQty": max(qty, 0),
        "sellQty": max(-qty, 0),
        "expiry": "",
    }
