from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from telegram import ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from vps_health_bot.collectors import collect_snapshot
from vps_health_bot.config import HealthBotConfig, persist_chat_id
from vps_health_bot.history import concern_signature, record_event
from vps_health_bot.max_util import max_util_reasons
from vps_health_bot import reports

logger = logging.getLogger("vps_health_bot")
_SNAP_CACHE: dict[str, object] = {"t": 0.0, "snap": None}


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Overview", callback_data="sec:overview"),
                InlineKeyboardButton("📜 Day report", callback_data="sec:day"),
            ],
            [
                InlineKeyboardButton("🤖 Bots", callback_data="sec:bots"),
                InlineKeyboardButton("🧠 Resources", callback_data="sec:res"),
            ],
            [
                InlineKeyboardButton("🌐 Network", callback_data="sec:net"),
                InlineKeyboardButton("⚡ HFT", callback_data="sec:hft"),
            ],
            [
                InlineKeyboardButton("🗒 Logs", callback_data="sec:logs"),
                InlineKeyboardButton("📅 History", callback_data="sec:hist"),
            ],
            [
                InlineKeyboardButton("💡 Recs", callback_data="sec:recs"),
                InlineKeyboardButton("🔄 Refresh", callback_data="sec:overview"),
            ],
        ]
    )


def _allowed(cfg: HealthBotConfig, update: Update) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None:
        return False
    if int(chat.id) in cfg.allowed_chat_ids:
        return True
    if user is not None and int(user.id) in cfg.allowed_user_ids:
        persist_chat_id(int(chat.id))
        return True
    return False


async def _deny(update: Update) -> None:
    if update.callback_query:
        await update.callback_query.answer("Unauthorized", show_alert=True)
        return
    if update.message:
        await update.message.reply_text("Unauthorized chat.")


def _snapshot(cfg: HealthBotConfig):
    now = time.time()
    cached = _SNAP_CACHE.get("snap")
    stamp = float(_SNAP_CACHE.get("t") or 0)
    if cached is not None and now - stamp < 8:
        return cached
    snap = collect_snapshot(
        cfg.timezone,
        expected_public_ip=cfg.expected_public_ip,
        expected_live=cfg.expected_live,
    )
    _SNAP_CACHE["t"] = now
    _SNAP_CACHE["snap"] = snap
    return snap


def _render(cfg: HealthBotConfig, section: str) -> str:
    snap = _snapshot(cfg)
    if section == "overview":
        return reports.overview(snap)
    if section == "day":
        return reports.day_report(snap)
    if section == "bots":
        return reports.bots_section(snap)
    if section == "res":
        return reports.resources_section(snap)
    if section == "net":
        return reports.network_section(snap)
    if section == "hft":
        return reports.hft_section(snap)
    if section == "logs":
        return reports.logs_section(snap)
    if section == "hist":
        return reports.history_section(snap)
    if section == "recs":
        return reports.recommendations()
    return reports.overview(snap)


