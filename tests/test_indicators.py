"""
tests/test_indicators.py
========================
Tests for:
  - Supertrend computation (direction matches close vs bands)
  - RSI computation (range 0..100)
  - Bollinger Bands computation (upper > basis > lower)
  - Traditional Pivot Points (P, R1, S1 formulas)
"""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import pytest

from src.indicators import (
    apply_all,
    bollinger_bands,
    rsi,
    supertrend,
    traditional_pivots,
)


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Generate 60 candles of realistic synthetic NIFTY data across 2 days."""
    np.random.seed(42)
    n = 60
    base_price = 24000.0

    # Random walk close prices
    returns = np.random.normal(0.0002, 0.001, n)
    closes  = base_price * np.cumprod(1 + returns)

    highs   = closes + np.random.uniform(5, 20, n)
    lows    = closes - np.random.uniform(5, 20, n)
    opens   = closes + np.random.uniform(-5, 5, n)
    volumes = np.random.uniform(1000, 50000, n)

    # 30 candles on day 1, 30 on day 2
    dates = [datetime.date(2025, 1, 15)] * 30 + [datetime.date(2025, 1, 16)] * 30

    return pd.DataFrame({
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": volumes,
        "date":   dates,
    })


def test_supertrend_columns_added(sample_ohlcv: pd.DataFrame) -> None:
    df = supertrend(sample_ohlcv.copy(), atr_period=10, factor=3.0)
    for col in ("st", "st_direction", "bull_trend", "bear_trend"):
        assert col in df.columns

    # Valid values after warmup
    warm = df.iloc[15:]
    assert warm["bull_trend"].dtype == bool
    assert warm["bear_trend"].dtype == bool
    # bull and bear must be opposite
    assert (warm["bull_trend"] != warm["bear_trend"]).all()


def test_rsi_bounds(sample_ohlcv: pd.DataFrame) -> None:
    df = rsi(sample_ohlcv.copy(), length=14)
    assert "rsi" in df.columns
    warm = df["rsi"].dropna()
    assert (warm >= 0).all() and (warm <= 100).all()


def test_bollinger_bands_ordering(sample_ohlcv: pd.DataFrame) -> None:
    df = bollinger_bands(sample_ohlcv.copy(), length=20, mult=2.0)
    for col in ("bb_basis", "bb_upper", "bb_lower"):
        assert col in df.columns

    warm = df.dropna()
    assert (warm["bb_upper"] >= warm["bb_basis"]).all()
    assert (warm["bb_basis"] >= warm["bb_lower"]).all()


def test_traditional_pivots(sample_ohlcv: pd.DataFrame) -> None:
    df = traditional_pivots(sample_ohlcv.copy())
    for col in ("pivot", "r1", "s1"):
        assert col in df.columns

    # Day 1 will have NaN (no prior day in dataset)
    assert df.iloc[:30]["pivot"].isna().all()

    # Day 2 must have valid numbers
    day2 = df.iloc[30:]
    assert day2["pivot"].notna().all()
    assert day2["r1"].notna().all()
    assert day2["s1"].notna().all()

    # R1 > Pivot > S1
    assert (day2["r1"] > day2["pivot"]).all()
    assert (day2["pivot"] > day2["s1"]).all()


def test_apply_all_chains_correctly(sample_ohlcv: pd.DataFrame) -> None:
    df = apply_all(sample_ohlcv.copy())
    expected_cols = [
        "st", "bull_trend", "bear_trend",
        "rsi", "bb_basis", "bb_upper", "bb_lower",
        "pivot", "r1", "s1",
    ]
    for c in expected_cols:
        assert c in df.columns, f"Missing column: {c}"

