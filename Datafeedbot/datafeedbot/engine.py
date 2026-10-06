from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from datafeedbot.alerts import AlertSink
from datafeedbot.brokers import create_provider, list_providers, planned_providers
from datafeedbot.brokers.base import MarketDataProvider
from datafeedbot.config import Settings
from datafeedbot.instruments import (
    ZerodhaInstrumentCache,
    dhan_feed_segment,
    resolve_by_security_id,
    resolve_dhan,
)
from datafeedbot.ipc import IpcServer
from datafeedbot.models import InstrumentRef, RawTick, Status
from datafeedbot.normalization import normalize_raw
from datafeedbot.rest_guard import RestRateGuard
from datafeedbot.sequence import SequenceAssigner, now_ms
from datafeedbot.state import UnifiedState
from datafeedbot.validation import is_cross_broker_mismatch, validate_tick
from datafeedbot.dhan_jwt_watch import start_dhan_jwt_watch
from datafeedbot.shoonya_token_watch import start_shoonya_token_watch
from datafeedbot.zerodha_token_watch import start_zerodha_token_watch

logger = logging.getLogger("datafeedbot.engine")
log_md = logging.getLogger("datafeedbot.market_data")
log_health = logging.getLogger("datafeedbot.feed_health")
log_fail = logging.getLogger("datafeedbot.failover")
log_perf = logging.getLogger("datafeedbot.performance")


