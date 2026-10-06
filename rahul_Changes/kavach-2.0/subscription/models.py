"""Shared types and helpers for subscription validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

STATUS_ACTIVE = "ACTIVE"
STATUS_BLOCKED = "BLOCKED"
VALID_STATUSES = frozenset({STATUS_ACTIVE, STATUS_BLOCKED})


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def normalize_server_id(value: str | None) -> str:
    s = str(value or "").strip()
    if len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s


def parse_iso_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    if not s:
        return None
    # ISO datetime leftovers only (do not split Apps Script "Tue Sep ..." strings)
    if len(s) >= 11 and s[4] == "-" and s[7] == "-" and "T" in s:
        s = s.split("T", 1)[0]
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    # Apps Script sometimes stringifies Date as:
    # "Tue Sep 01 2026 00:00:00 GMT+0530 (India Standard Time)"
    import re
    m = re.match(
        r"^[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})\b",
        s,
    )
    if m:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        mon = months.get(m.group(1).lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(2)))
            except ValueError:
                return None
    # dd/mm/yyyy or dd-mm-yyyy
    m2 = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m2:
        d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def parse_nonneg_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


@dataclass
class SubscriptionRecord:
    serial_number: str = ""
    name: str = ""
    server_id: str = ""
    start_date: date | None = None
    end_date: date | None = None
    max_sell_lots_directional: int | None = None
    max_ato_lots_directional: int | None = None
    max_sell_lots_batman: int | None = None
    max_ato_lots_batman: int | None = None
    server_alias: str = ""
    status: str = STATUS_ACTIVE

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubscriptionRecord":
        return cls(
            serial_number=str(data.get("serial_number") or "").strip(),
            name=str(data.get("name") or "").strip(),
            server_id=str(data.get("server_id") or "").strip(),
            start_date=parse_iso_date(data.get("start_date")),
            end_date=parse_iso_date(data.get("end_date")),
            max_sell_lots_directional=parse_nonneg_int(data.get("max_sell_lots_directional")),
            max_ato_lots_directional=parse_nonneg_int(data.get("max_ato_lots_directional")),
            max_sell_lots_batman=parse_nonneg_int(data.get("max_sell_lots_batman")),
            max_ato_lots_batman=parse_nonneg_int(data.get("max_ato_lots_batman")),
            server_alias=str(data.get("server_alias") or "").strip(),
            status=str(data.get("status") or STATUS_ACTIVE).strip().upper(),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start_date"] = self.start_date.isoformat() if self.start_date else None
        d["end_date"] = self.end_date.isoformat() if self.end_date else None
        return d


@dataclass
class SubscriptionDecision:
    allowed: bool
    reason: str
    ui_status: str
    record: SubscriptionRecord | None = None
    max_sell_lots_batman: int | None = None
    max_ato_lots_batman: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "ui_status": self.ui_status,
            "max_sell_lots_batman": self.max_sell_lots_batman,
            "max_ato_lots_batman": self.max_ato_lots_batman,
            "record": self.record.to_dict() if self.record else None,
            "details": self.details,
        }


@dataclass
class DayGrant:
    valid_for_date: str
    server_id: str
    validated_at: str
    allowed: bool
    reason: str
    ui_status: str
    serial_number: str = ""
    name: str = ""
    server_alias: str = ""
    status: str = ""
    start_date: str | None = None
    end_date: str | None = None
    max_sell_lots_directional: int | None = None
    max_ato_lots_directional: int | None = None
    max_sell_lots_batman: int | None = None
    max_ato_lots_batman: int | None = None
    source: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DayGrant":
        return cls(
            valid_for_date=str(data.get("valid_for_date") or ""),
            server_id=str(data.get("server_id") or ""),
            validated_at=str(data.get("validated_at") or ""),
            allowed=bool(data.get("allowed")),
            reason=str(data.get("reason") or ""),
            ui_status=str(data.get("ui_status") or "SUBSCRIPTION_VALIDATION_ERROR"),
            serial_number=str(data.get("serial_number") or ""),
            name=str(data.get("name") or ""),
            server_alias=str(data.get("server_alias") or ""),
            status=str(data.get("status") or ""),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            max_sell_lots_directional=parse_nonneg_int(data.get("max_sell_lots_directional")),
            max_ato_lots_directional=parse_nonneg_int(data.get("max_ato_lots_directional")),
            max_sell_lots_batman=parse_nonneg_int(data.get("max_sell_lots_batman")),
            max_ato_lots_batman=parse_nonneg_int(data.get("max_ato_lots_batman")),
            source=str(data.get("source") or ""),
            error=str(data.get("error") or ""),
        )


def decision_to_grant(
    decision: SubscriptionDecision,
    *,
    server_id: str,
    source: str,
    error: str = "",
) -> DayGrant:
    rec = decision.record
    return DayGrant(
        valid_for_date=today_ist().isoformat(),
        server_id=server_id,
        validated_at=now_ist().isoformat(),
        allowed=decision.allowed,
        reason=decision.reason,
        ui_status=decision.ui_status,
        serial_number=rec.serial_number if rec else "",
        name=rec.name if rec else "",
        server_alias=rec.server_alias if rec else "",
        status=rec.status if rec else "",
        start_date=rec.start_date.isoformat() if rec and rec.start_date else None,
        end_date=rec.end_date.isoformat() if rec and rec.end_date else None,
        max_sell_lots_directional=rec.max_sell_lots_directional if rec else None,
        max_ato_lots_directional=rec.max_ato_lots_directional if rec else None,
        max_sell_lots_batman=decision.max_sell_lots_batman,
        max_ato_lots_batman=decision.max_ato_lots_batman,
        source=source,
        error=error,
    )
