"""Subscription data sources (Apps Script HTTP or local JSON file)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from subscription.config import SubscriptionConfig
from subscription.models import SubscriptionRecord, normalize_server_id

logger = logging.getLogger(__name__)


class SubscriptionRepositoryError(Exception):
    """Fetch failed — callers must fail closed."""


class SubscriptionRepository:
    def fetch_for_server(self, server_id: str) -> SubscriptionRecord | None:
        raise NotImplementedError


class AppsScriptRepository(SubscriptionRepository):
    def __init__(self, cfg: SubscriptionConfig) -> None:
        self._cfg = cfg

    def fetch_for_server(self, server_id: str) -> SubscriptionRecord | None:
        if not self._cfg.api_url or not self._cfg.api_key:
            raise SubscriptionRepositoryError("subscription API URL/key not configured")
        params = urllib.parse.urlencode(
            {"server_id": server_id, "api_key": self._cfg.api_key}
        )
        url = self._cfg.api_url
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}{params}"
        last_err: Exception | None = None
        attempts = 1 + int(self._cfg.api_retry_count)
        for i in range(attempts):
            try:
                req = urllib.request.Request(
                    full,
                    headers={"User-Agent": "KavachSubscription/1.0", "Accept": "application/json"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=self._cfg.api_timeout_sec) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise SubscriptionRepositoryError("malformed subscription response")
                if not data.get("ok"):
                    err = str(data.get("error") or "subscription lookup failed")
                    if err in ("not_found", "unauthorized"):
                        return None if err == "not_found" else (_raise_unauth(err))
                    raise SubscriptionRepositoryError(err)
                sub = data.get("subscription")
                if not isinstance(sub, dict):
                    raise SubscriptionRepositoryError("missing subscription object")
                return SubscriptionRecord.from_dict(sub)
            except SubscriptionRepositoryError:
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_err = exc
                logger.warning("subscription API attempt %s failed: %s", i + 1, type(exc).__name__)
                if i + 1 < attempts:
                    time.sleep(0.4 * (2**i))
        raise SubscriptionRepositoryError(f"subscription API unavailable: {last_err}")


def _raise_unauth(err: str) -> None:
    raise SubscriptionRepositoryError(err)


class FileRepository(SubscriptionRepository):
    """Local JSON fixture for tests / offline until Apps Script is deployed."""

    def __init__(self, cfg: SubscriptionConfig) -> None:
        self._cfg = cfg

    def fetch_for_server(self, server_id: str) -> SubscriptionRecord | None:
        path = self._cfg.file_path
        if not path.is_file():
            raise SubscriptionRepositoryError(f"subscription file missing: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SubscriptionRepositoryError(f"subscription file unreadable: {exc}") from exc
        rows: list[dict[str, Any]]
        if isinstance(data, dict) and isinstance(data.get("subscriptions"), list):
            rows = [r for r in data["subscriptions"] if isinstance(r, dict)]
        elif isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            rows = [data]
        else:
            raise SubscriptionRepositoryError("malformed subscription file")
        want = normalize_server_id(server_id)
        for row in rows:
            if normalize_server_id(str(row.get("server_id") or "")) == want:
                return SubscriptionRecord.from_dict(row)
        return None


def build_repository(cfg: SubscriptionConfig) -> SubscriptionRepository:
    if cfg.source == "file":
        return FileRepository(cfg)
    return AppsScriptRepository(cfg)
