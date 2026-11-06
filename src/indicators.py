"""
src/indicators.py
=================
Pure-Python re-implementation of all Pine Script indicators used in the
NIFTY Options Buying Strategy v9.

All functions operate on a ``pandas.DataFrame`` with columns:
    open, high, low, close, volume

They append result columns in-place and return the same DataFrame so calls
can be chained.

Indicator mapping (Pine Script → Python)
-----------------------------------------
  ta.supertrend(factor, atrPeriod)         → supertrend()
  ta.rsi(close, rsiLength)                 → rsi()
  ta.sma + ta.stdev (Bollinger Bands)      → bollinger_bands()
  ta.pivot_point_levels("Traditional", D)  → traditional_pivots()
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ======================================================================
# Supertrend
# ======================================================================

def supertrend(
    df: pd.DataFrame,
    atr_period: int = 10,
    factor: float = 3.0,
) -> pd.DataFrame:
    """
    Compute Supertrend and append columns:
        st          — Supertrend line value
        st_direction — -1 (bull / green) or +1 (bear / red)
        bull_trend  — bool: direction == -1
        bear_trend  — bool: direction == +1

    Mirrors: ``[st, direction] = ta.supertrend(factor, atrPeriod)``
    Pine uses direction < 0 → bull, direction > 0 → bear.
    """
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    n     = len(df)

    # ATR (Wilder's smoothed)
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(alpha=1 / atr_period, min_periods=atr_period, adjust=False).mean()

    hl2       = (high + low) / 2
    upper_raw = hl2 + factor * atr
    lower_raw = hl2 - factor * atr

    upper = upper_raw.copy()
    lower = lower_raw.copy()
    direction = pd.Series(np.nan, index=df.index)
    st_line   = pd.Series(np.nan, index=df.index)

    for i in range(1, n):
        # Upper band
        if upper_raw.iloc[i] < upper.iloc[i - 1] or close.iloc[i - 1] > upper.iloc[i - 1]:
            upper.iloc[i] = upper_raw.iloc[i]
        else:
            upper.iloc[i] = upper.iloc[i - 1]

        # Lower band
        if lower_raw.iloc[i] > lower.iloc[i - 1] or close.iloc[i - 1] < lower.iloc[i - 1]:
            lower.iloc[i] = lower_raw.iloc[i]
        else:
            lower.iloc[i] = lower.iloc[i - 1]

        # Direction
        prev_dir = direction.iloc[i - 1]
        if pd.isna(prev_dir):
            direction.iloc[i] = 1
        elif prev_dir == 1:
            direction.iloc[i] = -1 if close.iloc[i] > upper.iloc[i] else 1
        else:
            direction.iloc[i] = 1 if close.iloc[i] < lower.iloc[i] else -1

        st_line.iloc[i] = lower.iloc[i] if direction.iloc[i] == -1 else upper.iloc[i]

    df["st"]           = st_line
    df["st_direction"] = direction
    df["bull_trend"]   = direction == -1
    df["bear_trend"]   = direction == 1
    return df


# ======================================================================
# RSI
# ======================================================================

def rsi(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """
    Append ``rsi`` column.
    Mirrors: ``ta.rsi(close, rsiLength)``
    Uses Wilder's smoothing (same as Pine Script / TradingView).
    """
    close  = df["close"]
    delta  = close.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    rs         = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"]  = 100 - (100 / (1 + rs))
    return df


# ======================================================================
# Bollinger Bands
# ======================================================================

def bollinger_bands(
    df: pd.DataFrame,
    length: int = 20,
    mult: float = 2.0,
) -> pd.DataFrame:
    """
    Append ``bb_basis``, ``bb_upper``, ``bb_lower`` columns.
    Mirrors Pine Script SMA + StdDev Bollinger Bands.
    """
    close         = df["close"]
    basis         = close.rolling(length).mean()
    dev           = close.rolling(length).std(ddof=0)   # population std, same as Pine

    df["bb_basis"] = basis
    df["bb_upper"] = basis + mult * dev
    df["bb_lower"] = basis - mult * dev
    return df


# ======================================================================
# Traditional Pivot Points (Daily)
# ======================================================================

def traditional_pivots(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute Traditional Daily Pivot Points and append columns:
        pivot, r1, s1

    Uses *previous* trading day's High / Low / Close — exactly as
    TradingView's ``ta.pivot_point_levels("Traditional", daily_anchor)``.

    Requires df to have a ``date`` column (date portion of IST timestamp)
    for grouping. The function groups candles by date and shifts one day.
    """
    if "date" not in df.columns:
        raise ValueError("DataFrame must have a 'date' column (date portion of IST timestamp)")

    daily = (
        df.groupby("date")
        .agg(prev_high=("high", "max"), prev_low=("low", "min"), prev_close=("close", "last"))
        .shift(1)          # previous day values
    )

    # Map back to intraday rows
    df = df.join(daily, on="date")

    p  = (df["prev_high"] + df["prev_low"] + df["prev_close"]) / 3
    df["pivot"] = p
    df["r1"]    = 2 * p - df["prev_low"]
    df["s1"]    = 2 * p - df["prev_high"]

    df.drop(columns=["prev_high", "prev_low", "prev_close"], inplace=True)
    return df


# ======================================================================
# Convenience: apply all indicators in one call
# ======================================================================

def apply_all(
    df: pd.DataFrame,
    st_atr_period: int = 10,
    st_factor: float = 3.0,
    rsi_length: int = 14,
    bb_length: int = 20,
    bb_mult: float = 2.0,
) -> pd.DataFrame:
    """Apply Supertrend, RSI, Bollinger Bands, and Pivot columns to df."""
    df = supertrend(df, atr_period=st_atr_period, factor=st_factor)
    df = rsi(df, length=rsi_length)
    df = bollinger_bands(df, length=bb_length, mult=bb_mult)
    df = traditional_pivots(df)
    return df

