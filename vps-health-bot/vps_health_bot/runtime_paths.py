"""Standalone runtime roots. Secrets live outside the git tree."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BOT_NAME = "vps_health"


def code_root() -> Path:
    return Path(os.getenv("VPS_HEALTH_ROOT", "/home/ubuntu/vps-health-bot")).expanduser()


def runtime_root() -> Path:
    return Path(os.getenv("VPS_HEALTH_RUNTIME", "/home/ubuntu/VPSHealth_Runtime")).expanduser()


def secrets_root() -> Path:
    return runtime_root() / "Credentials"


def logs_base() -> Path:
    return runtime_root() / "Logs"


def data_base() -> Path:
    return runtime_root() / "Data"


def timezone_name() -> str:
    return os.getenv("TZ", "Asia/Kolkata")


def log_day_dir(when: datetime | None = None) -> Path:
    if when is None:
        try:
            when = datetime.now(ZoneInfo(timezone_name()))
        except Exception:
            when = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    day = logs_base() / when.strftime("%Y-%m") / when.strftime("%Y-%m-%d")
    (day / "logs").mkdir(parents=True, exist_ok=True)
    (day / "errors").mkdir(parents=True, exist_ok=True)
    return day


def bot_log_paths(when: datetime | None = None) -> dict[str, Path]:
    day = log_day_dir(when=when)
    return {
        "day": day,
        "all_log": day / "logs" / "all.log",
        "errors_log": day / "errors" / "all_errors.log",
    }


def health_data_dir() -> Path:
    path = data_base() / BOT_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def allowed_chats_file() -> Path:
    return health_data_dir() / "allowed_chats.json"


def process_log_paths() -> tuple[Path, Path]:
    d = health_data_dir()
    return d / "bot_stdout.log", d / "bot_stderr.log"


def secrets_env_candidates() -> list[Path]:
    return [
        secrets_root() / "telegram" / "token.env",
        secrets_root() / "telegram" / "bots" / BOT_NAME / "token.env",
        Path("/home/ubuntu/Place Order-Secrets/telegram/bots/vps_health/token.env"),
        Path("/home/ubuntu/Place Order-Secrets/config/vps_health_runtime.env"),
        code_root() / "config" / "vps_health.env",
    ]


def ensure_layout() -> None:
    (secrets_root() / "telegram").mkdir(parents=True, exist_ok=True)
    health_data_dir()
    log_day_dir()
    stdout, stderr = process_log_paths()
    stdout.parent.mkdir(parents=True, exist_ok=True)
    for p in (stdout, stderr):
        p.touch(exist_ok=True)
