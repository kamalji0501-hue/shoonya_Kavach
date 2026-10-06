"""Watch Shoonya session JSON and reconnect Shoonya WS only (leave other feeds alone)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from datafeedbot.config import load_shoonya_session

logger = logging.getLogger("datafeedbot.shoonya_token_watch")


def start_shoonya_token_watch(
    *,
    runtime_root: Path,
    on_session: Callable[[str, str], None],
    initial_token: str = "",
    poll_seconds: float = 5.0,
) -> threading.Event:
    """Invoke on_session(uid, susertoken) when Kavach session file changes."""
    stop = threading.Event()
    root = Path(runtime_root)
    last_token = (initial_token or "").strip()

    def _loop() -> None:
        nonlocal last_token
        logger.info("Shoonya token watch started poll=%.1fs", poll_seconds)
        while not stop.wait(timeout=poll_seconds):
            uid, token = load_shoonya_session(root)
            if token == last_token:
                continue
            prev4 = last_token[-4:] if len(last_token) >= 4 else "-"
            last_token = token
            try:
                if token:
                    logger.info(
                        "Shoonya token file changed last4=%s uid=%s (was %s)",
                        token[-4:],
                        uid or "-",
                        prev4,
                    )
                else:
                    logger.warning("Shoonya token file cleared (was %s)", prev4)
                on_session(uid, token)
            except Exception as exc:
                logger.error("Shoonya token hot-reload failed: %s", exc)

    threading.Thread(target=_loop, daemon=True, name="dfb-shoonya-token-watch").start()
    return stop