async def _send_text(message, text: str) -> None:
    chunks = _chunks(text)
    for i, chunk in enumerate(chunks):
        await message.reply_text(
            chunk,
            reply_markup=main_kb() if i == len(chunks) - 1 else None,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def _chunks(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        parts.append(rest[:limit])
        rest = rest[limit:]
    return parts


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    if not _allowed(cfg, update):
        await _deny(update)
        return
    await _send_text(update.effective_message, _render(cfg, "overview"))


async def on_plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Old Tejas habit: typing hi / hello should show the dashboard."""
    text = (update.effective_message.text or "").strip().lower()
    if text in {"hi", "hii", "hello", "hey", "hlo", "hlw"}:
        await cmd_start(update, context)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None:
        return
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    if user is None or int(user.id) not in cfg.allowed_user_ids:
        await _deny(update)
        return
    persist_chat_id(int(chat.id))
    await update.effective_message.reply_text(
        f"chat_id=<code>{chat.id}</code> type={chat.type}\n"
        "Saved for alerts + heartbeats.",
        parse_mode="HTML",
    )


async def on_section(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    q = update.callback_query
    if q is None:
        return
    if not _allowed(cfg, update):
        await _deny(update)
        return
    await q.answer()
    data = q.data or "sec:overview"
    section = data.split(":", 1)[-1]
    text = _render(cfg, section)
    chunk = text if len(text) <= 3900 else text[:3900] + "\n…"
    try:
        await q.edit_message_text(
            chunk, reply_markup=main_kb(), parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("edit_message_text fallback: %s", exc)
        await _send_text(q.message, text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    if not _allowed(cfg, update):
        await _deny(update)
        return
    await update.effective_message.reply_text(
        "VPS Health Bot (KamaljiAlgo)\n"
        "/start — dashboard\n"
        "/report — full day narrative\n"
        "/bots /res /net /hft /logs /recs — sections\n"
        "/id — save this chat (use in VPS Health Group)\n"
        "/hist — today’s peaks / RSS trend / 429 history\n"
        "Group posts every 15 min + immediately on max utilisation.",
        reply_markup=main_kb(),
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    if not _allowed(cfg, update):
        await _deny(update)
        return
    await _send_text(update.effective_message, _render(cfg, "day"))


def _section_cmd(section: str) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    async def _inner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cfg: HealthBotConfig = context.application.bot_data["cfg"]
        if not _allowed(cfg, update):
            await _deny(update)
            return
        await _send_text(update.effective_message, _render(cfg, section))

    return _inner


def _all_chats(cfg: HealthBotConfig) -> frozenset[int]:
    from vps_health_bot.config import load_config

    try:
        return load_config().allowed_chat_ids
    except Exception:
        return cfg.allowed_chat_ids


async def _broadcast(context: ContextTypes.DEFAULT_TYPE, prefix: str, text: str) -> None:
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    chats = _all_chats(cfg)
    body = prefix + text
    if len(body) > 3900:
        body = body[:3900] + "\n…"
    for chat_id in chats:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=body,
                parse_mode="HTML",
                reply_markup=main_kb(),
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("send failed chat=%s: %s", chat_id, exc)
            record_event("send_fail", chat_id=chat_id, error=str(exc)[:160])
        else:
            record_event("send_ok", chat_id=chat_id, prefix=prefix[:40])


async def alert_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    snap = _snapshot(cfg)
    reasons = max_util_reasons(snap)
    if reasons:
        sig = "MAX|" + "|".join(reasons)
        if context.bot_data.get("last_max_sig") != sig:
            context.bot_data["last_max_sig"] = sig
            extra = " · ".join(reasons)
            record_event("max_util", reasons=reasons)
            await _broadcast(
                context,
                f"🚨 <b>Max utilisation</b>\n{extra}\n\n",
                reports.overview(snap),
            )
        return
    context.bot_data["last_max_sig"] = ""
    if not snap.concerns:
        context.bot_data["last_alert_sig"] = ""
        return
    sig = concern_signature(list(snap.concerns))
    if context.bot_data.get("last_alert_sig") == sig:
        return
    context.bot_data["last_alert_sig"] = sig
    record_event("alert", sig=sig, concerns=list(snap.concerns)[:8])
    await _broadcast(context, "⚠️ <b>VPS health alert</b>\n\n", reports.overview(snap))


async def heartbeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    snap = _snapshot(cfg)
    record_event("heartbeat", load=snap.load1, mem=round(snap.mem_used_pct, 1))
    await _broadcast(context, "📡 <b>VPS Health · 15 min</b>\n\n", reports.overview(snap))


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: HealthBotConfig = context.application.bot_data["cfg"]
    cmu: ChatMemberUpdated | None = update.my_chat_member
    if cmu is None:
        return
    user = cmu.from_user
    chat = cmu.chat
    if user is None or int(user.id) not in cfg.allowed_user_ids:
        return
    persist_chat_id(int(chat.id))
    logger.info("registered chat from membership update chat=%s", chat.id)
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"VPS Health Bot is now watching <code>{chat.title or chat.id}</code>.\n"
                "Send /start for the dashboard."
            ),
            parse_mode="HTML",
            reply_markup=main_kb(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("welcome send failed: %s", exc)


def build_app(cfg: HealthBotConfig) -> Application:
    app = Application.builder().token(cfg.token).build()
    app.bot_data["cfg"] = cfg
    app.add_handler(CommandHandler(["start", "menu"], cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("bots", _section_cmd("bots")))
    app.add_handler(CommandHandler("res", _section_cmd("res")))
    app.add_handler(CommandHandler("net", _section_cmd("net")))
    app.add_handler(CommandHandler("hft", _section_cmd("hft")))
    app.add_handler(CommandHandler("po", _section_cmd("hft")))
    app.add_handler(CommandHandler("logs", _section_cmd("logs")))
    app.add_handler(CommandHandler("recs", _section_cmd("recs")))
    app.add_handler(CommandHandler(["hist", "history"], _section_cmd("hist")))
    app.add_handler(CallbackQueryHandler(on_section, pattern=r"^sec:"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_plain_text))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_help))
    if app.job_queue is not None:
        app.job_queue.run_repeating(
            alert_job, interval=cfg.alert_interval_sec, first=20, name="vps_health_alert"
        )
        app.job_queue.run_repeating(
            heartbeat_job, interval=cfg.heartbeat_sec, first=cfg.heartbeat_sec, name="vps_health_heartbeat"
        )
    else:
        logger.warning("job-queue unavailable — install python-telegram-bot[job-queue]")
    return app
