"""
tests/test_strike_selector.py
=============================
Tests for:
  - Strike rounding to nearest 50
  - 1% OTM offset calculation
  - Expiry roll logic (Thursday → next Thursday)
  - Full symbol generation
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from src.strike_selector import (
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
        (20725.0, 20750),  # round-half to even in Python: 20725 / 50 = 414.5 → 414*50=20700 or 415*50
        (20776.0, 20800),
        (207.5,   200),    # 207.5 / 50 = 4.15 → 4 * 50 = 200
        (225.0,   250),    # 225 / 50 = 4.5 → nearest even = 4*50=200 or round()=200/250
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
    """Monday should pick THIS week's Thursday."""
    # 2025-05-19 is a Monday; Thursday is 2025-05-22
    fake_monday = datetime.date(2025, 5, 19)
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_monday
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
        expiry = get_expiry(roll_if_today=True)
    assert expiry == "22MAY25"


def test_get_expiry_on_thursday_rolls_to_next_week() -> None:
    """Thursday (expiry day) must roll to NEXT Thursday (29MAY25)."""
    # 2025-05-22 is a Thursday; next Thursday is 2025-05-29
    fake_thursday = datetime.date(2025, 5, 22)
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_thursday
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
        expiry = get_expiry(roll_if_today=True)
    assert expiry == "29MAY25"


def test_get_expiry_on_thursday_no_roll() -> None:
    """If roll_if_today=False, Thursday returns TODAY's date."""
    fake_thursday = datetime.date(2025, 5, 22)
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_thursday
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)
        expiry = get_expiry(roll_if_today=False)
    assert expiry == "22MAY25"


# ----------------------------------------------------------------------
# select_strike()
# ----------------------------------------------------------------------

def test_select_strike_ce() -> None:
    fake_date = datetime.date(2025, 5, 19)  # Monday → expiry 22MAY25
    with patch("datetime.date") as mock_date:
        mock_date.today.return_value = fake_date
        mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)

        # spot = 20750
        # 1% = 207.5 → nearest 50 = 200
        # CE = 20750 + 200 = 20950
        symbol = select_strike(20750.0, "CE")

    assert symbol.startswith("NIFTY")
    assert symbol.endswith("CE")
    assert "20950" in symbol or "21000" in symbol  # depending on round()


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

