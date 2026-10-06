"""Zerodha/Kite market-data adapter — official kiteconnect WebSocket + REST.

Docs: https://kite.trade/docs/connect/v3/websocket/
Python: https://github.com/zerodha/pykiteconnect
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from datafeedbot.instruments import ZerodhaInstrumentCache
from datafeedbot.models import FeedHealth, InstrumentRef, RawTick, Source
from datafeedbot.sequence import now_ms

logger = logging.getLogger("datafeedbot.brokers.zerodha")

NIFTY50_INDEX_TOKEN = 256265

TickCallback = Callable[[RawTick], None]


def extract_kite_tick(tick: dict[str, Any], instrument_key: str) -> RawTick | None:
    """Normalize kiteconnect tick dict (MODE_FULL / quote).

    Official SDK already parses binary packets into dicts with keys like
    last_price, depth, volume, timestamp, exchange_timestamp.
    Prices are floats in INR (not paise) once parsed by kiteconnect.
    """
    if not isinstance(tick, dict):
        return None
    try:
        ltp = float(tick.get("last_price") or 0)
    except (TypeError, ValueError):
        return None
    if ltp <= 0:
        return None
    bid = ask = None
    depth = tick.get("depth") or {}
    if isinstance(depth, dict):
        buys = depth.get("buy") or []
        sells = depth.get("sell") or []
        if buys and isinstance(buys[0], dict):
            try:
                bid = float(buys[0].get("price") or 0) or None
            except (TypeError, ValueError):
                bid = None
        if sells and isinstance(sells[0], dict):
            try:
                ask = float(sells[0].get("price") or 0) or None
            except (TypeError, ValueError):
                ask = None
    volume = None
    try:
        if tick.get("volume_traded") is not None:
            volume = int(tick.get("volume_traded"))
        elif tick.get("volume") is not None:
            volume = int(tick.get("volume"))
    except (TypeError, ValueError):
        volume = None
    market_ts = 0
    for key in ("exchange_timestamp", "timestamp", "last_trade_time"):
        val = tick.get(key)
        if val is None:
            continue
        try:
            # datetime or epoch
            if hasattr(val, "timestamp"):
                market_ts = int(val.timestamp() * 1000)
            else:
                market_ts = int(float(val) * 1000) if float(val) < 1e12 else int(float(val))
            break
        except Exception:
            continue
    token = tick.get("instrument_token")
    return RawTick(
        source=Source.ZERODHA_WS.value,
        instrument=instrument_key,
        ltp=ltp,
        bid=bid,
        ask=ask,
        volume=volume,
        market_timestamp=market_ts,
        received_timestamp=now_ms(),
        broker_id=str(token) if token is not None else None,
    )


class ZerodhaMarketDataProvider:
    name = "ZERODHA_WS"

    def __init__(
        self,
        *,
        api_key: str,
        access_token: str,
        on_tick: TickCallback,
        instrument_cache: ZerodhaInstrumentCache | None = None,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self._on_tick = on_tick
        self._cache = instrument_cache or ZerodhaInstrumentCache()
        self._token: int | None = None
        self._instrument_key = ""
        self._ticker = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest: RawTick | None = None
        self._health = FeedHealth(broker="ZERODHA_WS")
        self._lock = threading.Lock()

    def get_health(self) -> FeedHealth:
        with self._lock:
            h = FeedHealth(**self._health.to_dict())
            if h.last_received_timestamp:
                h.age_ms = max(0, now_ms() - h.last_received_timestamp)
            return h

    def get_latest(self) -> RawTick | None:
        return self._latest

    async def connect(self) -> None:
        if not self.api_key or not self.access_token:
            with self._lock:
                self._health.status = "NO_CREDENTIALS"
            logger.warning("Zerodha credentials missing — provider idle")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_ticker, name="dfb-zerodha-ws", daemon=True
        )
        self._thread.start()

    async def disconnect(self) -> None:
        self._stop.set()
        t = self._ticker
        if t is not None:
            try:
                t.close()
            except Exception:
                pass
            try:
                t.stop()
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=8.0)
        self._thread = None
        self._ticker = None
        with self._lock:
            self._health.connected = False
            self._health.status = "DISCONNECTED"

    def clear_token(self) -> None:
        """Kavach Kite deactivate — drop token and stop this WS. Dhan untouched."""
        self.access_token = ""
        self._stop.set()
        t = self._ticker
        if t is not None:
            try:
                t.close()
            except Exception:
                pass
            try:
                t.stop()
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=8.0)
        self._thread = None
        self._ticker = None
        with self._lock:
            self._health.connected = False
            self._health.status = "IDLE"
            self._health.last_error = "token_cleared"
        logger.info("Zerodha token cleared — socket will idle")

    def reconnect_with_token(self, access_token: str) -> None:
        """Swap Kite access_token and restart this WS only (Dhan untouched)."""
        token = (access_token or "").strip()
        if not token:
            raise ValueError("empty Zerodha access_token")
        self._stop.set()
        t = self._ticker
        if t is not None:
            try:
                t.close()
            except Exception:
                pass
            try:
                t.stop()
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=8.0)
        self._thread = None
        self._ticker = None
        self.access_token = token
        # New KiteTicker every time. Brief pause so the old socket is gone.
        import time
        time.sleep(1.0)
        self._stop.clear()
        with self._lock:
            self._health.connected = False
            self._health.status = "RECONNECTING"
            self._health.last_error = ""
        self._thread = threading.Thread(
            target=self._run_ticker, name="dfb-zerodha-ws", daemon=True
        )
        self._thread.start()
        logger.info("Zerodha WS reconnect started last4=%s", token[-4:])

    async def subscribe(self, instrument: InstrumentRef) -> None:
        token = instrument.zerodha_instrument_token
        if token is None:
            token = self._cache.resolve(
                instrument.strike, instrument.option_type, instrument.expiry
            )
        self._token = int(token) if token is not None else None
        self._instrument_key = instrument.key
        t = self._ticker
        if t is not None and self._token is not None:
            try:
                t.subscribe([self._token])
                t.set_mode(t.MODE_FULL, [self._token])
            except Exception as exc:
                logger.warning("Zerodha subscribe failed: %s", exc)

    async def unsubscribe(self, instrument: InstrumentRef) -> None:
        t = self._ticker
        tok = instrument.zerodha_instrument_token or self._token
        if t is not None and tok is not None:
            try:
                t.unsubscribe([int(tok)])
            except Exception:
                pass
        self._token = None

    def load_instruments(self, rows: list[dict]) -> None:
        self._cache.load_from_rows(rows)

    def fetch_and_load_instruments(self) -> int:
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=self.api_key)
        kite.set_access_token(self.access_token)
        rows = kite.instruments("NFO")
        self.load_instruments(rows)
        return len(rows)

    def _run_ticker(self) -> None:
        try:
            from kiteconnect import KiteTicker
        except ImportError as exc:
            logger.error("kiteconnect missing: %s", exc)
            with self._lock:
                self._health.status = "ERROR"
                self._health.last_error = str(exc)
            return

        kws = KiteTicker(self.api_key, self.access_token)

        def on_ticks(ws, ticks):  # noqa: ANN001
            if self._stop.is_set():
                return
            if not (self.access_token or "").strip():
                return
            for tick in ticks or []:
                tok = int(tick.get("instrument_token") or 0)
                if tok == NIFTY50_INDEX_TOKEN:
                    raw = extract_kite_tick(tick, "NIFTY_INDEX")
                    if raw:
                        raw = RawTick(
                            source=Source.ZERODHA_WS.value,
                            instrument="NIFTY_INDEX",
                            ltp=raw.ltp,
                            bid=raw.bid,
                            ask=raw.ask,
                            volume=raw.volume,
                            market_timestamp=raw.market_timestamp,
                            received_timestamp=raw.received_timestamp,
                            broker_id=str(NIFTY50_INDEX_TOKEN),
                        )
                        self._latest = raw
                        with self._lock:
                            self._health.packet_count += 1
                            self._health.last_received_timestamp = raw.received_timestamp
                            if raw.market_timestamp:
                                self._health.last_market_timestamp = raw.market_timestamp
                            self._health.status = "HEALTHY"
                            self._health.connected = True
                        self._on_tick(raw)
                    continue
                if self._token is not None and tok != int(self._token):
                    continue
                raw = extract_kite_tick(tick, self._instrument_key or "UNKNOWN")
                if not raw:
                    continue
                self._latest = raw
                with self._lock:
                    self._health.packet_count += 1
                    self._health.last_received_timestamp = raw.received_timestamp
                    if raw.market_timestamp:
                        self._health.last_market_timestamp = raw.market_timestamp
                    self._health.status = "HEALTHY"
                    self._health.connected = True
                self._on_tick(raw)

        def on_connect(ws, response):  # noqa: ANN001
            logger.info("Zerodha WS connected")
            with self._lock:
                self._health.connected = True
                self._health.status = "HEALTHY"
            subs = [NIFTY50_INDEX_TOKEN]
            if self._token is not None:
                subs.append(int(self._token))
            ws.subscribe(subs)
            ws.set_mode(ws.MODE_LTP, [NIFTY50_INDEX_TOKEN])
            if self._token is not None:
                ws.set_mode(ws.MODE_FULL, [int(self._token)])

        def on_close(ws, code, reason):  # noqa: ANN001
            logger.warning("Zerodha WS closed code=%s reason=%s", code, reason)
            with self._lock:
                self._health.connected = False
                self._health.status = "DISCONNECTED"
                self._health.last_error = f"{code}:{reason}"[:200]

        def on_error(ws, code, reason):  # noqa: ANN001
            logger.warning("Zerodha WS error code=%s reason=%s", code, reason)
            with self._lock:
                self._health.status = "ERROR"
                self._health.last_error = f"{code}:{reason}"[:200]

        kws.on_ticks = on_ticks
        kws.on_connect = on_connect
        kws.on_close = on_close
        kws.on_error = on_error
        self._ticker = kws
        try:
            # threaded=True so we can stop later
            kws.connect(threaded=True)
            while not self._stop.is_set():
                self._stop.wait(0.5)
        except Exception as exc:
            logger.error("Zerodha ticker failed: %s", exc)
            with self._lock:
                self._health.status = "ERROR"
                self._health.last_error = str(exc)[:200]
        finally:
            try:
                kws.close()
            except Exception:
                pass


def zerodha_rest_ltp(
    *,
    api_key: str,
    access_token: str,
    instrument_token: int,
    instrument_key: str,
    exchange_symbol: str | None = None,
):
    """REST quote snapshot via kiteconnect (recovery only)."""
    from datafeedbot.rest_guard import RestQuoteOutcome

    try:
        from kiteconnect import KiteConnect
    except ImportError:
        return RestQuoteOutcome(error="kiteconnect_missing")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    # Prefer quote API if exchange:tradingsymbol known; else ltp via instrument dump key
    try:
        if exchange_symbol:
            q = kite.quote([exchange_symbol])
            item = q.get(exchange_symbol) or {}
        else:
            # fallback: ohlc/ltp needs tradingsymbol; use instruments instrument_token via quote if possible
            return RestQuoteOutcome(error="exchange_symbol_required")
        ltp = float(item.get("last_price") or 0)
        if ltp <= 0:
            return RestQuoteOutcome(error="ltp_non_positive")
        depth = item.get("depth") or {}
        bid = ask = None
        if depth.get("buy"):
            bid = float(depth["buy"][0].get("price") or 0) or None
        if depth.get("sell"):
            ask = float(depth["sell"][0].get("price") or 0) or None
        return RestQuoteOutcome(
            tick=RawTick(
                source=Source.ZERODHA_REST.value,
                instrument=instrument_key,
                ltp=ltp,
                bid=bid,
                ask=ask,
                market_timestamp=0,
                received_timestamp=now_ms(),
                broker_id=str(instrument_token),
            )
        )
    except Exception as exc:
        msg = str(exc)
        is_429 = "429" in msg or "Too many" in msg.lower()
        logger.warning("Zerodha REST failed: %s", exc)
        return RestQuoteOutcome(is_429=is_429, error=msg[:160])
