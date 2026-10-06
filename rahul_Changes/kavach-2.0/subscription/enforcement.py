"""Map Deploy/Register requests to lot totals and enforce Batman/Directional caps."""

from __future__ import annotations

import logging
from typing import Any

from subscription.audit import audit_event
from subscription.config import load_config
from subscription.service import load_day_grant
from subscription.validator import validate_directional_trade_limits, validate_trade_limits

logger = logging.getLogger(__name__)

# Full iron condor: buy_lots per side, sell = 2 * buy per side → total sell = 4 * buy_lots
# ATO sized to buy leg per side → total ATO = 2 * buy_lots
SELL_LOTS_PER_BUY_LOT_FULL_IC = 4
ATO_LOTS_PER_BUY_LOT_FULL_IC = 2


def deploy_requested_limits(base_lots: int) -> tuple[int, int]:
    lots = max(0, int(base_lots))
    return lots * SELL_LOTS_PER_BUY_LOT_FULL_IC, lots * ATO_LOTS_PER_BUY_LOT_FULL_IC


def register_requested_limits_from_legs(
    *,
    pe_sell_lots: int = 0,
    ce_sell_lots: int = 0,
    pe_ato_lots: int = 0,
    ce_ato_lots: int = 0,
) -> tuple[int, int]:
    return int(pe_sell_lots) + int(ce_sell_lots), int(pe_ato_lots) + int(ce_ato_lots)


def register_requested_limits_from_payload(payload: dict[str, Any]) -> tuple[int, int] | None:
    """Optional client-supplied totals are ignored for auth — return None to compute in arm."""
    return None


def check_batman_entry_allowed(
    *,
    requested_sell_lots: int,
    requested_ato_lots: int,
    action: str = "register",
) -> dict[str, Any]:
    """
    Fail-closed gate for Register/Deploy.
    Safe Exit / Flatten must NOT call this.
    """
    cfg = load_config()
    grant = load_day_grant(cfg)
    if grant is None:
        audit_event(
            cfg.audit_dir,
            action,
            "blocked",
            reason="missing_day_grant",
            requested_sell_lots=requested_sell_lots,
            requested_ato_lots=requested_ato_lots,
        )
        return {
            "ok": False,
            "error": "Subscription has not been validated today. New trading is blocked.",
            "ui_status": "SUBSCRIPTION_VALIDATION_ERROR",
        }
    if not grant.allowed:
        audit_event(
            cfg.audit_dir,
            action,
            "blocked",
            reason=grant.reason,
            ui_status=grant.ui_status,
            requested_sell_lots=requested_sell_lots,
            requested_ato_lots=requested_ato_lots,
        )
        return {
            "ok": False,
            "error": grant.reason
            or "Subscription is not active. New trading is blocked.",
            "ui_status": grant.ui_status,
            "end_date": grant.end_date,
            "status": grant.status,
        }

    ok, err = validate_trade_limits(
        max_sell_lots_batman=grant.max_sell_lots_batman,
        max_ato_lots_batman=grant.max_ato_lots_batman,
        requested_sell_lots=int(requested_sell_lots),
        requested_ato_lots=int(requested_ato_lots),
    )
    if not ok:
        audit_event(
            cfg.audit_dir,
            action,
            "limit_blocked",
            reason=err,
            requested_sell_lots=requested_sell_lots,
            requested_ato_lots=requested_ato_lots,
            max_sell_lots_batman=grant.max_sell_lots_batman,
            max_ato_lots_batman=grant.max_ato_lots_batman,
        )
        return {
            "ok": False,
            "error": err,
            "ui_status": grant.ui_status,
            "max_sell_lots_batman": grant.max_sell_lots_batman,
            "max_ato_lots_batman": grant.max_ato_lots_batman,
        }

    audit_event(
        cfg.audit_dir,
        action,
        "allowed",
        requested_sell_lots=requested_sell_lots,
        requested_ato_lots=requested_ato_lots,
        max_sell_lots_batman=grant.max_sell_lots_batman,
        max_ato_lots_batman=grant.max_ato_lots_batman,
    )
    return {
        "ok": True,
        "max_sell_lots_batman": grant.max_sell_lots_batman,
        "max_ato_lots_batman": grant.max_ato_lots_batman,
        "ui_status": grant.ui_status,
    }



