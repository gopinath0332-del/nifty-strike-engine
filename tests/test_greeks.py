"""
tests/test_greeks.py
====================
Unit tests for Black-Scholes option Greeks & Implied Volatility (IV) calculator.
"""

import math
from datetime import datetime, timezone, timedelta
import pytest

from src.greeks import (
    norm_cdf,
    norm_pdf,
    bs_price,
    bs_delta,
    calculate_iv,
    calculate_greeks,
    parse_expiry_from_symbol,
)

IST = timezone(timedelta(hours=5, minutes=30))


def test_norm_cdf_and_pdf():
    assert pytest.approx(norm_cdf(0.0), abs=1e-5) == 0.5
    assert pytest.approx(norm_cdf(1.96), abs=1e-3) == 0.975
    assert pytest.approx(norm_pdf(0.0), abs=1e-5) == 1.0 / math.sqrt(2.0 * math.pi)


def test_bs_price_call_and_put_parity():
    S = 24500.0
    K = 24500.0
    T = 7.0 / 365.0
    r = 0.065
    sigma = 0.15

    call = bs_price(S, K, T, r, sigma, is_call=True)
    put = bs_price(S, K, T, r, sigma, is_call=False)

    # Call-Put Parity: C - P = S - K * exp(-r*T)
    expected_diff = S - K * math.exp(-r * T)
    actual_diff = call - put
    assert pytest.approx(actual_diff, abs=1e-2) == expected_diff


def test_bs_delta_bounds():
    S = 24500.0
    K = 24500.0
    T = 4.0 / 365.0
    r = 0.065
    sigma = 0.18

    call_delta = bs_delta(S, K, T, r, sigma, is_call=True)
    put_delta = bs_delta(S, K, T, r, sigma, is_call=False)

    assert 0.45 <= call_delta <= 0.55
    assert -0.55 <= put_delta <= -0.45
    assert pytest.approx(call_delta - put_delta, abs=1e-3) == 1.0


def test_calculate_iv_recovers_known_volatility():
    S = 24500.0
    K = 24600.0
    T = 5.0 / 365.0
    r = 0.065
    true_vol = 0.22  # 22%

    market_price = bs_price(S, K, T, r, true_vol, is_call=True)
    recovered_iv = calculate_iv(market_price, S, K, T, r, is_call=True)

    assert pytest.approx(recovered_iv, abs=0.2) == 22.0


def test_parse_expiry_from_symbol():
    now = datetime(2026, 11, 8, 10, 0, tzinfo=IST)
    symbol = "NIFTY12NOV2624600CE"
    expiry_dt = parse_expiry_from_symbol(symbol, now)

    assert expiry_dt.year == 2026
    assert expiry_dt.month == 11
    assert expiry_dt.day == 12
    assert expiry_dt.hour == 15
    assert expiry_dt.minute == 30


def test_calculate_greeks_end_to_end():
    entry_time = datetime(2026, 11, 9, 9, 50, tzinfo=IST)
    result = calculate_greeks(
        symbol="NIFTY12NOV2624600CE",
        spot=24550.0,
        strike=24600.0,
        option_type="CE",
        premium=245.0,
        candle_time=entry_time,
    )

    assert 10.0 <= result.iv <= 40.0
    assert 0.40 <= result.delta <= 0.60
    assert result.time_to_expiry_days > 0

