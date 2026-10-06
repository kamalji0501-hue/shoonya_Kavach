#!/usr/bin/env python3
"""Datafeedbot entrypoint — market data only (no trading)."""

from __future__ import annotations

import signal
import sys
import threading
import time

from datafeedbot.config import Settings
from datafeedbot.engine import DatafeedEngine
from datafeedbot.logging_setup import setup_logging


def main() -> int:
    settings = Settings.load()
    setup_logging(settings.log_dir, level="INFO")
    # Ensure Zerodha access_token is valid before feeds start (auto-refresh when
    # USER_ID+PASSWORD+TOTP are present in Runtime credentials).
    if settings.enable_zerodha and settings.zerodha_api_key:
        try:
            from datafeedbot.auth.zerodha_token import ensure_zerodha_access_token

            token = ensure_zerodha_access_token(settings.runtime_root)
            settings.zerodha_access_token = token
        except Exception as exc:
            import logging

            logging.getLogger("datafeedbot").warning(
                "Zerodha token ensure skipped/failed: %s", exc
            )
    engine = DatafeedEngine(settings)
    stop = threading.Event()

    def _stop(*_args):  # noqa: ANN001
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    engine.start()
    print("Datafeedbot READY", flush=True)
    print(f"IPC={settings.ipc_socket}", flush=True)
    last_pulse = 0.0
    while not stop.is_set():
        time.sleep(1.0)
        now = time.time()
        # Detailed health/performance pulse every 30s (always, not only when %30==0)
        if now - last_pulse >= 30.0:
            last_pulse = now
            try:
                engine.health_pulse()
            except Exception as exc:
                import logging

                logging.getLogger("datafeedbot.error").warning(
                    "health_pulse failed: %s", exc
                )
    engine.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
