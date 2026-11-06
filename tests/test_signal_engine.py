"""
tests/test_signal_engine.py
===========================
Tests for:
  - CE entry condition
  - PE entry condition
  - Single trade per day enforcement (no 2nd trade even if condition matches)
  - Supertrend flip exits (trailing SL)
  - Hard EOD exit at 15:00 IST
  - Entry cutoff time (no entry after 14:45 IST)
  - Daily counter reset on date change
"""

from __future__ import annotations

from datetime import datetime, time

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

import pytest

from src.candle_builder import Candle
from src.signal_engine import SignalEngine


def make_candle(
    close: float,
    hour: int = 10,
    minute: int = 0,
    day: int = 15,
) -> Candle:
    ts = datetime(2025, 1, day, hour, minute, 0, tzinfo=IST)
    return Candle(
        open=close - 5,
        high=close + 10,
        low=close - 10,
        close=close,
        volume=1000.0,
        timestamp=ts,
        is_closed=True,
    )


# ----------------------------------------------------------------------
# Entry signals
# ----------------------------------------------------------------------

def test_ce_entry_signal_fires() -> None:
    engine = SignalEngine()
    candle = make_candle(close=24100.0, hour=10, minute=0)
    indicators = {
        "bull_trend": True,
        "bear_trend": False,
        "rsi":        75.0,     # > 70
        "upper_bb":   24050.0,  # close > upper_bb
        "lower_bb":   23900.0,
        "r1":         24020.0,  # close > R1
        "s1":         23850.0,
    }
    result = engine.evaluate(candle, indicators)
    assert result.ce_entry is True
    assert result.pe_entry is False


def test_pe_entry_signal_fires() -> None:
    engine = SignalEngine()
    candle = make_candle(close=23800.0, hour=10, minute=0)
    indicators = {
        "bull_trend": False,
        "bear_trend": True,
        "rsi":        25.0,     # < 30
        "upper_bb":   24100.0,
        "lower_bb":   23850.0,  # close < lower_bb
        "r1":         24050.0,
        "s1":         23820.0,  # close < S1
    }
    result = engine.evaluate(candle, indicators)
    assert result.pe_entry is True
    assert result.ce_entry is False


def test_single_trade_per_day_enforced() -> None:
    """Once a CE trade is filled, NO further CE or PE entries should fire today."""
    engine = SignalEngine()

    # Candle 1: CE enters
    candle1 = make_candle(close=24100.0, hour=10, minute=0)
    ind_ce = {
        "bull_trend": True,
        "bear_trend": False,
        "rsi": 75.0,
        "upper_bb": 24050.0,
        "lower_bb": 23900.0,
        "r1": 24020.0,
        "s1": 23850.0,
    }
    result1 = engine.evaluate(candle1, ind_ce)
    assert result1.ce_entry is True

    # Simulate fill
    engine.on_entry_filled("CE")
    assert engine.trade_today == 1

    # Candle 2: Same bull condition — must NOT fire again
    candle2 = make_candle(close=24150.0, hour=10, minute=3)
    result2 = engine.evaluate(candle2, ind_ce)
    assert result2.ce_entry is False
    assert result2.pe_entry is False

    # Simulate exit
    engine.on_exit_filled()

    # Candle 3: PE condition matches later in same day — must NOT fire
    candle3 = make_candle(close=23800.0, hour=11, minute=0)
    ind_pe = {
        "bull_trend": False,
        "bear_trend": True,
        "rsi": 25.0,
        "upper_bb": 24100.0,
        "lower_bb": 23850.0,
        "r1": 24050.0,
        "s1": 23820.0,
    }
    result3 = engine.evaluate(candle3, ind_pe)
    assert result3.pe_entry is False
    assert result3.ce_entry is False


def test_no_entry_after_cutoff() -> None:
    """No entries at or after 14:45 IST."""
    engine = SignalEngine(entry_cutoff=time(14, 45))
    candle = make_candle(close=24100.0, hour=14, minute=45)   # cutoff minute
    indicators = {
        "bull_trend": True,
        "bear_trend": False,
        "rsi": 75.0,
        "upper_bb": 24050.0,
        "lower_bb": 23900.0,
        "r1": 24020.0,
        "s1": 23850.0,
    }
    result = engine.evaluate(candle, indicators)
    assert result.ce_entry is False


# ----------------------------------------------------------------------
# Exit signals
# ----------------------------------------------------------------------

def test_supertrend_flip_exits_ce() -> None:
    """CE position should close when Supertrend flips to bear."""
    engine = SignalEngine()
    engine.on_entry_filled("CE")

    candle = make_candle(close=24000.0, hour=10, minute=30)
    indicators = {
        "bull_trend": False,
        "bear_trend": True,   # flipped to bear!
        "rsi": 55.0,
    }
    result = engine.evaluate(candle, indicators)
    assert result.exit_ce is True
    assert result.exit_reason == "ST Flip Exit"


def test_supertrend_flip_exits_pe() -> None:
    """PE position should close when Supertrend flips to bull."""
    engine = SignalEngine()
    engine.on_entry_filled("PE")

    candle = make_candle(close=24000.0, hour=10, minute=30)
    indicators = {
        "bull_trend": True,   # flipped to bull!
        "bear_trend": False,
        "rsi": 55.0,
    }
    result = engine.evaluate(candle, indicators)
    assert result.exit_pe is True
    assert result.exit_reason == "ST Flip Exit"


def test_hard_eod_exit_at_1500() -> None:
    """Position must be force-closed at 15:00 IST sharp."""
    engine = SignalEngine(eod_exit=time(15, 0))
    engine.on_entry_filled("CE")

    candle = make_candle(close=24200.0, hour=15, minute=0)
    indicators = {
        "bull_trend": True,   # trend still bull, but time is 15:00!
        "bear_trend": False,
        "rsi": 80.0,
    }
    result = engine.evaluate(candle, indicators)
    assert result.exit_ce is True
    assert result.exit_reason == "EOD 15:00 Exit"


def test_daily_counter_resets_on_new_day() -> None:
    """Trade counter must reset to 0 when candle date changes."""
    engine = SignalEngine()
    candle_day1 = make_candle(close=24100.0, day=15)
    engine.on_entry_filled("CE")
    assert engine.trade_today == 1

    # Position is closed by EOD or ST flip before next day
    engine.on_exit_filled()

    # Candle from next trading day
    candle_day2 = make_candle(close=24100.0, day=16)
    ind = {
        "bull_trend": True,
        "bear_trend": False,
        "rsi": 75.0,
        "upper_bb": 24050.0,
        "lower_bb": 23900.0,
        "r1": 24020.0,
        "s1": 23850.0,
    }
    result = engine.evaluate(candle_day2, ind)
    assert engine.trade_today == 0   # counter was reset
    assert result.ce_entry is True   # new trade allowed
