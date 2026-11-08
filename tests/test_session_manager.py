"""
tests/test_session_manager.py
=============================
Unit tests for weekend detection and market hours enforcement.
"""

from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock
import pandas as pd
import pytest
from src.session_manager import is_weekend, is_market_hours, SessionManager
from config.settings import Settings
from src.indicators import apply_all

IST = timezone(timedelta(hours=5, minutes=30))


def _mock_settings() -> Settings:
    import os
    os.environ["KITE_API_KEY"] = "mock_key"
    os.environ["KITE_API_SECRET"] = "mock_secret"
    os.environ["KITE_ACCESS_TOKEN"] = "mock_token"
    os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/test"
    return Settings.load("config/settings.yml")


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


def test_preseed_historical_candles_success():
    mock_kite = MagicMock()
    now_ist = datetime.now(tz=IST)
    yesterday = now_ist - timedelta(days=1)

    # 60 dummy historical candles
    sample_records = [
        {
            "date": yesterday + timedelta(minutes=5 * i),
            "open": 24000.0 + i,
            "high": 24010.0 + i,
            "low": 23990.0 + i,
            "close": 24005.0 + i,
            "volume": 1000 + i,
        }
        for i in range(60)
    ]
    mock_kite.historical_data.return_value = sample_records

    mock_notifier = MagicMock()
    mock_firestore = MagicMock()
    mock_state = MagicMock()
    mock_state.reconcile.return_value = {"status": "CLEAN"}

    sm = SessionManager(
        settings=_mock_settings(),
        kite_client=mock_kite,
        notifier=mock_notifier,
        firestore=mock_firestore,
        state_manager=mock_state,
    )

    # sm.__init__ already calls preseed_historical_candles(count=50)
    assert len(sm._candle_buffer) == 50
    mock_kite.historical_data.assert_called_once()

    # Check candle fields
    first_candle = sm._candle_buffer[0]
    assert "open" in first_candle
    assert "high" in first_candle
    assert "low" in first_candle
    assert "close" in first_candle
    assert "volume" in first_candle
    assert "timestamp" in first_candle
    assert "date" in first_candle
    assert first_candle["date"] == yesterday.date()


def test_preseed_historical_candles_empty():
    mock_kite = MagicMock()
    mock_kite.historical_data.return_value = []

    mock_notifier = MagicMock()
    mock_firestore = MagicMock()
    mock_state = MagicMock()
    mock_state.reconcile.return_value = {"status": "CLEAN"}

    sm = SessionManager(
        settings=_mock_settings(),
        kite_client=mock_kite,
        notifier=mock_notifier,
        firestore=mock_firestore,
        state_manager=mock_state,
    )

    assert len(sm._candle_buffer) == 0


def test_preseed_historical_candles_exception_graceful():
    mock_kite = MagicMock()
    mock_kite.historical_data.side_effect = Exception("API connection timed out")

    mock_notifier = MagicMock()
    mock_firestore = MagicMock()
    mock_state = MagicMock()
    mock_state.reconcile.return_value = {"status": "CLEAN"}

    sm = SessionManager(
        settings=_mock_settings(),
        kite_client=mock_kite,
        notifier=mock_notifier,
        firestore=mock_firestore,
        state_manager=mock_state,
    )

    assert len(sm._candle_buffer) == 0


def test_preseed_enables_immediate_pivot_and_indicator_readiness():
    mock_kite = MagicMock()
    yesterday = datetime(2026, 11, 6, 9, 15, tzinfo=IST)
    today = datetime(2026, 11, 9, 9, 15, tzinfo=IST)

    # Pre-seed 50 bars from yesterday (2026-11-06)
    yesterday_records = [
        {
            "date": yesterday + timedelta(minutes=5 * i),
            "open": 24500.0 + (i % 4) * 5,
            "high": 24520.0 + (i % 4) * 5,
            "low": 24480.0 + (i % 4) * 5,
            "close": 24500.0 + ((i + 1) % 4) * 5,
            "volume": 5000,
        }
        for i in range(50)
    ]
    mock_kite.historical_data.return_value = yesterday_records

    mock_notifier = MagicMock()
    mock_firestore = MagicMock()
    mock_state = MagicMock()
    mock_state.reconcile.return_value = {"status": "CLEAN"}

    sm = SessionManager(
        settings=_mock_settings(),
        kite_client=mock_kite,
        notifier=mock_notifier,
        firestore=mock_firestore,
        state_manager=mock_state,
    )

    assert len(sm._candle_buffer) == 50

    # Now add the very first 09:20 AM closed candle of today (2026-11-09)
    sm._candle_buffer.append({
        "open": 24560.0,
        "high": 24590.0,
        "low": 24550.0,
        "close": 24580.0,
        "volume": 6000.0,
        "timestamp": today + timedelta(minutes=5),
        "date": today.date(),
    })

    assert len(sm._candle_buffer) == 51

    # Convert buffer to DataFrame and apply indicators
    df = pd.DataFrame(list(sm._candle_buffer))
    df = apply_all(
        df,
        st_atr_period=10,
        st_factor=3.0,
        st_buffer_points=0.0,
        rsi_length=14,
        bb_length=20,
        bb_mult=2.0,
    )

    # Inspect the latest bar (the first candle of today)
    latest_bar = df.iloc[-1]
    assert pd.notna(latest_bar["pivot"]), "Pivot should NOT be NaN on the first morning candle"
    assert pd.notna(latest_bar["r1"]), "R1 should NOT be NaN on the first morning candle"
    assert pd.notna(latest_bar["s1"]), "S1 should NOT be NaN on the first morning candle"
    assert pd.notna(latest_bar["st"]), "Supertrend should NOT be NaN"
    assert pd.notna(latest_bar["rsi"]), "RSI should NOT be NaN"
    assert pd.notna(latest_bar["bb_basis"]), "Bollinger Bands should NOT be NaN"

    # Expected pivots based on yesterday's day range:
    # High max = 24535, Low min = 24480, Close last = 24510
    expected_p = (24535.0 + 24480.0 + 24510.0) / 3.0
    assert round(latest_bar["pivot"], 2) == round(expected_p, 2)