def check_directional_entry_allowed(
    *,
    requested_sell_lots: int,
    requested_ato_lots: int,
    action: str = "register",
) -> dict[str, Any]:
    """
    Fail-closed gate for Directional Deploy/Register.
    Uses tracker Max Sell / Max ATO Directional (not Batman caps).
    Safe Exit / Flatten must NOT call this.
    """
    cfg = load_config()
    grant = load_day_grant(cfg)
    if grant is None:
        audit_event(
            cfg.audit_dir,
            action,
            "blocked",
            reason="missing_day_grant",
            strategy="directional",
            requested_sell_lots=requested_sell_lots,
            requested_ato_lots=requested_ato_lots,
        )
        return {
            "ok": False,
            "error": "Subscription has not been validated today. New trading is blocked.",
            "ui_status": "SUBSCRIPTION_VALIDATION_ERROR",
        }
    if not grant.allowed:
        audit_event(
            cfg.audit_dir,
            action,
            "blocked",
            reason=grant.reason,
            ui_status=grant.ui_status,
            strategy="directional",
            requested_sell_lots=requested_sell_lots,
            requested_ato_lots=requested_ato_lots,
        )
        return {
            "ok": False,
            "error": grant.reason
            or "Subscription is not active. New trading is blocked.",
            "ui_status": grant.ui_status,
            "end_date": grant.end_date,
            "status": grant.status,
        }

    ok, err = validate_directional_trade_limits(
        max_sell_lots_directional=grant.max_sell_lots_directional,
        max_ato_lots_directional=grant.max_ato_lots_directional,
        requested_sell_lots=int(requested_sell_lots),
        requested_ato_lots=int(requested_ato_lots),
    )
    if not ok:
        audit_event(
            cfg.audit_dir,
            action,
            "limit_blocked",
            reason=err,
            strategy="directional",
            requested_sell_lots=requested_sell_lots,
            requested_ato_lots=requested_ato_lots,
            max_sell_lots_directional=grant.max_sell_lots_directional,
            max_ato_lots_directional=grant.max_ato_lots_directional,
        )
        return {
            "ok": False,
            "error": err,
            "ui_status": grant.ui_status,
            "max_sell_lots_directional": grant.max_sell_lots_directional,
            "max_ato_lots_directional": grant.max_ato_lots_directional,
        }

    audit_event(
        cfg.audit_dir,
        action,
        "allowed",
        strategy="directional",
        requested_sell_lots=requested_sell_lots,
        requested_ato_lots=requested_ato_lots,
        max_sell_lots_directional=grant.max_sell_lots_directional,
        max_ato_lots_directional=grant.max_ato_lots_directional,
    )
    return {
        "ok": True,
        "max_sell_lots_directional": grant.max_sell_lots_directional,
        "max_ato_lots_directional": grant.max_ato_lots_directional,
        "ui_status": grant.ui_status,
    }


def check_entry_allowed_for_strategy(
    *,
    strategy: str,
    requested_sell_lots: int,
    requested_ato_lots: int,
    action: str = "register",
) -> dict[str, Any]:
    """Route lot-cap checks to Directional or Batman tracker columns."""
    strat = (strategy or "").strip().lower()
    if strat == "directional":
        return check_directional_entry_allowed(
            requested_sell_lots=requested_sell_lots,
            requested_ato_lots=requested_ato_lots,
            action=action,
        )
    return check_batman_entry_allowed(
        requested_sell_lots=requested_sell_lots,
        requested_ato_lots=requested_ato_lots,
        action=action,
    )
