from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_dir: Path | None, level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if log_dir is None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    categories = (
        "system",
        "market_data",
        "feed_health",
        "failover",
        "error",
        "performance",
    )
    for cat in categories:
        path = log_dir / f"{cat}.log"
        fh = logging.handlers.RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger = logging.getLogger(f"datafeedbot.{cat}")
        logger.addHandler(fh)
        logger.propagate = True
