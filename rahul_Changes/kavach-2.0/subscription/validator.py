"""Pure subscription validation (no I/O)."""

from __future__ import annotations

from datetime import timedelta

from subscription.models import (
    STATUS_ACTIVE,
    STATUS_BLOCKED,
    SubscriptionDecision,
    SubscriptionRecord,
    normalize_server_id,
    today_ist,
)


def validate_record(
    record: SubscriptionRecord | None,
    *,
    algo_server_id: str,
    expiring_soon_days: int = 7,
) -> SubscriptionDecision:
    if record is None:
        return SubscriptionDecision(
            allowed=False,
            reason="No subscription found for this server.",
            ui_status="USER_NOT_REGISTERED",
        )

    if normalize_server_id(record.server_id) != normalize_server_id(algo_server_id):
        return SubscriptionDecision(
            allowed=False,
            reason="Server not authorized for this subscription.",
            ui_status="SERVER_NOT_AUTHORIZED",
            record=record,
        )

    status = (record.status or STATUS_ACTIVE).upper()
    if status == STATUS_BLOCKED:
        return SubscriptionDecision(
            allowed=False,
            reason="Subscription is blocked.",
            ui_status="NOT_AUTHORIZED",
            record=record,
            max_sell_lots_batman=record.max_sell_lots_batman,
            max_ato_lots_batman=record.max_ato_lots_batman,
        )
    if status != STATUS_ACTIVE:
        return SubscriptionDecision(
            allowed=False,
            reason=f"Subscription status is {status}.",
            ui_status="NOT_AUTHORIZED",
            record=record,
            max_sell_lots_batman=record.max_sell_lots_batman,
            max_ato_lots_batman=record.max_ato_lots_batman,
        )

    if record.start_date is None or record.end_date is None:
        return SubscriptionDecision(
            allowed=False,
            reason="Subscription dates are missing or invalid.",
            ui_status="SUBSCRIPTION_VALIDATION_ERROR",
            record=record,
        )
    if record.end_date < record.start_date:
        return SubscriptionDecision(
            allowed=False,
            reason="Subscription end date is before start date.",
            ui_status="SUBSCRIPTION_VALIDATION_ERROR",
            record=record,
        )

    today = today_ist()
    if today < record.start_date:
        return SubscriptionDecision(
            allowed=False,
            reason="Subscription has not started yet.",
            ui_status="NOT_AUTHORIZED",
            record=record,
            max_sell_lots_batman=record.max_sell_lots_batman,
            max_ato_lots_batman=record.max_ato_lots_batman,
        )
    if today > record.end_date:
        return SubscriptionDecision(
            allowed=False,
            reason="Your subscription has expired. Please renew your subscription to continue using the algo.",
            ui_status="EXPIRED",
            record=record,
            max_sell_lots_batman=record.max_sell_lots_batman,
            max_ato_lots_batman=record.max_ato_lots_batman,
            details={"end_date": record.end_date.isoformat()},
        )

    if record.max_sell_lots_batman is None or record.max_ato_lots_batman is None:
        return SubscriptionDecision(
            allowed=False,
            reason="Batman lot limits are missing or invalid.",
            ui_status="SUBSCRIPTION_VALIDATION_ERROR",
            record=record,
        )

    ui = "ACTIVE"
    if today >= record.end_date - timedelta(days=max(1, expiring_soon_days) - 1):
        # within expiring_soon_days window including end date
        days_left = (record.end_date - today).days
        if days_left <= expiring_soon_days:
            ui = "EXPIRING_SOON"

    return SubscriptionDecision(
        allowed=True,
        reason="Subscription active.",
        ui_status=ui,
        record=record,
        max_sell_lots_batman=record.max_sell_lots_batman,
        max_ato_lots_batman=record.max_ato_lots_batman,
        details={
            "end_date": record.end_date.isoformat(),
            "days_left": (record.end_date - today).days,
        },
    )


def validate_trade_limits(
    *,
    max_sell_lots_batman: int | None,
    max_ato_lots_batman: int | None,
    requested_sell_lots: int,
    requested_ato_lots: int,
) -> tuple[bool, str]:
    if max_sell_lots_batman is None or max_ato_lots_batman is None:
        return False, "Subscription lot limits are unavailable."
    if requested_sell_lots < 0 or requested_ato_lots < 0:
        return False, "Requested lot counts are invalid."
    if requested_sell_lots > max_sell_lots_batman:
        return (
            False,
            f"Registration blocked. Your subscription allows a maximum of "
            f"{max_sell_lots_batman} Batman SELL lots.",
        )
    if requested_ato_lots > max_ato_lots_batman:
        return (
            False,
            f"Registration blocked. Your subscription allows a maximum of "
            f"{max_ato_lots_batman} Batman ATO lots.",
        )
    return True, ""


def validate_directional_trade_limits(
    *,
    max_sell_lots_directional: int | None,
    max_ato_lots_directional: int | None,
    requested_sell_lots: int,
    requested_ato_lots: int,
) -> tuple[bool, str]:
    if max_sell_lots_directional is None or max_ato_lots_directional is None:
        return False, "Directional subscription lot limits are unavailable."
    if requested_sell_lots < 0 or requested_ato_lots < 0:
        return False, "Requested lot counts are invalid."
    if requested_sell_lots > max_sell_lots_directional:
        return (
            False,
            f"Registration blocked. Your subscription allows a maximum of "
            f"{max_sell_lots_directional} Directional SELL lots.",
        )
    if requested_ato_lots > max_ato_lots_directional:
        return (
            False,
            f"Registration blocked. Your subscription allows a maximum of "
            f"{max_ato_lots_directional} Directional ATO lots.",
        )
    return True, ""
