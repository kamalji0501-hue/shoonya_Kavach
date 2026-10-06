"""Shoonya order adapter. Same method names as ZerodhaBroker and BatmanBroker.

Place Order on Shoonya accepts LMT and SL-LMT. A MARKET request from Kavach
is sent as the same aggressive limit the ATO path already uses.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from core.exceptions import BrokerAuthError, OrderCancelError, OrderPlacementError, PositionError
from core.day_pnl_cache import update_day_pnl_from_positions
from core.models import LTPQuote
from core.resilience import CircuitBreaker
from core.shoonya_credentials import load_session
from core.shoonya_instruments import map_shoonya_order_symbol

logger = logging.getLogger("batman.shoonya.broker")

_HOST = "https://api.shoonya.com/NorenWClientAPI/"
_PRODUCT = {
    "MIS": "I",
    "INTRADAY": "I",
    "I": "I",
    "CNC": "C",
    "C": "C",
    "MARGIN": "M",
    "NRML": "M",
    "M": "M",
    "NORMAL": "M",
}
_TERMINAL = {"TRADED", "COMPLETE", "REJECTED", "CANCELLED", "CANCELED", "EXPIRED"}


def _map_status(raw: str) -> str:
    status = str(raw or "").strip().upper()
    if status in {"COMPLETE", "TRADED"}:
        return "TRADED"
    if status in {"CANCELLED", "CANCELED"}:
        return "CANCELLED"
    if status == "REJECTED":
        return "REJECTED"
    if status == "EXPIRED":
        return "EXPIRED"
    return "PENDING"


class ShoonyaBroker:
    def __init__(self, uid: str, susertoken: str):
        self._uid = uid.strip()
        self._token = susertoken.strip()
        self._auth_time = datetime.now()
        self._lock = threading.Lock()
        self._runtime_mode_provider: Callable[[], str] | None = None
        self._circuit = CircuitBreaker(name="shoonya", failure_threshold=5, recovery_timeout=60.0)
        self._orders: dict[str, dict[str, Any]] = {}
        self._token_cache: dict[tuple[str, str], str] = {}

    @classmethod
    def connect(cls, root=None) -> "ShoonyaBroker":
        session = load_session(root)
        if not session["ok"]:
            raise BrokerAuthError(
                "Shoonya is the main order broker but no session is saved. "
                "Paste the Shoonya user id and session token on TOKEN."
            )
        return cls(session["uid"], session["susertoken"])

    def hot_reload_token(self, client_code: str, access_token: str) -> None:
        self._uid = (client_code or self._uid).strip()
        self._token = (access_token or "").strip()
        self._auth_time = datetime.now()

    def needs_reauth(self) -> bool:
        return not self._token

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
            raise OrderPlacementError("Live Shoonya orders are blocked (runtime mode=mock / not prod).")
        if not self._token or not self._uid:
            raise OrderPlacementError(
                "Shoonya session missing — orders blocked. Save a Shoonya session on TOKEN first."
            )

    def _call(self, endpoint: str, payload: dict[str, Any]) -> Any:
        # Shoonya OAuth REST: Bearer token in Authorization, body is text/plain jData=...
        # (not the legacy jData+jKey form-urlencoded Noren style).
        body = ("jData=" + json.dumps(payload, separators=(",", ":"))).encode("utf-8")
        req = urllib.request.Request(
            _HOST + endpoint,
            data=body,
            headers={
                "Content-Type": "text/plain",
                "Authorization": f"Bearer {self._token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise OrderPlacementError(f"Shoonya HTTP {exc.code} {endpoint}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OrderPlacementError(f"Shoonya network {endpoint}: {exc}") from exc
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise OrderPlacementError(f"Shoonya {endpoint} returned non-JSON") from exc

    def _product(self, trade_type: str) -> str:
        return _PRODUCT.get(str(trade_type or "MARGIN").upper(), "M")

    def _symbol(self, symbol: str) -> str:
        try:
            return map_shoonya_order_symbol(symbol)
        except ValueError as exc:
            raise OrderPlacementError(str(exc)) from exc

    def _token_for(self, tsym: str, exchange: str = "NFO") -> str:
        key = (str(exchange or "NFO").upper(), str(tsym or "").strip().upper())
        hit = self._token_cache.get(key)
        if hit:
            return hit
        data = self._call("SearchScrip", {"uid": self._uid, "stext": tsym, "exch": exchange})
        rows = data.get("values") if isinstance(data, dict) else None
        token = ""
        for row in rows or []:
            if str(row.get("tsym") or "").upper() == tsym.upper():
                token = str(row.get("token") or "")
                break
        if not token and rows:
            token = str(rows[0].get("token") or "")
        if not token:
            raise OrderPlacementError(f"Shoonya symbol not found: {tsym}")
        self._token_cache[key] = token
        return token

    def _ltp_rest(self, symbol: str) -> float:
        tsym = self._symbol(symbol)
        exch = "NSE" if tsym in {"NIFTY 50", "NIFTY"} else "NFO"
        if exch == "NSE":
            token = "26000"
        else:
            token = self._token_for(tsym, "NFO")

        last_err = ""
        for attempt in range(3):
            quote = self._call("GetQuotes", {"uid": self._uid, "exch": exch, "token": token})
            if not isinstance(quote, dict) or str(quote.get("stat") or "") not in {"Ok", ""}:
                if isinstance(quote, dict) and quote.get("stat") == "Not_Ok":
                    raise OrderPlacementError(quote.get("emsg") or f"quote failed for {tsym}")
                last_err = "bad stat"
                time.sleep(0.15 * (attempt + 1))
                continue
            q_tsym = str(quote.get("tsym") or "").strip().upper()
            # Burst glitch: API sometimes returns index (NIFTY 50) on an NFO token.
            if exch == "NFO" and q_tsym and q_tsym != tsym.upper():
                last_err = f"tsym mismatch got={q_tsym}"
                logger.warning(
                    "shoonya quote tsym mismatch want=%s got=%s attempt=%s",
                    tsym,
                    q_tsym,
                    attempt + 1,
                )
                time.sleep(0.15 * (attempt + 1))
                continue
            try:
                px = float(quote.get("lp") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if px > 0:
                return px
            last_err = "lp<=0"
            time.sleep(0.15 * (attempt + 1))
        if last_err:
            logger.warning("shoonya quote failed for %s after retries: %s", tsym, last_err)
        return 0.0

    def get_nifty_ltp(self) -> float:
        try:
            from core.nifty_ltp_feed import read_nifty_ltp_cache

            snap = read_nifty_ltp_cache()
            if snap is not None and float(snap.ltp) > 0:
                return float(snap.ltp)
        except Exception as exc:
            logger.debug("Feeder NIFTY cache miss: %s", exc)
        return self._ltp_rest("NIFTY")

    def get_nifty_quote(self) -> LTPQuote:
        ltp = self.get_nifty_ltp()
        return LTPQuote(symbol="NIFTY", ltp=ltp)

    def get_ltp(self, names: list[str], **_kwargs: Any) -> dict[str, float]:
        """LTP for symbols. Accepts kite_only=… kwargs for shared call sites (ignored).

        Optimized: GetQuotes with SearchScrip token cache (no expiry-blind feeder peek).
        """
        return self.get_ltps_batch(list(names or []))

    def get_ltps_batch(self, names: list[str]) -> dict[str, float]:
        """GetQuotes (token-cached SearchScrip); expiry-aware feeder fallback.

        Feeder peek is used only after REST fails, and only with the expiry
        parsed from the Shoonya tsym so we never quote the wrong week.
        """
        out: dict[str, float] = {}
        for name in names or []:
            key = str(name or "").strip()
            if not key:
                continue
            if key in out:
                continue
            px = 0.0
            try:
                px = float(self._ltp_rest(key) or 0)
            except Exception as exc:
                logger.debug("shoonya ltp failed %s: %s", key, exc)
                px = 0.0
            if px <= 0:
                try:
                    from core.feeder_ipc import peek_option_quote
                    from core.shoonya_instruments import _SHOONYA, expiry_of_shoonya_tsym

                    tsym = self._symbol(key)
                    m = _SHOONYA.fullmatch(tsym.upper())
                    exp = expiry_of_shoonya_tsym(tsym)
                    if m and exp is not None:
                        strike = int(m.group(5))
                        opt = "CE" if m.group(4).upper() == "C" else "PE"
                        q = peek_option_quote(
                            strike=strike,
                            option_type=opt,
                            expiry=exp.isoformat(),
                            wait_seconds=2.0,
                        )
                        if q and float(q.get("ltp") or 0) > 0:
                            px = float(q["ltp"])
                            logger.info(
                                "shoonya ltp via feeder fallback %s exp=%s px=%s",
                                tsym,
                                exp,
                                px,
                            )
                except Exception as exc:
                    logger.debug("shoonya feeder ltp fallback failed %s: %s", key, exc)
            out[key] = px
        return out

    def get_fno_ltp_by_security_ids(self, security_ids: dict) -> dict:
        return {}

    def get_nifty_option_ltps(self, legs, expiry_date=None) -> dict:
        """Strike/opt LTPs for Deploy preview for the *selected* expiry.

        Always quotes the Shoonya tsym for expiry_date via GetQuotes (SearchScrip
        token-cached). Feeder peek is skipped: IPC keys have no expiry and the
        feed may be on a different week than the preview dropdown.
        """
        out: dict = {}
        need_rest: list[tuple[tuple, str]] = []
        for strike, opt in legs or []:
            key = (int(strike), str(opt).upper())
            if expiry_date is None:
                out[key] = 0.0
                continue
            from core.shoonya_instruments import format_shoonya_tsym

            tsym = format_shoonya_tsym(expiry_date, key[0], key[1])
            need_rest.append((key, tsym))
        if need_rest:
            batch = self.get_ltps_batch([tsym for _, tsym in need_rest])
            for key, tsym in need_rest:
                out[key] = float(batch.get(tsym) or 0.0)
        return out

    def get_option_chain(self):
        return []

    def atm_strike(self, underlying: str = "NIFTY", expiry=None) -> int:
        ltp = self.get_nifty_ltp()
        return int(round(ltp / 50.0) * 50) if ltp > 0 else 0

    def otm_strike(self, underlying: str, expiry, offset: int) -> int:
        return self.atm_strike() + int(offset) * 50

    def _side(self, transaction_type: str) -> str:
        side = str(transaction_type or "").upper()
        if side in {"B", "BUY"}:
            return "B"
        if side in {"S", "SELL"}:
            return "S"
        raise OrderPlacementError(f"Invalid side: {transaction_type}")

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
        self._enforce_live_mode_for_orders()
        kind = str(order_type or "MARKET").upper()
        if kind in {"MARKET", "MKT"}:
            side = "BUY" if self._side(transaction_type) == "B" else "SELL"
            return self.place_aggressive_limit(
                symbol, int(qty), side, trade_type=trade_type, exchange=exchange
            )
        tsym = self._symbol(symbol)
        prctyp = "SL-LMT" if kind in {"SL", "SL-LMT", "STOPLIMIT", "STOP_LOSS", "SL-M", "SL-MKT", "STOPMARKET", "STOP_LOSS_MARKET"} else "LMT"
        payload = {
            "uid": self._uid,
            "actid": self._uid,
            "exch": "NFO" if str(exchange).upper() in {"NFO", "INDEX", ""} else str(exchange).upper(),
            "tsym": tsym,
            "qty": str(int(qty)),
            "prc": str(float(price)),
            "prd": self._product(trade_type),
            "trantype": self._side(transaction_type),
            "prctyp": prctyp,
            "ret": "DAY",
            "ordersource": "API",
            "dscqty": "0",
            "remarks": "KAVACH2",
        }
        if prctyp == "SL-LMT":
            payload["trgprc"] = str(float(trigger_price or price))
        result = self._call("PlaceOrder", payload)
        if not isinstance(result, dict) or result.get("stat") != "Ok" or not result.get("norenordno"):
            emsg = result.get("emsg") if isinstance(result, dict) else result
            raise OrderPlacementError(f"Shoonya rejected {tsym}: {emsg}")
        oid = str(result["norenordno"])
        self._orders[oid] = {
            "tsym": tsym,
            "exch": payload["exch"],
            "prd": payload["prd"],
            "qty": str(int(qty)),
        }
        logger.info("Shoonya order %s %s %s qty=%s id=%s", payload["trantype"], prctyp, tsym, qty, oid)
        return oid

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
                    logger.debug("shoonya chase modify %s: %s", order_id, exc)
                next_chase = time.time() + max(0.5, float(chase_interval_sec))
            time.sleep(0.4)
        return order_id

    def modify_order(self, order_id: str, price: float | None = None, qty: int | None = None, **_ignored) -> str:
        self._enforce_live_mode_for_orders()
        meta = self._orders.get(str(order_id), {})
        payload = {
            "uid": self._uid,
            "norenordno": str(order_id),
            "exch": meta.get("exch") or "NFO",
            "tsym": meta.get("tsym") or "",
            "qty": str(int(qty or meta.get("qty") or 0)),
            "prc": str(float(price or 0)),
            "prctyp": "LMT",
            "ret": "DAY",
        }
        result = self._call("ModifyOrder", payload)
        if isinstance(result, dict) and result.get("stat") not in {"Ok", None}:
            if result.get("stat") == "Not_Ok":
                raise OrderPlacementError(result.get("emsg") or "modify failed")
        return str(order_id)

    def get_order_status(self, order_id: str) -> str:
        """Return mapped status for order_id.

        Prefer OrderBook (live truth). SingleOrdHist is chronological with
        COMPLETE/Fill near the top and PENDING/NewAck near the bottom — never
        trust rows[-1] alone or overnight/ATO will see PENDING after a fill.
        """
        oid = str(order_id)
        try:
            book = self.get_orderbook()
            for row in book or []:
                rid = str(row.get("order_id") or row.get("norenordno") or "")
                if rid == oid:
                    return _map_status(str(row.get("status") or ""))
        except Exception:
            pass
        result = self._call("SingleOrdHist", {"uid": self._uid, "norenordno": oid})
        rows = result if isinstance(result, list) else []
        if not rows and isinstance(result, dict):
            return _map_status(str(result.get("status") or result.get("stat") or ""))
        # Prefer terminal fill / reject over stale PENDING ack rows.
        mapped = [_map_status(str((r or {}).get("status") or "")) for r in rows]
        for pref in ("TRADED", "REJECTED", "CANCELLED", "CANCELED", "EXPIRED"):
            if pref in mapped:
                return "CANCELLED" if pref in {"CANCELLED", "CANCELED"} else pref
        return mapped[0] if mapped else ""

    def cancel_order(self, order_id: str) -> str:
        self._enforce_live_mode_for_orders()
        result = self._call("CancelOrder", {"uid": self._uid, "norenordno": str(order_id)})
        if isinstance(result, dict) and result.get("stat") == "Not_Ok":
            raise OrderCancelError(result.get("emsg") or "cancel failed")
        return str(order_id)

    def cancel_all_intraday(self) -> None:
        book = self.get_orderbook()
        for row in book or []:
            if _map_status(str(row.get("status") or "")) in _TERMINAL:
                continue
            oid = row.get("order_id") or row.get("norenordno")
            if oid:
                try:
                    self.cancel_order(str(oid))
                except Exception as exc:
                    logger.warning("shoonya cancel-all skip %s: %s", oid, exc)

    def get_positions(self) -> pd.DataFrame:
        result = self._call("PositionBook", {"uid": self._uid, "actid": self._uid})
        rows = result if isinstance(result, list) else []
        if isinstance(result, dict) and result.get("stat") == "Not_Ok":
            if "no data" in str(result.get("emsg") or "").lower():
                rows = []
            else:
                raise PositionError(result.get("emsg") or "positions failed")
        records = []
        for row in rows:
            qty = int(float(row.get("netqty") or 0))
            avg = float(row.get("netavgprc") or 0)
            token = row.get("token") or ""
            tsym = row.get("tsym") or ""
            expiry_label = ""
            try:
                from core.shoonya_instruments import expiry_of_shoonya_tsym

                exp_d = expiry_of_shoonya_tsym(str(tsym))
                if exp_d is not None:
                    expiry_label = exp_d.strftime("%d %b %Y")
            except Exception:
                expiry_label = ""
            ltp = 0.0
            try:
                ltp = float(row.get("lp") or row.get("c") or 0)
            except (TypeError, ValueError):
                ltp = 0.0
            records.append(
                {
                    "tradingsymbol": tsym,
                    "tradingSymbol": tsym,
                    "exchange": row.get("exch") or "NFO",
                    "quantity": qty,
                    "netQty": qty,
                    "average_price": avg,
                    "avgPrice": avg,
                    "last_price": ltp,
                    "ltp": ltp,
                    "instrument_token": token,
                    "instrumentToken": token,
                    "securityId": token,
                    "product": row.get("prd") or "",
                    "pnl": float(row.get("urmtom") or row.get("rpnl") or 0),
                    "buyQty": int(float(row.get("daybuyqty") or 0)),
                    "sellQty": int(float(row.get("daysellqty") or 0)),
                    "expiry": expiry_label,
                }
            )
        frame = pd.DataFrame(records)
        try:
            # day_pnl_cache expects Kite-style list/dict — not a DataFrame
            update_day_pnl_from_positions(list(records))
        except Exception:
            pass
        return frame

    def get_live_pnl(self) -> float:
        frame = self.get_positions()
        if frame is None or frame.empty or "pnl" not in frame.columns:
            return 0.0
        return float(frame["pnl"].sum())

    def get_balance(self) -> dict[str, Any]:
        result = self._call("Limits", {"uid": self._uid, "actid": self._uid})
        if not isinstance(result, dict):
            return {}
        if result.get("stat") == "Not_Ok":
            raise BrokerAuthError(result.get("emsg") or "Shoonya limits failed")
        return result

    def get_orderbook(self) -> list[dict[str, Any]]:
        result = self._call("OrderBook", {"uid": self._uid})
        rows = result if isinstance(result, list) else []
        out = []
        for row in rows:
            try:
                avg = float(row.get("avgprc") or 0)
            except (TypeError, ValueError):
                avg = 0.0
            out.append(
                {
                    "order_id": row.get("norenordno"),
                    "norenordno": row.get("norenordno"),
                    "tradingsymbol": row.get("tsym"),
                    "status": row.get("status"),
                    "quantity": row.get("qty"),
                    "price": row.get("prc"),
                    "average_price": avg,
                    "avgprc": avg,
                    "filled_quantity": row.get("fillshares"),
                    "transaction_type": row.get("trantype"),
                }
            )
        return out

    def get_order_history(self, order_id: str) -> list[dict[str, Any]]:
        """SingleOrdHist rows with average_price for ATO fill / trade summary."""
        result = self._call("SingleOrdHist", {"uid": self._uid, "norenordno": str(order_id)})
        rows = result if isinstance(result, list) else ([] if not isinstance(result, dict) else [result])
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                avg = float(row.get("avgprc") or 0)
            except (TypeError, ValueError):
                avg = 0.0
            out.append(
                {
                    "order_id": row.get("norenordno") or order_id,
                    "status": row.get("status"),
                    "average_price": avg,
                    "avgprc": avg,
                    "tradingsymbol": row.get("tsym"),
                    "quantity": row.get("qty"),
                    "filled_quantity": row.get("fillshares"),
                }
            )
        return out

    def get_holdings(self) -> list:
        result = self._call("Holdings", {"uid": self._uid, "actid": self._uid, "prd": "C"})
        return result if isinstance(result, list) else []

    def get_lot_size(self, symbol: str) -> int:
        tsym = self._symbol(symbol)
        token = self._token_for(tsym)
        info = self._call("GetSecurityInfo", {"uid": self._uid, "exch": "NFO", "token": token})
        try:
            return int(float((info or {}).get("ls") or 65))
        except (TypeError, ValueError):
            return 65

    def close_position(self, symbol: str, qty: int, side: str, trade_type: str = "MARGIN", exchange: str = "NFO") -> str:
        return self.place_market_order(symbol, qty, side, trade_type=trade_type, exchange=exchange)

    def close_all_positions(self, trade_type: str = "MARGIN") -> None:
        frame = self.get_positions()
        if frame is None or frame.empty:
            return
        for _, row in frame.iterrows():
            qty = int(row.get("netQty") or 0)
            if qty == 0:
                continue
            side = "SELL" if qty > 0 else "BUY"
            self.close_position(str(row.get("tradingsymbol") or ""), abs(qty), side, trade_type=trade_type)

    def send_alert(self, message: str, chat_id: str = "", bot_token: str = "") -> None:
        logger.info("shoonya alert: %s", message)

    def circuit_status(self) -> dict[str, Any]:
        return {"name": "shoonya", "open": False}

    def __repr__(self) -> str:
        last4 = self._token[-4:] if len(self._token) >= 4 else ""
        return f"ShoonyaBroker(uid={self._uid}, token=····{last4})"
