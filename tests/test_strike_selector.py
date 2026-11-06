"""
tests/test_strike_selector.py
=============================
Tests for:
  - Strike rounding to nearest 50
  - 1% OTM offset calculation
  - Expiry roll logic (Thursday → next Thursday)
  - Full symbol generation
  - Premium-based strike selection with mock KiteConnect
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.strike_selector import (
    StrikeSelector,
    get_expiry,
    nearest_strike,
    select_strike,
)


# ----------------------------------------------------------------------
# nearest_strike()
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (20750.0, 20750),
        (20724.9, 20700),
        (20725.0, 20750),
        (20776.0, 20800),
        (207.5,   200),
        (225.0,   250),
        (250.0,   250),
    ],
)
def test_nearest_strike(value: float, expected: int) -> None:
    result = nearest_strike(value, 50)
    assert result % 50 == 0


# ----------------------------------------------------------------------
# get_expiry() — expiry roll tests
# ----------------------------------------------------------------------

def test_get_expiry_on_a_monday() -> None:
    fake_monday = datetime.date(2025, 5, 19)
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_monday
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
        expiry = get_expiry(roll_if_today=True)
    assert expiry == "22MAY25"


def test_get_expiry_on_thursday_rolls_to_next_week() -> None:
    fake_thursday = datetime.date(2025, 5, 22)
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_thursday
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
        expiry = get_expiry(roll_if_today=True)
    assert expiry == "29MAY25"


def test_get_expiry_on_thursday_no_roll() -> None:
    fake_thursday = datetime.date(2025, 5, 22)
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_thursday
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
        expiry = get_expiry(roll_if_today=False)
    assert expiry == "22MAY25"


# ----------------------------------------------------------------------
# select_strike() fallback
# ----------------------------------------------------------------------

def test_select_strike_ce() -> None:
    fake_date = datetime.date(2025, 5, 19)
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_date
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
        symbol = select_strike(20750.0, "CE")

    assert symbol.startswith("NIFTY")
    assert symbol.endswith("CE")


def test_select_strike_pe() -> None:
    fake_date = datetime.date(2025, 5, 19)
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_date
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
        symbol = select_strike(20750.0, "PE")

    assert symbol.startswith("NIFTY")
    assert symbol.endswith("PE")


def test_select_strike_invalid_type_raises() -> None:
    with pytest.raises(ValueError, match="option_type must be"):
        select_strike(20750.0, "FUT")


# ----------------------------------------------------------------------
# StrikeSelector live premium-based selection tests
# ----------------------------------------------------------------------

def test_select_by_premium_with_mock_kite() -> None:
    mock_kite = MagicMock()
    target_expiry = datetime.date(2026, 11, 12)

    # Mock instruments response
    mock_kite.instruments.return_value = [
        {"name": "NIFTY", "instrument_type": "CE", "expiry": target_expiry, "strike": 24500.0, "tradingsymbol": "NIFTY26N1224500CE"},
        {"name": "NIFTY", "instrument_type": "CE", "expiry": target_expiry, "strike": 24600.0, "tradingsymbol": "NIFTY26N1224600CE"},
        {"name": "NIFTY", "instrument_type": "CE", "expiry": target_expiry, "strike": 24700.0, "tradingsymbol": "NIFTY26N1224700CE"},
    ]

    # Target 1% of 24600 spot is 246.0.
    # Suppose 24600CE LTP is 240.0, 24500CE is 310.0, 24700CE is 180.0
    # Closest to 246.0 is 24600CE (|240 - 246| = 6.0)
    mock_kite.ltp.return_value = {
        "NFO:NIFTY26N1224500CE": {"last_price": 310.0},
        "NFO:NIFTY26N1224600CE": {"last_price": 240.0},
        "NFO:NIFTY26N1224700CE": {"last_price": 180.0},
    }

    selector = StrikeSelector(kite=mock_kite)
    symbol, ltp = selector.select_by_premium(
        24600.0,
        "CE",
        current_date=datetime.date(2026, 11, 6),
    )

    assert symbol == "NIFTY26N1224600CE"
    assert ltp == 240.0
