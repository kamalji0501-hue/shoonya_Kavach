"""Watch Zerodha token JSON files and reconnect KiteTicker only (leave Dhan WS up)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from datafeedbot.config import load_zerodha_access_token

logger = logging.getLogger("datafeedbot.zerodha_token_watch")


def start_zerodha_token_watch(
    *,
    runtime_root: Path,
    on_token: Callable[[str], None],
    initial_token: str = "",
    poll_seconds: float = 5.0,
) -> threading.Event:
    """Invoke on_token when the Kavach/Feeder Kite file differs from memory."""
    stop = threading.Event()
    root = Path(runtime_root)
    last_token = (initial_token or "").strip()

    def _loop() -> None:
        nonlocal last_token
        logger.info("Zerodha token watch started poll=%.1fs", poll_seconds)
        while not stop.wait(timeout=poll_seconds):
            token = load_zerodha_access_token(root)
            if token == last_token:
                continue
            prev4 = last_token[-4:] if len(last_token) >= 4 else "-"
            last_token = token
            try:
                if token:
                    logger.info("Zerodha token file changed last4=%s (was %s)", token[-4:], prev4)
                else:
                    logger.warning("Zerodha token file cleared (was %s)", prev4)
                on_token(token)
            except Exception as exc:
                logger.error("Zerodha token hot-reload failed: %s", exc)

    threading.Thread(target=_loop, daemon=True, name="dfb-zerodha-token-watch").start()
    return stop
