#!/usr/bin/env python3
"""One-shot: send current VPS day report to all allowed chats."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram import Bot

from vps_health_bot.collectors import collect_snapshot
from vps_health_bot.config import load_config
from vps_health_bot import reports
from vps_health_bot.telegram_ui import main_kb


async def main() -> None:
    cfg = load_config()
    snap = collect_snapshot(
        cfg.timezone,
        expected_public_ip=cfg.expected_public_ip,
        expected_live=cfg.expected_live,
    )
    text = "📡 <b>VPS Day Report</b>\n\n" + reports.day_report(snap)
    if len(text) > 3900:
        text = text[:3900] + "\n…"
    bot = Bot(cfg.token)
    me = await bot.get_me()
    print("bot", me.username, me.id)
    for chat_id in cfg.allowed_chat_ids:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=main_kb(),
            disable_web_page_preview=True,
        )
        print("sent to", chat_id)


if __name__ == "__main__":
    asyncio.run(main())
