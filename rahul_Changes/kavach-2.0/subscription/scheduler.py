"""Background once-daily subscription refresh at configured IST hour."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from subscription.config import load_config
from subscription.service import refresh_subscription

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_thread: threading.Thread | None = None
_stop = threading.Event()


def _seconds_until_next_run(hour: int) -> float:
    now = datetime.now(IST)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _loop() -> None:
    while not _stop.is_set():
        try:
            cfg = load_config()
            # Ensure today's grant exists (boot + daily).
            refresh_subscription(cfg, force=False)
            wait = _seconds_until_next_run(cfg.validate_hour_ist)
            logger.info(
                "subscription scheduler sleeping %.0fs until next %02d:00 IST refresh",
                wait,
                cfg.validate_hour_ist,
            )
            if _stop.wait(wait):
                break
            refresh_subscription(load_config(), force=True)
        except Exception:
            logger.exception("subscription scheduler iteration failed")
            if _stop.wait(60):
                break


def start_subscription_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_loop,
        name="subscription-scheduler",
        daemon=True,
    )
    _thread.start()
    logger.info("subscription scheduler started")


def stop_subscription_scheduler() -> None:
    _stop.set()
