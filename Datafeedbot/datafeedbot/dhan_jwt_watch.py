"""Watch Dhan JWT JSON files and reconnect the existing Dhan WS (one socket)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from datafeedbot.config import load_dhan_jwt

logger = logging.getLogger("datafeedbot.dhan_jwt_watch")


def start_dhan_jwt_watch(
    *,
    runtime_root: Path,
    on_token: Callable[[str], None],
    initial_token: str = "",
    poll_seconds: float = 5.0,
) -> threading.Event:
    """Invoke on_token when the Kavach/Feeder JWT file differs from memory."""
    stop = threading.Event()
    root = Path(runtime_root)
    last_token = (initial_token or "").strip()

    def _loop() -> None:
        nonlocal last_token
        logger.info("Dhan JWT watch started poll=%.1fs", poll_seconds)
        while not stop.wait(timeout=poll_seconds):
            token = load_dhan_jwt(root)
            if token == last_token:
                continue
            prev4 = last_token[-4:] if len(last_token) >= 4 else "-"
            last_token = token
            try:
                if token:
                    logger.info("Dhan JWT file changed last4=%s (was %s)", token[-4:], prev4)
                else:
                    logger.warning("Dhan JWT file cleared (was %s)", prev4)
                on_token(token)
            except Exception as exc:
                logger.error("Dhan JWT hot-reload failed: %s", exc)

    threading.Thread(target=_loop, daemon=True, name="dfb-dhan-jwt-watch").start()
    return stop
