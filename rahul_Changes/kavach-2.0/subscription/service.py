"""Orchestrate daily subscription refresh and grant reads."""

from __future__ import annotations

import logging
from typing import Any

from subscription.audit import audit_event
from subscription.config import SubscriptionConfig, load_config
from subscription.grant_store import read_grant, write_grant
from subscription.models import DayGrant, SubscriptionDecision, decision_to_grant, today_ist
from subscription.repository import (
    SubscriptionRepositoryError,
    build_repository,
)
from subscription.validator import validate_record

logger = logging.getLogger(__name__)


def _denied_decision(reason: str, ui_status: str) -> SubscriptionDecision:
    return SubscriptionDecision(allowed=False, reason=reason, ui_status=ui_status)


def refresh_subscription(cfg: SubscriptionConfig | None = None, *, force: bool = False) -> DayGrant:
    cfg = cfg or load_config()
    server_id = cfg.algo_server_id
    if not server_id:
        decision = _denied_decision(
            "ALGO_SERVER_ID is not configured.",
            "SUBSCRIPTION_VALIDATION_ERROR",
        )
        grant = decision_to_grant(decision, server_id="", source=cfg.source, error="missing_server_id")
        write_grant(cfg.grant_dir, grant)
        audit_event(cfg.audit_dir, "subscription_refresh", "deny", reason=grant.reason)
        return grant

    if not force:
        existing = read_grant(cfg.grant_dir)
        if existing and existing.valid_for_date == today_ist().isoformat():
            return existing

    try:
        repo = build_repository(cfg)
        record = repo.fetch_for_server(server_id)
        decision = validate_record(
            record,
            algo_server_id=server_id,
            expiring_soon_days=cfg.expiring_soon_days,
        )
        grant = decision_to_grant(decision, server_id=server_id, source=cfg.source)
    except SubscriptionRepositoryError as exc:
        logger.error("subscription refresh failed closed: %s", exc)
        decision = _denied_decision(
            "Subscription validation error. New trading is blocked until validation succeeds.",
            "SUBSCRIPTION_VALIDATION_ERROR",
        )
        grant = decision_to_grant(
            decision,
            server_id=server_id,
            source=cfg.source,
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("unexpected subscription refresh failure")
        decision = _denied_decision(
            "Subscription validation error. New trading is blocked until validation succeeds.",
            "SUBSCRIPTION_VALIDATION_ERROR",
        )
        grant = decision_to_grant(
            decision,
            server_id=server_id,
            source=cfg.source,
            error=type(exc).__name__,
        )

    write_grant(cfg.grant_dir, grant)
    audit_event(
        cfg.audit_dir,
        "subscription_refresh",
        "allow" if grant.allowed else "deny",
        server_id=server_id,
        ui_status=grant.ui_status,
        reason=grant.reason,
        source=cfg.source,
    )
    return grant


def force_refresh() -> DayGrant:
    return refresh_subscription(force=True)


def load_day_grant(cfg: SubscriptionConfig | None = None) -> DayGrant | None:
    cfg = cfg or load_config()
    grant = read_grant(cfg.grant_dir)
    if grant and grant.valid_for_date == today_ist().isoformat():
        return grant
    return None


def refresh_if_needed(cfg: SubscriptionConfig | None = None) -> DayGrant:
    """Return today's grant; refresh if missing."""
    cfg = cfg or load_config()
    grant = load_day_grant(cfg)
    if grant is not None:
        return grant
    return refresh_subscription(cfg, force=True)


def get_subscription_view(cfg: SubscriptionConfig | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    grant = load_day_grant(cfg)
    if grant is None:
        # Do not call Google from request path — report missing grant.
        return {
            "ok": True,
            "allowed": False,
            "ui_status": "SUBSCRIPTION_VALIDATION_ERROR",
            "reason": "Subscription has not been validated today yet.",
            "server_id": cfg.algo_server_id,
            "server_alias": "",
            "name": "",
            "status": "",
            "start_date": None,
            "end_date": None,
            "max_sell_lots_batman": None,
            "max_ato_lots_batman": None,
            "max_sell_lots_directional": None,
            "max_ato_lots_directional": None,
            "grant": None,
        }
    return {
        "ok": True,
        "allowed": grant.allowed,
        "ui_status": grant.ui_status,
        "reason": grant.reason,
        "server_id": grant.server_id,
        "server_alias": grant.server_alias,
        "name": grant.name,
        "status": grant.status,
        "start_date": grant.start_date,
        "end_date": grant.end_date,
        "max_sell_lots_batman": grant.max_sell_lots_batman,
        "max_ato_lots_batman": grant.max_ato_lots_batman,
        "max_sell_lots_directional": grant.max_sell_lots_directional,
        "max_ato_lots_directional": grant.max_ato_lots_directional,
        "valid_for_date": grant.valid_for_date,
        "validated_at": grant.validated_at,
        "grant": grant.to_dict(),
    }
