"""
tests/test_candle_builder.py
============================
Unit tests for CandleBuilder:
  - Rejection of pre-market ticks (< 09:15:00 IST)
  - Rejection of post-market ticks (>= 15:30:00 IST)
  - Proper candle formation and closure on candle boundaries
"""

from datetime import datetime, time, timedelta
import pytest
try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

from src.candle_builder import CandleBuilder


def test_pre_market_ticks_ignored_by_default() -> None:
    builder = CandleBuilder(interval_minutes=5, market_hours_only=True)
    # 09:14:23 IST pre-market tick
    ts = datetime(2026, 11, 16, 9, 14, 23, tzinfo=IST)
    closed = builder.on_tick(price=25206.15, volume=100.0, ts=ts)
    assert closed is False
    assert builder._current is None
    assert builder._current_slot is None


def test_post_market_ticks_ignored_by_default() -> None:
    builder = CandleBuilder(interval_minutes=5, market_hours_only=True)
    # 15:30:05 IST post-market tick
    ts = datetime(2026, 11, 16, 15, 30, 5, tzinfo=IST)
    closed = builder.on_tick(price=25206.15, volume=100.0, ts=ts)
    assert closed is False
    assert builder._current is None


def test_market_hours_candle_flow() -> None:
    builder = CandleBuilder(interval_minutes=5, market_hours_only=True)

    # 1. First tick at 09:15:00 opens the 09:15 candle
    t1 = datetime(2026, 11, 16, 9, 15, 0, tzinfo=IST)
    assert builder.on_tick(price=25200.0, volume=10.0, ts=t1) is False
    assert builder._current is not None
    assert builder._current.timestamp.time() == time(9, 15)
    assert builder._current.open == 25200.0

    # 2. Intra-candle tick at 09:17:30 updates OHLCV
    t2 = datetime(2026, 11, 16, 9, 17, 30, tzinfo=IST)
    assert builder.on_tick(price=25250.0, volume=20.0, ts=t2) is False
    assert builder._current.high == 25250.0
    assert builder._current.close == 25250.0

    # 3. Boundary crossed at 09:20:00 -> 09:15 candle closes!
    t3 = datetime(2026, 11, 16, 9, 20, 0, tzinfo=IST)
    closed = builder.on_tick(price=25240.0, volume=15.0, ts=t3)
    assert closed is True
    assert builder.last_closed_candle is not None
    assert builder.last_closed_candle.timestamp.time() == time(9, 15)
    assert builder.last_closed_candle.open == 25200.0
    assert builder.last_closed_candle.high == 25250.0
    assert builder.last_closed_candle.close == 25250.0
    assert builder.last_closed_candle.is_closed is True

    # And new candle at 09:20 is now open
    assert builder._current.timestamp.time() == time(9, 20)
    assert builder._current.open == 25240.0


def test_market_hours_only_false_allows_any_time() -> None:
    builder = CandleBuilder(interval_minutes=5, market_hours_only=False)
    ts = datetime(2026, 11, 16, 9, 14, 23, tzinfo=IST)
    assert builder.on_tick(price=25206.15, volume=100.0, ts=ts) is False
    assert builder._current is not None
    assert builder._current.timestamp.time() == time(9, 10)
