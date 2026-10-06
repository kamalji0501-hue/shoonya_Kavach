#!/usr/bin/env python3
"""Optional Telegram alerts for Datafeedbot ops (deduped). Never logs secrets."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger("datafeedbot.feed_health")


class AlertSink:
    def __init__(
        self,
        *,
        token: str = "",
        chat_id: str = "",
        min_repeat_seconds: float = 120.0,
    ) -> None:
        self.token = (token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.min_repeat_seconds = float(min_repeat_seconds)
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._sent = 0
        self._suppressed = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def notify(self, key: str, text: str, *, force: bool = False) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key, 0.0)
            if not force and (now - last) < self.min_repeat_seconds:
                self._suppressed += 1
                return False
            self._last[key] = now
        logger.warning("ALERT [%s] %s", key, text[:400])
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload: dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": f"📡 Datafeedbot\n{text}"[:3500],
                "disable_web_page_preview": True,
            }
            with httpx.Client(timeout=8.0) as client:
                r = client.post(url, json=payload)
                if r.status_code >= 400:
                    logger.warning("telegram alert HTTP %s", r.status_code)
                    return False
            self._sent += 1
            return True
        except Exception as exc:
            logger.warning("telegram alert failed: %s", exc)
            return False

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"sent": self._sent, "suppressed": self._suppressed}
