"""Shoonya market-data adapter — one WebSocket per user + throttled REST fallback.

Docs:
  wss://api.shoonya.com/NorenWSAPI/
  FAQ: only ONE websocket connection per user.
Orders must NOT share this REST poller — recovery quotes are rate-limited separately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from datafeedbot.brokers.shoonya.instruments import NIFTY_INDEX_TOKEN, resolve_nifty_option
from datafeedbot.models import FeedHealth, InstrumentRef, RawTick, Source
from datafeedbot.sequence import now_ms

logger = logging.getLogger("datafeedbot.brokers.shoonya")

_WS_URL = "wss://api.shoonya.com/NorenWSAPI/"
_REST_HOST = "https://api.shoonya.com/NorenWClientAPI/"
TickCallback = Callable[[RawTick], None]


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def extract_shoonya_tick(msg: dict[str, Any], instrument_key: str, *, source: str) -> RawTick | None:
    if not isinstance(msg, dict):
        return None
    ltp = _f(msg.get("lp") or msg.get("c"))
    if ltp is None:
        return None
    bid = _f(msg.get("bp1") or msg.get("bp"))
    ask = _f(msg.get("sp1") or msg.get("sp"))
    volume = None
    try:
        if msg.get("v") is not None:
            volume = int(float(msg.get("v")))
    except (TypeError, ValueError):
        volume = None
    market_ts = 0
    for key in ("ft", "ltt", "ctm"):
        val = msg.get(key)
        if val is None:
            continue
        try:
            n = float(val)
            market_ts = int(n * 1000) if n < 1e12 else int(n)
            break
        except (TypeError, ValueError):
            continue
    token = msg.get("tk") or msg.get("token")
    return RawTick(
        source=source,
        instrument=instrument_key,
        ltp=float(ltp),
        bid=bid,
        ask=ask,
        volume=volume,
        market_timestamp=market_ts,
        received_timestamp=now_ms(),
        broker_id=str(token) if token is not None else None,
    )


class ShoonyaMarketDataProvider:
    name = "SHOONYA_WS"

    def __init__(
        self,
        *,
        uid: str,
        access_token: str,
        on_tick: TickCallback,
        actid: str = "",
    ) -> None:
        self.uid = (uid or "").strip()
        self.actid = (actid or uid or "").strip()
        self.access_token = (access_token or "").strip()
        self._on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: RawTick | None = None
        self._health = FeedHealth(broker="SHOONYA_WS")
        self._lock = threading.Lock()
        self._force_reconnect = False
        self._option_token: str | None = None
        self._option_exch = "NFO"
        self._instrument_key = ""
        self._subscribed_keys: set[str] = set()
        self._rest_lock = threading.Lock()
        self._last_rest_mono = 0.0
        self._rest_min_interval_s = 1.25  # keep well under market-data REST pressure

    def get_health(self) -> FeedHealth:
        with self._lock:
            h = FeedHealth(**self._health.to_dict())
            if h.last_received_timestamp:
                h.age_ms = max(0, now_ms() - h.last_received_timestamp)
            return h

    def get_latest(self) -> RawTick | None:
        return self._latest

    def reconnect_with_token(self, token: str, uid: str = "") -> None:
        tok = (token or "").strip()
        if not tok:
            raise ValueError("empty Shoonya access token")
        with self._lock:
            self.access_token = tok
            if uid:
                self.uid = uid.strip()
                self.actid = uid.strip()
            self._force_reconnect = True
            self._health.status = "RECONNECTING"
        logger.info("Shoonya token reload queued last4=%s", tok[-4:])

    def clear_token(self) -> None:
        with self._lock:
            self.access_token = ""
            self._force_reconnect = True
            self._health.connected = False
            self._health.status = "IDLE"
            self._health.last_error = "token_cleared"
        logger.info("Shoonya token cleared — socket will idle")

    def _take_force_reconnect(self) -> bool:
        with self._lock:
            if not self._force_reconnect:
                return False
            self._force_reconnect = False
            return True

    def _reconnect_pending(self) -> bool:
        with self._lock:
            return bool(self._force_reconnect)

    async def connect(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main, name="dfb-shoonya-ws", daemon=True
        )
        self._thread.start()

    async def disconnect(self) -> None:
        self._stop.set()
        with self._lock:
            self._health.connected = False
            self._health.status = "DISCONNECTED"

    async def subscribe(self, instrument: InstrumentRef) -> None:
        token = getattr(instrument, "shoonya_token", None)
        exch = str(getattr(instrument, "shoonya_exchange", None) or "NFO")
        if not token and instrument.strike:
            hit = resolve_nifty_option(
                int(instrument.strike),
                str(instrument.option_type or "CE"),
                str(instrument.expiry or ""),
            )
            if hit:
                token = hit.get("token")
                exch = str(hit.get("exch") or "NFO")
        self._option_token = str(token).strip() if token else None
        self._option_exch = exch
        self._instrument_key = instrument.key
        # Ask running loop to refresh subscriptions without new socket when possible.
        with self._lock:
            self._force_reconnect = False
        self._subscribed_keys = set()
        logger.info(
            "Shoonya subscribe queued key=%s token=%s exch=%s",
            self._instrument_key,
            self._option_token,
            self._option_exch,
        )

    async def unsubscribe(self, instrument: InstrumentRef) -> None:
        if self._instrument_key and instrument.key == self._instrument_key:
            self._option_token = None
            self._instrument_key = ""
            self._subscribed_keys = set()

    def _thread_main(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._session_loop())
            except Exception as exc:
                logger.error("Shoonya WS session crashed: %s", exc, exc_info=True)
                with self._lock:
                    self._health.last_error = str(exc)[:200]
                    self._health.connected = False
                    self._health.status = "ERROR"
            if self._stop.is_set():
                break
            time.sleep(1.0)

    async def _session_loop(self) -> None:
        import websockets
        from websockets.exceptions import ConnectionClosed

        backoff = 1.5
        while not self._stop.is_set():
            token = (self.access_token or "").strip()
            uid = (self.uid or "").strip()
            if not token or not uid:
                with self._lock:
                    self._health.status = "NO_CREDENTIALS"
                    self._health.connected = False
                await asyncio.sleep(2.0)
                continue

            ws = None
            opened = time.monotonic()
            try:
                ws = await websockets.connect(
                    _WS_URL,
                    ping_interval=20,
                    ping_timeout=90,
                    close_timeout=3,
                    max_queue=256,
                )
                await ws.send(
                    json.dumps(
                        {
                            "t": "a",
                            "uid": uid,
                            "actid": self.actid or uid,
                            "source": "API",
                            "accesstoken": token,
                        }
                    )
                )
                # Wait for connect ack
                ack_ok = False
                for _ in range(20):
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    msg = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                    if str(msg.get("t") or "") == "ak":
                        ack_ok = str(msg.get("s") or "").upper() == "OK"
                        if not ack_ok:
                            raise RuntimeError(f"Shoonya WS auth rejected: {msg}")
                        break
                if not ack_ok:
                    raise RuntimeError("Shoonya WS connect ack missing")

                with self._lock:
                    self._health.connected = True
                    self._health.status = "LIVE"
                    self._health.last_error = ""
                backoff = 1.5
                await self._send_subscriptions(ws)
                logger.info("Shoonya WS connected uid=%s", uid)

                while not self._stop.is_set():
                    if self._take_force_reconnect():
                        logger.info("Shoonya WS forced reconnect")
                        break
                    # Refresh subscribe set if option token changed
                    desired = self._desired_keys()
                    if desired != self._subscribed_keys:
                        await self._send_subscriptions(ws)
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    tcode = str(msg.get("t") or "")
                    if tcode in {"tk", "tf", "dk", "df"}:
                        self._handle_feed_msg(msg)
                    elif tcode == "ck":
                        # heartbeat / connect keep
                        continue
            except Exception as exc:
                uptime = time.monotonic() - opened
                with self._lock:
                    self._health.connected = False
                    self._health.status = "RECONNECTING"
                    self._health.last_error = str(exc)[:200]
                logger.warning("Shoonya WS error uptime=%.1fs err=%s", uptime, exc)
                # Close dead socket cleanly, then open a fresh one (still only one at a time).
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                # Throttled REST snapshot so Kavach still has LTP while WS is down.
                try:
                    self._rest_snapshot_fallback()
                except Exception as rest_exc:
                    logger.debug("Shoonya REST fallback skipped: %s", rest_exc)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 1.7)
                continue
            finally:
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                self._subscribed_keys = set()
                with self._lock:
                    self._health.connected = False

    def _desired_keys(self) -> set[str]:
        keys = {f"NSE|{NIFTY_INDEX_TOKEN}"}
        if self._option_token:
            keys.add(f"{self._option_exch}|{self._option_token}")
        return keys

    async def _send_subscriptions(self, ws: Any) -> None:
        keys = sorted(self._desired_keys())
        if not keys:
            return
        # One multiplexed subscribe frame (Shoonya allows many tokens on one WS).
        payload = {"t": "t", "k": "#".join(keys)}
        await ws.send(json.dumps(payload))
        self._subscribed_keys = set(keys)
        logger.info("Shoonya WS subscribed %s", payload["k"])

    def _handle_feed_msg(self, msg: dict[str, Any]) -> None:
        token = str(msg.get("tk") or msg.get("token") or "")
        exch = str(msg.get("e") or msg.get("exch") or "").upper()
        if token == NIFTY_INDEX_TOKEN or (exch in {"NSE", ""} and token == NIFTY_INDEX_TOKEN):
            raw = extract_shoonya_tick(msg, "NIFTY_INDEX", source=Source.SHOONYA_WS.value)
            if raw:
                raw = RawTick(
                    source=Source.SHOONYA_WS.value,
                    instrument="NIFTY_INDEX",
                    ltp=raw.ltp,
                    bid=raw.bid,
                    ask=raw.ask,
                    volume=raw.volume,
                    market_timestamp=raw.market_timestamp,
                    received_timestamp=raw.received_timestamp,
                    broker_id=NIFTY_INDEX_TOKEN,
                )
                self._emit(raw)
            return
        if self._option_token and token == str(self._option_token):
            key = self._instrument_key or f"NIFTY_OPTION_{token}"
            raw = extract_shoonya_tick(msg, key, source=Source.SHOONYA_WS.value)
            if raw:
                self._emit(raw)

    def _emit(self, raw: RawTick) -> None:
        self._latest = raw
        with self._lock:
            self._health.last_received_timestamp = raw.received_timestamp
            self._health.connected = True
            self._health.status = "LIVE"
        try:
            if raw.instrument == "NIFTY_INDEX":
                from datafeedbot.nifty_cache import write_nifty_ltp_cache

                write_nifty_ltp_cache(float(raw.ltp), source=str(raw.source))
        except Exception:
            pass
        try:
            self._on_tick(raw)
        except Exception as exc:
            logger.debug("on_tick failed: %s", exc)

    def _rest_call(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Low-priority market REST. Never used for orders."""
        body = ("jData=" + json.dumps(payload, separators=(",", ":"))).encode("utf-8")
        req = urllib.request.Request(
            _REST_HOST + endpoint,
            data=body,
            headers={
                "Content-Type": "text/plain",
                "Authorization": f"Bearer {self.access_token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")

    def _rest_snapshot_fallback(self) -> None:
        with self._rest_lock:
            now = time.monotonic()
            if (now - self._last_rest_mono) < self._rest_min_interval_s:
                return
            self._last_rest_mono = now
        if not self.access_token or not self.uid:
            return
        # Index first
        try:
            q = self._rest_call(
                "GetQuotes",
                {"uid": self.uid, "exch": "NSE", "token": NIFTY_INDEX_TOKEN},
            )
            raw = extract_shoonya_tick(q, "NIFTY_INDEX", source=Source.SHOONYA_REST.value)
            if raw:
                raw = RawTick(
                    source=Source.SHOONYA_REST.value,
                    instrument="NIFTY_INDEX",
                    ltp=raw.ltp,
                    bid=raw.bid,
                    ask=raw.ask,
                    volume=raw.volume,
                    market_timestamp=raw.market_timestamp,
                    received_timestamp=now_ms(),
                    broker_id=NIFTY_INDEX_TOKEN,
                )
                self._emit(raw)
        except Exception as exc:
            logger.debug("Shoonya REST index failed: %s", exc)
        # Active option (optional; spaced by lock above)
        if self._option_token and self._instrument_key:
            try:
                q = self._rest_call(
                    "GetQuotes",
                    {
                        "uid": self.uid,
                        "exch": self._option_exch or "NFO",
                        "token": str(self._option_token),
                    },
                )
                raw = extract_shoonya_tick(
                    q, self._instrument_key, source=Source.SHOONYA_REST.value
                )
                if raw:
                    self._emit(raw)
            except Exception as exc:
                logger.debug("Shoonya REST option failed: %s", exc)