class DatafeedEngine:
    """Hot path: tick → validate → normalize → sequence → state → IPC.

    Trading / trail / OMS logic lives in Blitz/GoBot — never here.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sequencer = SequenceAssigner()
        self.state = UnifiedState(self.sequencer)
        self.ipc = IpcServer(settings.ipc_socket, self.state.snapshot)
        self.ipc.set_control_handler(self._handle_control)
        self._z_cache = ZerodhaInstrumentCache()
        self._providers: dict[str, MarketDataProvider] = {}
        self._dhan: MarketDataProvider | None = None
        self._zerodha: MarketDataProvider | None = None
        self._shoonya: MarketDataProvider | None = None
        self._rest_guard = RestRateGuard(
            min_interval_ms=max(3000, int(settings.rest_recovery_min_interval_ms)),
            max_interval_ms=60_000,
            cooldown_on_429_ms=30_000,
        )
        self._alerts = AlertSink(
            token=settings.telegram_token,
            chat_id=settings.telegram_chat_id,
            min_repeat_seconds=120.0,
        )
        self._recovery_stop = threading.Event()
        self._recovery_thread: threading.Thread | None = None
        self._zerodha_watch_stop: threading.Event | None = None
        self._dhan_watch_stop: threading.Event | None = None
        self._shoonya_watch_stop: threading.Event | None = None
        self._dual_stale_since = 0.0
        self._last_source: str | None = None
        self._tick_counts: dict[str, int] = {}
        self._reject_counts: dict[str, int] = {}
        self.metrics: dict[str, list[float]] = {
            "process_ms": [],
            "ipc_clients": [],
        }
        self._tick_ring: list[dict[str, Any]] = []
        self._tick_ring_max = 2000
        self._last_gap_log_mono = 0.0
        runtime = getattr(settings, "runtime_root", None)
        if runtime is None:
            # Derive from ipc socket parent (…/runtime/ipc.sock → runtime)
            runtime = Path(settings.ipc_socket).parent
        self._tick_journal_dir = Path(runtime) / "Logs" / "ticks"

    def start(self) -> None:
        self.ipc.start()
        logger.info(
            "broker registry active=%s planned=%s",
            list_providers(),
            list(planned_providers()),
        )
        if self.settings.enable_dhan:
            self._dhan = create_provider(
                "dhan",
                client_id=self.settings.dhan_client_code,
                access_token=self.settings.dhan_access_token,
                on_tick=self.on_raw_tick,
            )
            self._providers["dhan"] = self._dhan
        if self.settings.enable_zerodha:
            self._zerodha = create_provider(
                "zerodha",
                api_key=self.settings.zerodha_api_key,
                access_token=self.settings.zerodha_access_token,
                on_tick=self.on_raw_tick,
                instrument_cache=self._z_cache,
            )
            self._providers["zerodha"] = self._zerodha
        if self.settings.enable_shoonya:
            self._shoonya = create_provider(
                "shoonya",
                uid=self.settings.shoonya_uid,
                access_token=self.settings.shoonya_access_token,
                on_tick=self.on_raw_tick,
                actid=self.settings.shoonya_uid,
            )
            self._providers["shoonya"] = self._shoonya
        import asyncio

        async def _boot() -> None:
            for prov in self._providers.values():
                await prov.connect()

        try:
            asyncio.run(_boot())
        except RuntimeError:
            loop = asyncio.get_event_loop()
            loop.create_task(_boot())
        if self._zerodha is not None and self.settings.zerodha_access_token:
            try:
                n = self._zerodha.fetch_and_load_instruments()  # type: ignore[attr-defined]
                logger.info("Zerodha NFO instruments loaded count=%s", n)
            except Exception as exc:
                logger.warning("Zerodha instrument master load failed: %s", exc)
        self._recovery_stop.clear()
        self._recovery_thread = threading.Thread(
            target=self._recovery_loop, name="dfb-recovery", daemon=True
        )
        self._recovery_thread.start()
        self._index_keep_thread = threading.Thread(
            target=self._index_keepalive_loop, name="dfb-nifty-keep", daemon=True
        )
        self._index_keep_thread.start()
        logger.info("DatafeedEngine started providers=%s", list(self._providers))
        log_health.info(
            "engine_start providers=%s rest_min_ms=%s alerts=%s",
            list(self._providers),
            self._rest_guard.min_interval_ms,
            self._alerts.enabled,
        )
        self._zerodha_watch_stop = start_zerodha_token_watch(
            runtime_root=self.settings.runtime_root,
            on_token=self.apply_zerodha_token,
            initial_token=self.settings.zerodha_access_token,
        )
        self._dhan_watch_stop = start_dhan_jwt_watch(
            runtime_root=self.settings.runtime_root,
            on_token=self.apply_dhan_token,
            initial_token=self.settings.dhan_access_token,
        )
        self._shoonya_watch_stop = start_shoonya_token_watch(
            runtime_root=self.settings.runtime_root,
            on_session=self.apply_shoonya_session,
            initial_token=self.settings.shoonya_access_token,
        )

    def apply_dhan_token(self, token: str) -> None:
        """Always apply the file JWT. Do not keep an old token in memory."""
        tok = (token or "").strip()
        if not tok:
            prev = (self.settings.dhan_access_token or "").strip()
            prev4 = prev[-4:] if len(prev) >= 4 else "-"
            self.settings.dhan_access_token = ""
            clearer = getattr(self._dhan, "clear_token", None) if self._dhan is not None else None
            if callable(clearer):
                clearer()
            logger.info("Dhan JWT cleared — WS will idle (was %s)", prev4)
            return
        last4 = tok[-4:]
        prev = (self.settings.dhan_access_token or "").strip()
        prev4 = prev[-4:] if len(prev) >= 4 else "-"
        self.settings.dhan_access_token = tok
        if self._dhan is None:
            logger.info("Dhan JWT updated last4=%s (provider not started, was %s)", last4, prev4)
            return
        reconnect = getattr(self._dhan, "reconnect_with_token", None)
        if reconnect is None:
            logger.warning("Dhan provider has no reconnect_with_token")
            return
        try:
            reconnect(tok)
            logger.info("Dhan WS forced reconnect last4=%s (was %s)", last4, prev4)
        except Exception as exc:
            logger.error("Dhan WS reconnect failed last4=%s err=%s", last4, exc)
            self._schedule_self_restart("dhan_reconnect_failed")

    def apply_zerodha_token(self, token: str) -> None:
        """Always apply the Kavach Kite token. Never keep the old one in memory."""
        tok = (token or "").strip()
        if not tok:
            prev = (self.settings.zerodha_access_token or "").strip()
            prev4 = prev[-4:] if len(prev) >= 4 else "-"
            self.settings.zerodha_access_token = ""
            clearer = getattr(self._zerodha, "clear_token", None) if self._zerodha is not None else None
            if callable(clearer):
                clearer()
            logger.info("Zerodha token cleared — WS will idle (was %s)", prev4)
            return
        last4 = tok[-4:]
        prev = (self.settings.zerodha_access_token or "").strip()
        prev4 = prev[-4:] if len(prev) >= 4 else "-"
        self.settings.zerodha_access_token = tok
        if self._zerodha is None:
            logger.info("Zerodha token updated last4=%s (provider not started, was %s)", last4, prev4)
            return
        reconnect = getattr(self._zerodha, "reconnect_with_token", None)
        if reconnect is None:
            logger.warning("Zerodha provider has no reconnect_with_token")
            return
        try:
            reconnect(tok)
            logger.info("Zerodha WS forced reconnect last4=%s (was %s)", last4, prev4)
        except Exception as exc:
            logger.error("Zerodha WS reconnect failed last4=%s err=%s", last4, exc)
            self._schedule_self_restart("zerodha_reconnect_failed")
            return
        self._schedule_zerodha_connect_watch(last4)


    def apply_shoonya_session(self, uid: str, token: str) -> None:
        """Hot-reload Shoonya market WS only. Never places orders."""
        tok = (token or "").strip()
        user = (uid or "").strip()
        if not tok:
            prev = (self.settings.shoonya_access_token or "").strip()
            prev4 = prev[-4:] if len(prev) >= 4 else "-"
            self.settings.shoonya_access_token = ""
            if user:
                self.settings.shoonya_uid = user
            clearer = getattr(self._shoonya, "clear_token", None) if self._shoonya is not None else None
            if callable(clearer):
                try:
                    clearer()
                except Exception as exc:
                    logger.warning("Shoonya clear_token failed: %s", exc)
            logger.warning("Shoonya token cleared (was %s)", prev4)
            return
        prev = (self.settings.shoonya_access_token or "").strip()
        prev4 = prev[-4:] if len(prev) >= 4 else "-"
        last4 = tok[-4:]
        self.settings.shoonya_access_token = tok
        if user:
            self.settings.shoonya_uid = user
        if self._shoonya is None:
            logger.info(
                "Shoonya token updated last4=%s uid=%s (provider not started, was %s)",
                last4,
                user or "-",
                prev4,
            )
            return
        reconnect = getattr(self._shoonya, "reconnect_with_token", None)
        if reconnect is None:
            logger.warning("Shoonya provider has no reconnect_with_token")
            return
        try:
            reconnect(tok, uid=user)
            logger.info("Shoonya WS forced reconnect last4=%s uid=%s (was %s)", last4, user or "-", prev4)
        except Exception as exc:
            logger.error("Shoonya WS reconnect failed last4=%s err=%s", last4, exc)
            self._schedule_self_restart("shoonya_reconnect_failed")

    def _schedule_zerodha_connect_watch(self, last4: str) -> None:
        """If KiteTicker does not come up after a Kavach paste, one service restart."""
        import threading

        def _check() -> None:
            import time
            time.sleep(12.0)
            z = self._zerodha
            if z is None:
                return
            health = getattr(z, "get_health", None)
            connected = False
            if callable(health):
                try:
                    connected = bool(getattr(health(), "connected", False))
                except Exception:
                    connected = False
            if connected:
                logger.info("Zerodha WS live after paste last4=%s", last4)
                return
            logger.warning("Zerodha WS still down 12s after last4=%s — restarting datafeedbot once", last4)
            self._schedule_self_restart("zerodha_not_connected")

        threading.Thread(target=_check, name="dfb-z-connect-watch", daemon=True).start()

    def _schedule_self_restart(self, reason: str) -> None:
        """Restart the one datafeedbot.service. Never start a second process."""
        import subprocess
        import time
        from pathlib import Path

        flag = Path("/home/ubuntu/Datafeedbot_Runtime/Data/shared/self_restart.lock")
        try:
            flag.parent.mkdir(parents=True, exist_ok=True)
            if flag.is_file() and time.time() - flag.stat().st_mtime < 120:
                logger.warning("skip self-restart reason=%s (cooldown)", reason)
                return
            flag.write_text(reason, encoding="utf-8")
        except Exception as exc:
            logger.warning("self-restart lock failed: %s", exc)
        logger.warning("systemctl restart datafeedbot.service reason=%s", reason)
        try:
            subprocess.Popen(
                ["sudo", "-n", "systemctl", "restart", "datafeedbot.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.error("self-restart failed: %s", exc)

    def stop(self) -> None:
        if self._zerodha_watch_stop is not None:
            self._zerodha_watch_stop.set()
        if self._dhan_watch_stop is not None:
            self._dhan_watch_stop.set()
        if self._shoonya_watch_stop is not None:
            self._shoonya_watch_stop.set()
        self._recovery_stop.set()
        import asyncio

        async def _down() -> None:
            for prov in self._providers.values():
                await prov.disconnect()

        try:
            asyncio.run(_down())
        except Exception:
            pass
        self.ipc.stop()

    def _ingest_index_tick(self, tick: RawTick) -> None:
        """Sid 13 is a side-channel: write Kavach cache + publish IPC, never replace option tape."""
        try:
            from datafeedbot.nifty_cache import write_nifty_ltp_cache

            write_nifty_ltp_cache(float(tick.ltp), source=str(tick.source or "dhan_websocket"))
        except Exception as exc:
            logger.warning("NIFTY cache write failed: %s", exc)
        try:
            md = normalize_raw(tick, status=Status.LIVE.value)
            md.instrument = "NIFTY_INDEX"
            md.sequence = self.sequencer.next()
            md.sequenced_timestamp = now_ms()
            self.ipc.publish(md)
            src = str(md.source or tick.source or "?")
            self._tick_counts[src] = self._tick_counts.get(src, 0) + 1
        except Exception as exc:
            logger.debug("index IPC publish skipped: %s", exc)

    def on_raw_tick(self, tick: RawTick) -> None:
        if (
            str(tick.broker_id or "") in {"13", "256265", "26000"}
            or str(tick.instrument or "") == "NIFTY_INDEX"
        ):
            self._ingest_index_tick(tick)
            return
        t0 = time.perf_counter()
        active = self.state.get_active()
        expected = active.key if active else None
        result = validate_tick(
            tick,
            expected_instrument=expected,
            max_age_ms=max(self.settings.stale_ms * 3, 10_000),
        )
        if not result.ok:
            reason = str(result.reason or "reject")
            self._reject_counts[reason] = self._reject_counts.get(reason, 0) + 1
            logger.debug(
                "reject tick source=%s reason=%s", tick.source, result.reason
            )
            return
        md = normalize_raw(tick, status=result.status)
        latest = self.state.latest()
        if (
            latest is not None
            and latest.source != md.source
            and is_cross_broker_mismatch(md.ltp, latest.ltp)
        ):
            self._reject_counts["cross_broker_mismatch"] = (
                self._reject_counts.get("cross_broker_mismatch", 0) + 1
            )
            log_md.warning(
                "reject cross-broker LTP mismatch incoming=%s/%s current=%s/%s",
                md.source,
                md.ltp,
                latest.source,
                latest.ltp,
            )
            return
        accepted = self.state.try_accept(md)
        if accepted is None:
            return
        self.ipc.publish(accepted)
        self._journal_tick(accepted)
        src = str(accepted.source or tick.source or "?")
        self._tick_counts[src] = self._tick_counts.get(src, 0) + 1
        if src != self._last_source:
            log_md.info(
                "source_switch %s -> %s instrument=%s seq=%s ltp=%s",
                self._last_source,
                src,
                accepted.instrument,
                accepted.sequence,
                accepted.ltp,
            )
            self._last_source = src
        # WS live again clears dual-stale alarm window
        if "WS" in src.upper():
            if self._dual_stale_since > 0:
                log_fail.info("WS recovered source=%s — dual_ws_stale cleared", src)
                self._alerts.notify(
                    "ws_recovered",
                    f"WS recovered via {src}\ninstrument={accepted.instrument}",
                )
            self._dual_stale_since = 0.0
        dt = (time.perf_counter() - t0) * 1000.0
        bucket = self.metrics["process_ms"]
        bucket.append(dt)
        if len(bucket) > 5000:
            del bucket[:2500]
        for prov in self._providers.values():
            self.state.update_health(prov.get_health())

    def _handle_control(self, msg: dict[str, Any]) -> dict[str, Any]:
        action = str(msg.get("action") or "")
        if action == "ping":
            return {"type": "pong", "ts": now_ms()}
        if action == "get_state":
            return {"type": "state", "data": self.ops_snapshot()}
        if action == "subscribe":
            ref = self._resolve_subscribe(msg)
            self._apply_subscribe(ref)
            return {
                "type": "subscribed",
                "instrument": ref.key,
                "dhan_security_id": ref.dhan_security_id,
                "zerodha_instrument_token": ref.zerodha_instrument_token,
                "shoonya_token": ref.shoonya_token,
            }
        if action == "unsubscribe":
            active = self.state.get_active()
            if active:
                self._apply_unsubscribe(active)
            self.state.set_active(None)
            return {"type": "unsubscribed"}
        return {"type": "error", "error": f"unknown_action:{action}"}

    def _resolve_subscribe(self, msg: dict[str, Any]) -> InstrumentRef:
        underlying = str(msg.get("underlying") or "NIFTY").upper()
        strike = int(msg.get("strike") or 0)
        option_type = str(msg.get("option_type") or "CE").upper()
        expiry = str(msg.get("expiry") or "")
        dhan_sid = msg.get("dhan_security_id")
        z_tok = msg.get("zerodha_instrument_token")
        resolved = None
        exch_code = 2
        exch_seg = "NSE_FNO"
        if self.settings.instruments_csv:
            if dhan_sid:
                resolved = resolve_by_security_id(
                    self.settings.instruments_csv, str(dhan_sid)
                )
            if resolved is None and strike:
                resolved = resolve_dhan(
                    self.settings.instruments_csv,
                    strike=strike,
                    option_type=option_type,
                    expiry=expiry,
                )
            if resolved:
                dhan_sid = resolved.security_id
                if not expiry:
                    expiry = resolved.expiry_label
                if not strike:
                    try:
                        strike = int(resolved.strike)
                    except (TypeError, ValueError):
                        strike = strike
                if resolved.underlying:
                    underlying = resolved.underlying.upper()
                exch_code, exch_seg = dhan_feed_segment(
                    resolved.exchange, resolved.segment
                )
        if z_tok is None and strike and underlying == "NIFTY":
            z_tok = self._z_cache.resolve(strike, option_type, expiry)
            logger.info(
                "zerodha resolve strike=%s opt=%s expiry=%s token=%s",
                strike,
                option_type,
                expiry,
                z_tok,
            )
        s_tok = msg.get("shoonya_token")
        s_exch = str(msg.get("shoonya_exchange") or "NFO")
        if (not s_tok) and strike and underlying == "NIFTY":
            try:
                from datafeedbot.brokers.shoonya.instruments import resolve_nifty_option

                hit = resolve_nifty_option(int(strike), option_type, expiry)
                if hit:
                    s_tok = hit.get("token")
                    s_exch = str(hit.get("exch") or "NFO")
                    logger.info(
                        "shoonya resolve strike=%s opt=%s expiry=%s token=%s",
                        strike,
                        option_type,
                        expiry,
                        s_tok,
                    )
            except Exception as exc:
                logger.warning("shoonya resolve failed: %s", exc)
        return InstrumentRef(
            underlying=underlying,
            strike=strike,
            option_type=option_type,
            expiry=expiry,
            dhan_security_id=str(dhan_sid) if dhan_sid else None,
            zerodha_instrument_token=int(z_tok) if z_tok is not None else None,
            dhan_exchange_code=int(exch_code),
            dhan_exchange_segment=str(exch_seg),
            shoonya_token=str(s_tok) if s_tok else None,
            shoonya_exchange=s_exch,
        )

    def _apply_subscribe(self, ref: InstrumentRef) -> None:
        old = self.state.get_active()
        self.state.set_active(ref)
        import asyncio

        async def _sub() -> None:
            for prov in self._providers.values():
                await prov.subscribe(ref)
            # Subscribe new first so Dhan sid never goes None (keeps one WS).
            # Zerodha still needs the previous token dropped.
            if old is not None and old.key != ref.key and self._zerodha is not None:
                try:
                    await self._zerodha.unsubscribe(old)
                except Exception:
                    pass

        try:
            asyncio.run(_sub())
        except RuntimeError:
            pass
        logger.info(
            "subscribed %s dhan=%s zerodha=%s shoonya=%s providers=%s",
            ref.key,
            ref.dhan_security_id,
            ref.zerodha_instrument_token,
            ref.shoonya_token,
            list(self._providers),
        )
        log_md.info(
            "subscribe instrument=%s dhan_sid=%s zerodha_token=%s shoonya_token=%s",
            ref.key,
            ref.dhan_security_id,
            ref.zerodha_instrument_token,
            ref.shoonya_token,
        )

    def _apply_unsubscribe(self, ref: InstrumentRef) -> None:
        import asyncio

        async def _unsub() -> None:
            for prov in self._providers.values():
                await prov.unsubscribe(ref)

        try:
            asyncio.run(_unsub())
        except RuntimeError:
            pass
        log_md.info("unsubscribe instrument=%s", ref.key)


    def _index_keepalive_loop(self) -> None:
        """If WS stop writing NIFTY, seed cache from REST (Shoonya or Kite). Never places orders."""
        from datafeedbot.config import load_shoonya_session, load_zerodha_access_token
        from datafeedbot.nifty_cache import cache_age_seconds, kite_index_ltp, write_nifty_ltp_cache

        while not self._recovery_stop.wait(2.0):
            try:
                if cache_age_seconds() < 3.0:
                    continue
                # Prefer Shoonya REST when that feed is enabled (Bot4 Shoonya-only).
                if self.settings.enable_shoonya:
                    uid, stok = load_shoonya_session(self.settings.runtime_root)
                    if uid and stok:
                        try:
                            from datafeedbot.brokers.shoonya.instruments import NIFTY_INDEX_TOKEN
                            from datafeedbot.brokers.shoonya.provider import extract_shoonya_tick
                            import json
                            import urllib.request

                            body = (
                                "jData="
                                + json.dumps(
                                    {"uid": uid, "exch": "NSE", "token": NIFTY_INDEX_TOKEN},
                                    separators=(",", ":"),
                                )
                            ).encode("utf-8")
                            req = urllib.request.Request(
                                "https://api.shoonya.com/NorenWClientAPI/GetQuotes",
                                data=body,
                                headers={
                                    "Content-Type": "text/plain",
                                    "Authorization": f"Bearer {stok}",
                                },
                                method="POST",
                            )
                            with urllib.request.urlopen(req, timeout=8) as resp:
                                q = json.loads(resp.read().decode("utf-8", "replace") or "{}")
                            raw = extract_shoonya_tick(q, "NIFTY_INDEX", source="SHOONYA_REST")
                            if raw and raw.ltp:
                                write_nifty_ltp_cache(float(raw.ltp), source="SHOONYA_REST")
                                logger.info("NIFTY cache filled from Shoonya REST px=%.2f", float(raw.ltp))
                                continue
                        except Exception as sexc:
                            logger.debug("Shoonya NIFTY keepalive skipped: %s", sexc)
                if not self.settings.enable_zerodha:
                    continue
                key = (self.settings.zerodha_api_key or "").strip()
                tok = load_zerodha_access_token(self.settings.runtime_root)
                px = kite_index_ltp(key, tok)
                if px is None:
                    continue
                write_nifty_ltp_cache(float(px), source="ZERODHA_REST")
                logger.info("NIFTY cache filled from Kite LTP px=%.2f", float(px))
            except Exception as exc:
                logger.warning("NIFTY keepalive failed: %s", exc)

    def _recovery_loop(self) -> None:
        """WS-only gap watcher — never calls Dhan/Zerodha REST."""
        while not self._recovery_stop.is_set():
            try:
                self._maybe_ws_gap_log()
            except Exception as exc:
                logger.warning("ws gap log loop error: %s", exc)
                log_fail.warning("ws_gap_log_error %s", exc)
            self._recovery_stop.wait(1.0)

    def _maybe_ws_gap_log(self) -> None:
        """Log dual_ws_stale gaps every ~15s; quote_source=ws_only (no REST)."""
        active = self.state.get_active()
        if not active:
            return
        dh = self._dhan.get_health() if self._dhan else None
        zh = self._zerodha.get_health() if self._zerodha else None
        sh = self._shoonya.get_health() if self._shoonya else None
        dhan_ok = bool(dh and dh.connected and dh.age_ms < self.settings.stale_ms * 2)
        z_ok = bool(zh and zh.connected and zh.age_ms < self.settings.stale_ms * 2)
        s_ok = bool(sh and sh.connected and sh.age_ms < self.settings.stale_ms * 2)
        if dhan_ok or z_ok or s_ok:
            return

        now_m = time.monotonic()
        if self._dual_stale_since <= 0:
            self._dual_stale_since = now_m
            log_fail.warning(
                "dual_ws_stale instrument=%s dhan_age=%s z_age=%s shoonya_age=%s quote_source=ws_only",
                active.key,
                getattr(dh, "age_ms", None),
                getattr(zh, "age_ms", None),
                getattr(sh, "age_ms", None),
            )
            self._alerts.notify(
                "dual_ws_stale",
                f"All WS stale/unavailable\n{active.key}\n"
                f"dhan_age_ms={getattr(dh, 'age_ms', None)} "
                f"z_age_ms={getattr(zh, 'age_ms', None)} "
                f"shoonya_age_ms={getattr(sh, 'age_ms', None)}\n"
                "quote_source=ws_only (order REST untouched)",
            )

        if (now_m - float(self._last_gap_log_mono or 0.0)) < 15.0:
            return
        self._last_gap_log_mono = now_m
        log_fail.warning(
            "dual_ws_stale_gap instrument=%s dual_stale_for_ms=%s "
            "dhan_age=%s z_age=%s shoonya_age=%s quote_source=ws_only",
            active.key,
            int((now_m - self._dual_stale_since) * 1000),
            getattr(dh, "age_ms", None),
            getattr(zh, "age_ms", None),
            getattr(sh, "age_ms", None),
        )

    def _journal_tick(self, accepted: Any) -> None:
        """RAM ring (max 2000) + JSONL under RUNTIME/Logs/ticks/YYYY-MM-DD/{sid}.jsonl."""
        import json
        from datetime import date

        try:
            active = self.state.get_active()
            sid = (
                (active.dhan_security_id if active else None)
                or str(getattr(accepted, "instrument", "") or "unknown")
            )
            sid = str(sid).replace("/", "_")
            row = {
                "sequence": getattr(accepted, "sequence", None),
                "instrument": getattr(accepted, "instrument", None),
                "ltp": getattr(accepted, "ltp", None),
                "bid": getattr(accepted, "bid", None),
                "ask": getattr(accepted, "ask", None),
                "source": getattr(accepted, "source", None),
                "status": getattr(accepted, "status", None),
                "market_timestamp": getattr(accepted, "market_timestamp", None),
                "received_timestamp": getattr(accepted, "received_timestamp", None),
                "sequenced_timestamp": getattr(accepted, "sequenced_timestamp", None),
                "security_id": sid,
                "quote_source": "ws_only",
            }
            ring = self._tick_ring
            ring.append(row)
            overflow = len(ring) - int(self._tick_ring_max)
            if overflow > 0:
                del ring[:overflow]

            day = date.today().isoformat()
            out_dir = Path(self._tick_journal_dir) / day
            out_dir.mkdir(parents=True, exist_ok=True)
            with (out_dir / f"{sid}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        except Exception as exc:
            logger.debug("tick journal failed: %s", exc)

    def latency_stats(self) -> dict[str, float]:
        xs = list(self.metrics["process_ms"])
        if not xs:
            return {}
        ys = sorted(xs)

        def pct(p: float) -> float:
            idx = min(len(ys) - 1, max(0, int(round((p / 100.0) * (len(ys) - 1)))))
            return ys[idx]

        return {
            "count": float(len(ys)),
            "avg": sum(ys) / len(ys),
            "p50": pct(50),
            "p95": pct(95),
            "p99": pct(99),
            "max": ys[-1],
        }

    def ops_snapshot(self) -> dict[str, Any]:
        for prov in self._providers.values():
            try:
                self.state.update_health(prov.get_health())
            except Exception:
                pass
        snap = self.state.snapshot()
        snap["ops"] = {
            "ipc_clients": self.ipc.client_count(),
            "tick_counts": dict(self._tick_counts),
            "reject_counts": dict(self._reject_counts),
            "rest_guard": self._rest_guard.stats(),
            "alerts": self._alerts.stats(),
            "dual_stale_ms": (
                int((time.monotonic() - self._dual_stale_since) * 1000)
                if self._dual_stale_since > 0
                else 0
            ),
            "last_source": self._last_source,
            "latency": self.latency_stats(),
        }
        return snap

    def health_pulse(self) -> None:
        """Periodic detailed health line — call from main loop."""
        snap = self.ops_snapshot()
        log_health.info(
            "pulse active=%s last_source=%s ipc_clients=%s ticks=%s rejects=%s "
            "rest=%s dual_stale_ms=%s latency=%s",
            snap.get("active"),
            snap["ops"].get("last_source"),
            snap["ops"].get("ipc_clients"),
            snap["ops"].get("tick_counts"),
            snap["ops"].get("reject_counts"),
            snap["ops"].get("rest_guard"),
            snap["ops"].get("dual_stale_ms"),
            snap["ops"].get("latency"),
        )
        stats = self.latency_stats()
        if stats:
            log_perf.info("process_ms %s", stats)
        # track ipc clients metric
        self.metrics["ipc_clients"].append(float(self.ipc.client_count()))
        if len(self.metrics["ipc_clients"]) > 500:
            del self.metrics["ipc_clients"][:250]
