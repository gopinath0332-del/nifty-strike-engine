"""
src/greeks.py
=============
Analytical Option Greeks & Implied Volatility (IV) calculator for NIFTY weekly options.
Implements Black-Scholes-Merton model using Python standard library math (erf).

Outputs:
  - Implied Volatility (IV) % via Newton-Raphson with bisection fallback.
  - Option Delta: Sensitivity of option price to underlying spot price.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

logger = logging.getLogger(__name__)

DEFAULT_RISK_FREE_RATE = 0.065  # RBI Repo Rate benchmark ~6.5%


@dataclass
class GreeksResult:
    """Calculated Black-Scholes Greeks at trade entry."""
    iv: float              # Implied Volatility in percent (e.g. 21.5 -> 21.5%)
    delta: float           # Option Delta (0 to 1 for Call, -1 to 0 for Put)
    time_to_expiry_days: float  # Days remaining to expiry


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """
    Computes theoretical European option price via Black-Scholes formula.
    """
    if T <= 0:
        return max(0.0, S - K) if is_call else max(0.0, K - S)
    if sigma <= 0.0001:
        intrinsic = max(0.0, S - K * math.exp(-r * T)) if is_call else max(0.0, K * math.exp(-r * T) - S)
        return intrinsic

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if is_call:
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """
    Computes option Delta (dV/dS).
    Call delta is between 0 and 1; Put delta is between -1 and 0.
    """
    if T <= 0:
        if is_call:
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if K > S else 0.0

    if sigma <= 0.0001:
        if is_call:
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if K > S else 0.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) if is_call else (norm_cdf(d1) - 1.0)


def calculate_iv(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = DEFAULT_RISK_FREE_RATE,
    is_call: bool = True,
) -> float:
    """
    Calculates Implied Volatility (IV) in percent.
    Uses Newton-Raphson with bounded bisection fallback.
    """
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return 0.0

    # Intrinsic value check
    intrinsic = max(0.0, S - K) if is_call else max(0.0, K - S)
    if market_price < intrinsic:
        market_price = intrinsic + 1.0

    sigma = 0.20  # Initial guess 20% vol

    # Newton-Raphson
    for _ in range(25):
        price = bs_price(S, K, T, r, sigma, is_call)
        diff = price - market_price
        if abs(diff) < 1e-3:
            return round(sigma * 100.0, 2)

        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        vega = S * math.sqrt(T) * norm_pdf(d1)

        if vega < 1e-5:
            break

        sigma -= diff / vega
        if sigma <= 0.01 or sigma > 3.0:
            break

    # Bisection fallback
    low, high = 0.01, 3.0
    for _ in range(30):
        mid = (low + high) / 2.0
        p = bs_price(S, K, T, r, mid, is_call)
        diff = p - market_price
        if abs(diff) < 1e-3:
            return round(mid * 100.0, 2)
        if diff > 0:
            high = mid
        else:
            low = mid

    return round(mid * 100.0, 2)


def parse_expiry_from_symbol(symbol: str, default_time: datetime) -> datetime:
    """
    Parses expiry date from NIFTY tradingsymbol, e.g. 'NIFTY12NOV2624600CE' -> 2026-11-12 15:30 IST.
    """
    pattern = r"NIFTY(\d{1,2})([A-Z]{3})(\d{2})"
    match = re.search(pattern, symbol.upper())
    if match:
        day, month, yr = match.groups()
        try:
            exp_date = datetime.strptime(f"{day}{month}20{yr}", "%d%b%Y").date()
            return datetime.combine(exp_date, datetime.min.time().replace(hour=15, minute=30), tzinfo=IST)
        except Exception:
            pass

    # Fallback: estimate 4 days forward
    return default_time + timedelta(days=4)


def calculate_greeks(
    symbol: str,
    spot: float,
    strike: float,
    option_type: str,
    premium: float,
    candle_time: datetime,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> GreeksResult:
    """
    Top-level helper to calculate contract IV and Delta at trade entry.
    """
    is_call = option_type.upper() == "CE"
    expiry_dt = parse_expiry_from_symbol(symbol, candle_time)

    # Time to expiry in years
    try:
        t_entry = candle_time.astimezone(IST)
    except Exception:
        t_entry = candle_time

    diff_seconds = max(3600.0, (expiry_dt - t_entry).total_seconds())
    time_to_expiry_years = max(1e-4, diff_seconds / (365.25 * 86400.0))
    time_to_expiry_days = round(diff_seconds / 86400.0, 1)

    iv = calculate_iv(
        market_price=premium,
        S=spot,
        K=strike,
        T=time_to_expiry_years,
        r=risk_free_rate,
        is_call=is_call,
    )

    sigma = iv / 100.0
    delta = bs_delta(
        S=spot,
        K=strike,
        T=time_to_expiry_years,
        r=risk_free_rate,
        sigma=sigma,
        is_call=is_call,
    )

    return GreeksResult(
        iv=iv,
        delta=round(delta, 3),
        time_to_expiry_days=time_to_expiry_days,
    )

