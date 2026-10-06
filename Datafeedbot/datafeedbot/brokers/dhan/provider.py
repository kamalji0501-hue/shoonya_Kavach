"""Dhan market-data adapter (extracted patterns from GoBot option_ws_feed/tick)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Callable

import httpx

from datafeedbot.models import FeedHealth, InstrumentRef, RawTick, Source
from datafeedbot.sequence import now_ms

logger = logging.getLogger("datafeedbot.brokers.dhan")

_NSE_FNO = 2
_FULL = 21
_UNSUB_FULL = _FULL + 1  # Dhan v2: unsubscribe code = subscribe code + 1
_TICKER = 15
_UNSUB_TICKER = _TICKER + 1
NIFTY_INDEX_SID = "13"
_INDEX_CODE = 0
_INDEX_SEG = "IDX_I"
_MIN_429_BACKOFF_S = 60.0
_MAX_429_BACKOFF_S = 120.0
_STABLE_CONNECT_S = 2.0
_IDLE_GRACE_S = 2.0
TickCallback = Callable[[RawTick], None]


def is_dhan_ws_429(exc: BaseException | str) -> bool:
    msg = str(exc)
    return "429" in msg or "Too Many" in msg


def next_ws_backoff(prev: float, *, is_429: bool, uptime_s: float) -> float:
    """Backoff after a drop. 429 or sub-2s 'connect' never resets to 1s."""
    prev = max(0.0, float(prev))
    if is_429:
        if prev < _MIN_429_BACKOFF_S:
            return _MIN_429_BACKOFF_S
        return min(_MAX_429_BACKOFF_S, prev * 2.0)
    # Instant drop (auth fail / IP reset) — same cool-off as 429, do not stampede.
    if uptime_s < _STABLE_CONNECT_S:
        if prev < _MIN_429_BACKOFF_S:
            return _MIN_429_BACKOFF_S
        return min(_MAX_429_BACKOFF_S, prev * 2.0)
    return min(20.0, max(1.5, prev * 1.5 if prev else 1.5))


def _is_ws_open(feed: Any) -> bool:
    ws = getattr(feed, "ws", None)
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is None:
        return True
    name = str(getattr(state, "name", state)).upper()
    return name not in {"CLOSED", "CLOSING"}


async def _await_disconnect(feed: Any) -> None:
    """Always await MarketFeed.disconnect(). Never close_connection() (fire-and-forget)."""
    if feed is None:
        return
    disconnect = getattr(feed, "disconnect", None)
    if not callable(disconnect):
        return
    try:
        res = disconnect()
        if asyncio.iscoroutine(res):
            await asyncio.wait_for(res, timeout=3.0)
    except Exception:
        logger.debug("Dhan WS disconnect ignored", exc_info=True)
    try:
        feed.ws = None
    except Exception:
        pass


async def _send_sid(
    feed: Any,
    sid: str,
    *,
    unsub: bool = False,
    exch_code: int = _NSE_FNO,
    exch_seg: str = "NSE_FNO",
) -> None:
    ws = getattr(feed, "ws", None)
    if ws is None:
        raise RuntimeError("dhan ws missing")
    payload = {
        "RequestCode": int(_UNSUB_FULL if unsub else _FULL),
        "InstrumentCount": 1,
        "InstrumentList": [
            {"ExchangeSegment": str(exch_seg or "NSE_FNO"), "SecurityId": str(sid)}
        ],
    }
    await ws.send(json.dumps(payload))
    code = int(exch_code or _NSE_FNO)
    tup = (code, str(sid), _FULL)
    current = list(getattr(feed, "instruments", None) or [])
    if unsub:
        feed.instruments = [x for x in current if str(x[1]) != str(sid)]
    else:
        merged = [x for x in current if str(x[1]) != str(sid)]
        merged.append(tup)
        feed.instruments = merged


async def _send_index(feed: Any, *, unsub: bool = False) -> None:
    """Pin NIFTY index (sid 13 / IDX_I) on the same Dhan WS as options."""
    ws = getattr(feed, "ws", None)
    if ws is None:
        raise RuntimeError("dhan ws missing")
    payload = {
        "RequestCode": int(_UNSUB_TICKER if unsub else _TICKER),
        "InstrumentCount": 1,
        "InstrumentList": [
            {"ExchangeSegment": _INDEX_SEG, "SecurityId": NIFTY_INDEX_SID}
        ],
    }
    await ws.send(json.dumps(payload))
    tup = (_INDEX_CODE, NIFTY_INDEX_SID, _TICKER)
    current = list(getattr(feed, "instruments", None) or [])
    if unsub:
        feed.instruments = [x for x in current if str(x[1]) != NIFTY_INDEX_SID]
    else:
        merged = [x for x in current if str(x[1]) != NIFTY_INDEX_SID]
        merged.append(tup)
        feed.instruments = merged


def extract_dhan_option_tick(tick: Any) -> dict[str, Any] | None:
    """Normalize Dhan MarketFeed tick → ltp/bid/ask/volume/security_id (GoBot-compatible)."""
    if not isinstance(tick, dict):
        return None
    tick_type = str(tick.get("type") or "")
    if tick_type in {"Previous Close", "OI Data"}:
        return None

    def _f(val: Any) -> float | None:
        if val is None or val == "":
            return None
        try:
            x = float(val)
        except (TypeError, ValueError):
            return None
        return x if x > 0 else None

    ltp = _f(tick.get("LTP") if "LTP" in tick else tick.get("ltp"))
    if ltp is None:
        return None
    bid = _f(tick.get("bid") or tick.get("best_bid"))
    ask = _f(tick.get("ask") or tick.get("best_ask"))
    depth = tick.get("depth")
    if isinstance(depth, list) and depth:
        top = depth[0] if isinstance(depth[0], dict) else {}
        if bid is None:
            bid = _f(top.get("bid_price"))
        if ask is None:
            ask = _f(top.get("ask_price"))
    volume = None
    try:
        if tick.get("volume") is not None:
            volume = int(tick.get("volume"))
    except (TypeError, ValueError):
        volume = None
    sid = tick.get("security_id")
    if sid is None:
        sid = tick.get("securityId")
    return {
        "ltp": ltp,
        "bid": bid,
        "ask": ask,
        "volume": volume,
        "security_id": str(sid) if sid is not None else None,
        "type": tick_type or "Ticker Data",
    }


def _patch_marketfeed_connect(MarketFeed: Any) -> None:
    if getattr(MarketFeed, "_dfb_ping_patched", False):
        return
    import websockets

    async def connect(self):  # type: ignore[no-untyped-def]
        if not self.ws or self.ws.state == websockets.protocol.State.CLOSED:
            try:
                if self.version == "v2":
                    url = (
                        f"{MarketFeed.market_feed_wss}?version=2"
                        f"&token={self.access_token}&clientId={self.client_id}&authType=2"
                    )
                    self.ws = await websockets.connect(
                        url,
                        ping_interval=20,
                        ping_timeout=90,
                        close_timeout=3,
                        max_queue=64,
                    )
                else:
                    raise ValueError(f"Unsupported version: {self.version}")
                await self.subscribe_instruments()
                if self.on_connect:
                    self.on_connect(self)
            except Exception as e:
                if self.on_error:
                    self.on_error(self, e)
                raise e
        else:
            try:
                await self.ws.ping()
            except websockets.ConnectionClosed:
                self.ws = None
                await connect(self)

    MarketFeed.connect = connect  # type: ignore[method-assign]
    MarketFeed._dfb_ping_patched = True


class DhanMarketDataProvider:
    name = "DHAN_WS"

    def __init__(
        self,
        *,
        client_id: str,
        access_token: str,
        on_tick: TickCallback,
        instrument_key: str = "",
    ) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self._on_tick = on_tick
        self._instrument_key = instrument_key
        self._security_id: str | None = None
        self._exch_code: int = _NSE_FNO
        self._exch_seg: str = "NSE_FNO"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: RawTick | None = None
        self._health = FeedHealth(broker="DHAN_WS")
        self._lock = threading.Lock()
        self._force_reconnect = False
        self.tick_timeout_seconds = 8.0

    def reconnect_with_token(self, token: str) -> None:
        """Swap JWT on the existing WS thread. Never start a second Dhan socket."""
        tok = (token or "").strip()
        if not tok:
            raise ValueError("empty Dhan JWT")
        with self._lock:
            self.access_token = tok
            self._force_reconnect = True
            self._health.status = "RECONNECTING"
        logger.info("Dhan JWT reload queued last4=%s", tok[-4:])

    def clear_token(self) -> None:
        """Kavach deactivate — drop JWT and close this socket. No second WS."""
        with self._lock:
            self.access_token = ""
            self._force_reconnect = True
            self._health.connected = False
            self._health.status = "IDLE"
            self._health.last_error = "token_cleared"
        logger.info("Dhan JWT cleared — socket will idle")

    def _take_force_reconnect(self) -> bool:
        with self._lock:
            if not self._force_reconnect:
                return False
            self._force_reconnect = False
            return True

    def _reconnect_pending(self) -> bool:
        with self._lock:
            return bool(self._force_reconnect)

    async def _sleep_backoff(self, seconds: float) -> None:
        """Sleep, but wake immediately when Kavach pastes or clears a JWT."""
        end = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < end and not self._stop.is_set():
            if self._reconnect_pending():
                return
            await asyncio.sleep(0.2)

    def get_health(self) -> FeedHealth:
        with self._lock:
            h = FeedHealth(**self._health.to_dict())
            if h.last_received_timestamp:
                h.age_ms = max(0, now_ms() - h.last_received_timestamp)
            return h

    def get_latest(self) -> RawTick | None:
        return self._latest

    async def connect(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main, name="dfb-dhan-ws", daemon=True
        )
        self._thread.start()

    async def disconnect(self) -> None:
        self._stop.set()
        with self._lock:
            self._health.connected = False
            self._health.status = "DISCONNECTED"

    async def subscribe(self, instrument: InstrumentRef) -> None:
        self._security_id = instrument.dhan_security_id
        self._instrument_key = instrument.key
        try:
            self._exch_code = int(getattr(instrument, "dhan_exchange_code", None) or _NSE_FNO)
        except (TypeError, ValueError):
            self._exch_code = _NSE_FNO
        self._exch_seg = str(
            getattr(instrument, "dhan_exchange_segment", None) or "NSE_FNO"
        )

    async def unsubscribe(self, instrument: InstrumentRef) -> None:
        if self._security_id and instrument.dhan_security_id == self._security_id:
            self._security_id = None

    def _thread_main(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._session_loop())
            except Exception as exc:
                logger.error("Dhan WS session crashed: %s", exc, exc_info=True)
                with self._lock:
                    self._health.last_error = str(exc)[:200]
                    self._health.connected = False
                    self._health.status = "ERROR"
            if self._stop.is_set():
                break
            time.sleep(1.0)

    async def _session_loop(self) -> None:
        """One MarketFeed socket. SID change = subscribe JSON, not a new handshake."""
        backoff = 1.0
        feed: Any = None
        subscribed_sid: str | None = None
        subscribed_exch: tuple[str, int] = ("NSE_FNO", _NSE_FNO)
        connected_mono = 0.0
        recv_task: asyncio.Task[Any] | None = None
        logged_idle = False
        idle_since = 0.0

        async def _cancel_recv() -> None:
            nonlocal recv_task
            if recv_task is None:
                return
            recv_task.cancel()
            try:
                await recv_task
            except (asyncio.CancelledError, Exception):
                pass
            recv_task = None

        async def _close_feed(*, reason: str) -> None:
            nonlocal feed, subscribed_sid, connected_mono
            await _cancel_recv()
            if feed is None:
                subscribed_sid = None
                connected_mono = 0.0
                return
            logger.info(
                "Dhan WS closing reason=%s sid=%s",
                reason,
                subscribed_sid or "-",
            )
            await _await_disconnect(feed)
            feed = None
            subscribed_sid = None
            connected_mono = 0.0

        while not self._stop.is_set():
            sid = (self._security_id or "").strip()
            # Index sid 13 is always pinned. Only idle-close when auth is missing.
            if not self.client_id or not self.access_token:
                if feed is not None:
                    if idle_since <= 0:
                        idle_since = time.monotonic()
                        logger.info(
                            "Dhan WS idle grace %.1fs before close sid=%s",
                            _IDLE_GRACE_S,
                            subscribed_sid or "-",
                        )
                    if time.monotonic() - idle_since < _IDLE_GRACE_S:
                        await asyncio.sleep(0.2)
                        continue
                    await _close_feed(reason="idle_unsubscribe")
                idle_since = 0.0
                if not logged_idle:
                    logger.info("Dhan WS idle — no Dhan JWT, socket closed")
                    logged_idle = True
                with self._lock:
                    self._health.connected = False
                    self._health.status = "IDLE"
                    self._health.last_error = ""
                await asyncio.sleep(1.0)
                continue
            idle_since = 0.0
            logged_idle = False

            if self._take_force_reconnect():
                backoff = 1.0
                if feed is not None:
                    await _close_feed(reason="jwt_reload")
                continue

            if feed is not None and not _is_ws_open(feed):
                await _close_feed(reason="socket_dead")

            if feed is None:
                try:
                    from dhanhq import DhanContext
                    from dhanhq.marketfeed import MarketFeed
                except ImportError as exc:
                    logger.error("dhanhq missing: %s", exc)
                    await asyncio.sleep(5.0)
                    continue
                _patch_marketfeed_connect(MarketFeed)
                try:
                    ctx = DhanContext(
                        client_id=self.client_id, access_token=self.access_token
                    )
                    instruments: list[tuple[int, str, int]] = [
                        (_INDEX_CODE, NIFTY_INDEX_SID, _TICKER)
                    ]
                    if sid:
                        instruments.append((int(self._exch_code), str(sid), _FULL))
                    feed = MarketFeed(
                        dhan_context=ctx,
                        instruments=instruments,
                        version="v2",
                    )
                    await feed.connect()
                    if not _is_ws_open(feed):
                        raise RuntimeError("dhan ws closed immediately after connect")
                    connected_mono = time.monotonic()
                    subscribed_sid = sid or NIFTY_INDEX_SID
                    subscribed_exch = (
                        (self._exch_seg, int(self._exch_code))
                        if sid
                        else (_INDEX_SEG, _INDEX_CODE)
                    )
                    with self._lock:
                        self._health.connected = True
                        self._health.status = "HEALTHY"
                        self._health.last_error = ""
                    logger.info(
                        "Dhan WS connected index=%s option=%s seg=%s (reuse until drop)",
                        NIFTY_INDEX_SID,
                        sid or "-",
                        self._exch_seg if sid else _INDEX_SEG,
                    )
                except Exception as exc:
                    uptime = (
                        (time.monotonic() - connected_mono) if connected_mono else 0.0
                    )
                    hit_429 = is_dhan_ws_429(exc)
                    backoff = next_ws_backoff(backoff, is_429=hit_429, uptime_s=uptime)
                    logger.warning(
                        "Dhan WS connect failed sid=%s 429=%s backoff=%.1fs err=%s",
                        sid,
                        hit_429,
                        backoff,
                        exc,
                    )
                    with self._lock:
                        self._health.connected = False
                        self._health.status = (
                            "RECONNECTING_429" if hit_429 else "RECONNECTING"
                        )
                        self._health.last_error = str(exc)[:200]
                    await _close_feed(reason="connect_failed")
                    await self._sleep_backoff(backoff)
                    continue

            option_now = sid or ""
            option_had = "" if subscribed_sid == NIFTY_INDEX_SID else (subscribed_sid or "")
            if option_now != option_had:
                try:
                    await _cancel_recv()
                    if option_had:
                        await _send_sid(
                            feed,
                            option_had,
                            unsub=True,
                            exch_code=subscribed_exch[1],
                            exch_seg=subscribed_exch[0],
                        )
                    if option_now:
                        await _send_sid(
                            feed,
                            option_now,
                            unsub=False,
                            exch_code=int(self._exch_code),
                            exch_seg=self._exch_seg,
                        )
                    await _send_index(feed, unsub=False)
                    logger.info(
                        "Dhan WS resubscribed same-socket index=%s old_opt=%s new_opt=%s/%s",
                        NIFTY_INDEX_SID,
                        option_had or "-",
                        option_now or "-",
                        self._exch_seg if option_now else _INDEX_SEG,
                    )
                    subscribed_sid = option_now or NIFTY_INDEX_SID
                    subscribed_exch = (
                        (self._exch_seg, int(self._exch_code))
                        if option_now
                        else (_INDEX_SEG, _INDEX_CODE)
                    )
                except Exception as exc:
                    logger.warning("Dhan WS resubscribe failed: %s", exc)
                    await _close_feed(reason="resubscribe_failed")
                    await asyncio.sleep(min(backoff, 5.0))
                    continue

            try:
                if recv_task is None:
                    recv_task = asyncio.create_task(feed.get_instrument_data())
                done, _ = await asyncio.wait(
                    {recv_task}, timeout=self.tick_timeout_seconds
                )
                if not done:
                    with self._lock:
                        if self._health.connected:
                            self._health.status = "HEALTHY_NO_TICK"
                    if connected_mono and (
                        time.monotonic() - connected_mono
                    ) >= _STABLE_CONNECT_S:
                        backoff = 1.0
                    continue
                try:
                    tick = recv_task.result()
                except Exception:
                    recv_task = None
                    raise
                recv_task = None
                parsed = extract_dhan_option_tick(tick)
                if not parsed:
                    continue
                psid = str(parsed.get("security_id") or "")
                is_index = psid == NIFTY_INDEX_SID
                if (
                    not is_index
                    and parsed.get("security_id")
                    and sid
                    and psid != str(sid)
                ):
                    continue
                recv = now_ms()
                inst_key = (
                    "NIFTY_INDEX"
                    if is_index
                    else (self._instrument_key or f"SID_{sid or psid}")
                )
                broker_id = NIFTY_INDEX_SID if is_index else str(sid or psid)
                raw = RawTick(
                    source=Source.DHAN_WS.value,
                    instrument=inst_key,
                    ltp=float(parsed["ltp"]),
                    bid=parsed.get("bid"),
                    ask=parsed.get("ask"),
                    volume=parsed.get("volume"),
                    market_timestamp=0,
                    received_timestamp=recv,
                    broker_id=broker_id,
                )
                self._latest = raw
                with self._lock:
                    self._health.packet_count += 1
                    self._health.last_received_timestamp = recv
                    self._health.status = "HEALTHY"
                if connected_mono and (
                    time.monotonic() - connected_mono
                ) >= _STABLE_CONNECT_S:
                    backoff = 1.0
                self._on_tick(raw)
            except Exception as exc:
                uptime = (time.monotonic() - connected_mono) if connected_mono else 0.0
                hit_429 = is_dhan_ws_429(exc)
                backoff = next_ws_backoff(backoff, is_429=hit_429, uptime_s=uptime)
                logger.warning(
                    "Dhan WS error sid=%s uptime=%.2fs 429=%s backoff=%.1fs err=%s",
                    sid,
                    uptime,
                    hit_429,
                    backoff,
                    exc,
                )
                with self._lock:
                    self._health.connected = False
                    self._health.status = (
                        "RECONNECTING_429" if hit_429 else "RECONNECTING"
                    )
                    self._health.last_error = str(exc)[:200]
                await _close_feed(reason="recv_error")
                await self._sleep_backoff(backoff)

        await _close_feed(reason="stop")


def dhan_rest_option_quote(
    *,
    client_id: str,
    access_token: str,
    security_id: str,
    instrument_key: str,
    exchange_segment: str = "NSE_FNO",
):
    """REST recovery snapshot (not realtime). Detects HTTP 429 for cooldown."""
    from datafeedbot.rest_guard import RestQuoteOutcome

    url = "https://api.dhan.co/v2/marketfeed/ltp"
    headers = {
        "access-token": access_token,
        "client-id": client_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        sid = int(float(security_id))
    except ValueError:
        return RestQuoteOutcome(error="bad_security_id")
    seg = (exchange_segment or "NSE_FNO").strip() or "NSE_FNO"
    body = {seg: [sid]}
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.post(url, headers=headers, json=body)
            if r.status_code == 429:
                logger.warning("Dhan REST quote HTTP 429")
                return RestQuoteOutcome(is_429=True, error="http_429")
            r.raise_for_status()
            payload = r.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 429:
            return RestQuoteOutcome(is_429=True, error="http_429")
        logger.warning("Dhan REST quote failed: %s", exc)
        return RestQuoteOutcome(error=str(exc)[:160])
    except Exception as exc:
        logger.warning("Dhan REST quote failed: %s", exc)
        return RestQuoteOutcome(error=str(exc)[:160])
    data = payload.get("data") if isinstance(payload, dict) else None
    item = None
    if isinstance(data, dict):
        block = data.get(seg) or data.get("NSE_FNO") or data.get("MCX_COMM") or {}
        if isinstance(block, dict):
            item = block.get(str(sid)) or block.get(sid)
    if not isinstance(item, dict):
        return RestQuoteOutcome(error="empty_payload")
    ltp = item.get("last_price") or item.get("LTP") or item.get("ltp")
    try:
        ltp_f = float(ltp)
    except (TypeError, ValueError):
        return RestQuoteOutcome(error="bad_ltp")
    if ltp_f <= 0:
        return RestQuoteOutcome(error="ltp_non_positive")
    return RestQuoteOutcome(
        tick=RawTick(
            source=Source.DHAN_REST.value,
            instrument=instrument_key,
            ltp=ltp_f,
            bid=None,
            ask=None,
            market_timestamp=0,
            received_timestamp=now_ms(),
            broker_id=str(sid),
        )
    )
