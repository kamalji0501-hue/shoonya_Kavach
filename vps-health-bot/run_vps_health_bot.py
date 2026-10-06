#!/usr/bin/env python3
"""Entry point: VPS Health Telegram bot (KamaljiAlgo)."""

from __future__ import annotations

import asyncio
import logging
import sys

from vps_health_bot.config import load_config
from vps_health_bot.logging_setup import setup_health_file_logging
from vps_health_bot.telegram_ui import build_app


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    cfg = load_config()
    day = setup_health_file_logging(level="INFO")
    logging.getLogger("vps_health_bot").info(
        "Starting VPS Health Bot chats=%s live=%s tz=%s log_day=%s",
        sorted(cfg.allowed_chat_ids),
        cfg.expected_live,
        cfg.timezone,
        day,
    )
    app = build_app(cfg)
    app.run_polling(
        allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"],
        drop_pending_updates=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
