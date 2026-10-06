from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from vps_health_bot import runtime_paths as rp


def _load_env() -> None:
    rp.ensure_layout()
    for candidate in rp.secrets_env_candidates():
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def _parse_ids(raw: str) -> set[int]:
    chats: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            chats.add(int(part))
    return chats


def _merge_persisted(chats: set[int]) -> set[int]:
    path = rp.allowed_chats_file()
    if not path.is_file():
        return chats
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        extra = data.get("chat_ids") or []
        for item in extra:
            chats.add(int(item))
    except Exception:
        return chats
    return chats


def persist_chat_id(chat_id: int) -> None:
    path = rp.allowed_chats_file()
    ids = _merge_persisted(set())
    ids.add(int(chat_id))
    path.write_text(
        json.dumps({"chat_ids": sorted(ids)}, indent=2) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class HealthBotConfig:
    token: str
    allowed_chat_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    expected_public_ip: str
    expected_live: tuple[str, ...]
    alert_interval_sec: int
    heartbeat_sec: int
    timezone: str
    data_dir: Path
    logs_day: Path


def load_config() -> HealthBotConfig:
    _load_env()
    token = (os.getenv("VPS_HEALTH_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "VPS_HEALTH_BOT_TOKEN missing. Put it in "
            f"{rp.secrets_root() / 'telegram' / 'token.env'}"
        )

    chats = _parse_ids(os.getenv("VPS_HEALTH_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or "")
    chats |= _parse_ids(os.getenv("VPS_HEALTH_GROUP_IDS") or "")
    chats = _merge_persisted(chats)
    if not chats:
        raise RuntimeError("VPS_HEALTH_CHAT_IDS missing (comma-separated Telegram chat ids)")

    users = _parse_ids(os.getenv("VPS_HEALTH_USER_IDS") or "")
    if not users:
        users = {cid for cid in chats if cid > 0}

    live_raw = os.getenv("VPS_HEALTH_EXPECTED_LIVE", "datafeedbot,blitzbot,vps-health-bot")
    live = tuple(x.strip() for x in live_raw.split(",") if x.strip())

    interval = int(os.getenv("VPS_HEALTH_ALERT_INTERVAL_SEC", "60"))
    heartbeat = int(os.getenv("VPS_HEALTH_HEARTBEAT_SEC", "900"))
    tz = os.getenv("TZ", "Asia/Kolkata")
    return HealthBotConfig(
        token=token,
        allowed_chat_ids=frozenset(chats),
        allowed_user_ids=frozenset(users),
        expected_public_ip=os.getenv("EXPECTED_PUBLIC_IP", "").strip(),
        expected_live=live,
        alert_interval_sec=max(20, interval),
        heartbeat_sec=max(120, heartbeat),
        timezone=tz,
        data_dir=rp.health_data_dir(),
        logs_day=rp.log_day_dir(),
    )
