"""
tests/test_session_manager.py
=============================
Unit tests for weekend detection and market hours enforcement.
"""

from datetime import datetime, timezone, timedelta
import pytest
from src.session_manager import is_weekend, is_market_hours

IST = timezone(timedelta(hours=5, minutes=30))


def test_is_weekend():
    # 2026-11-07 is Saturday
    sat = datetime(2026, 11, 7, 12, 0, tzinfo=IST)
    assert is_weekend(sat) is True

    # 2026-11-08 is Sunday
    sun = datetime(2026, 11, 8, 12, 0, tzinfo=IST)
    assert is_weekend(sun) is True

    # 2026-11-06 is Friday
    fri = datetime(2026, 11, 6, 12, 0, tzinfo=IST)
    assert is_weekend(fri) is False

    # 2026-11-09 is Monday
    mon = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    assert is_weekend(mon) is False


def test_is_market_hours():
    # Saturday afternoon: Market is closed
    sat = datetime(2026, 11, 7, 12, 0, tzinfo=IST)
    assert is_market_hours(sat) is False

    # Monday 08:50 AM (pre-market): Market is not open yet
    mon_early = datetime(2026, 11, 9, 8, 50, tzinfo=IST)
    assert is_market_hours(mon_early) is False

    # Monday 09:14 AM (tick stream warmup / open): Market is open
    mon_open = datetime(2026, 11, 9, 9, 14, tzinfo=IST)
    assert is_market_hours(mon_open) is True

    # Monday 11:30 AM (active session): Market is open
    mon_mid = datetime(2026, 11, 9, 11, 30, tzinfo=IST)
    assert is_market_hours(mon_mid) is True

    # Monday 15:30 PM: Market close cutoff
    mon_close = datetime(2026, 11, 9, 15, 30, tzinfo=IST)
    assert is_market_hours(mon_close) is True

    # Monday 15:31 PM: Market is closed
    mon_late = datetime(2026, 11, 9, 15, 31, tzinfo=IST)
    assert is_market_hours(mon_late) is False

