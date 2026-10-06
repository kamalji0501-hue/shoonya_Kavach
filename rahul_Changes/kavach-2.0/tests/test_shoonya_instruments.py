"""Shoonya trading-symbol map. No broker and no orders."""

from datetime import date

import pytest

from core.shoonya_instruments import (
    expiry_of_shoonya_tsym,
    format_shoonya_tsym,
    last_tuesday,
    map_shoonya_order_symbol,
    shoonya_tsym_from_any,
)


def test_last_tuesday_matches_nse_2026() -> None:
    assert last_tuesday(2026, 9) == date(2026, 9, 29)


def test_kite_weekly_compact_to_shoonya() -> None:
    assert shoonya_tsym_from_any("NIFTY2690824000CE") == "NIFTY08SEP26C24000"
    assert shoonya_tsym_from_any("NIFTY2690824000PE") == "NIFTY08SEP26P24000"
    assert shoonya_tsym_from_any("NIFTY26O0824000PE") == "NIFTY08OCT26P24000"


def test_kite_monthly_uses_last_tuesday() -> None:
    assert shoonya_tsym_from_any("NIFTY26SEP25000CE") == "NIFTY29SEP26C25000"
    assert shoonya_tsym_from_any("NIFTY26SEP24000CE") == "NIFTY29SEP26C24000"


def test_dhan_custom_name() -> None:
    assert shoonya_tsym_from_any("NIFTY 29 SEP 25000 CALL") == "NIFTY29SEP26C25000"
    assert shoonya_tsym_from_any("nifty 8 sep 24000 put") == "NIFTY08SEP26P24000"


def test_already_shoonya_is_unchanged() -> None:
    assert shoonya_tsym_from_any("NIFTY29SEP26C25000") == "NIFTY29SEP26C25000"
    assert expiry_of_shoonya_tsym("NIFTY29SEP26C25000") == date(2026, 9, 29)


def test_format_helper() -> None:
    assert format_shoonya_tsym(date(2026, 9, 29), 25000, "CE") == "NIFTY29SEP26C25000"


def test_expiry_mismatch_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.shoonya_instruments.shoonya_tsym_from_any",
        lambda _symbol: "NIFTY29SEP26C25000",
    )
    with pytest.raises(ValueError, match="Refusing to rewrite"):
        map_shoonya_order_symbol("NIFTY 08 SEP 24000 CALL")
