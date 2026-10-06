from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from vps_health_bot import runtime_paths as rp

_CONFIGURED = False


def setup_health_file_logging(*, level: str = "INFO") -> Path:
    global _CONFIGURED
    rp.ensure_layout()
    paths = rp.bot_log_paths()
    day = paths["day"]

    root_logger = logging.getLogger()
    for h in list(root_logger.handlers):
        if getattr(h, "_vps_health_file", False):
            root_logger.removeHandler(h)
            h.close()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app = RotatingFileHandler(
        paths["all_log"], encoding="utf-8", maxBytes=2_000_000, backupCount=5
    )
    app.setLevel(getattr(logging, level.upper(), logging.INFO))
    app.setFormatter(fmt)
    app._vps_health_file = True  # type: ignore[attr-defined]

    err = RotatingFileHandler(
        paths["errors_log"], encoding="utf-8", maxBytes=1_000_000, backupCount=5
    )
    err.setLevel(logging.ERROR)
    err.setFormatter(fmt)
    err._vps_health_file = True  # type: ignore[attr-defined]

    root_logger.addHandler(app)
    root_logger.addHandler(err)
    if root_logger.level > getattr(logging, level.upper(), logging.INFO):
        root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    _CONFIGURED = True
    logging.getLogger("vps_health_bot").info(
        "Health file logging ready day=%s all=%s events=%s",
        day,
        paths["all_log"],
        rp.log_day_dir() / "logs" / "events.jsonl",
    )
    return day
