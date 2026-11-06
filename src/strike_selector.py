"""
src/strike_selector.py
======================
Selects the correct NIFTY weekly option contract symbol.

Rules:
  - Strike  = ATM ± 1% of spot, rounded to nearest 50
  - Expiry  = current week's Thursday
  - If today IS Thursday (expiry day), use NEXT Thursday's expiry

Symbol format:  NIFTY{DDMMMYY}{STRIKE}{CE|PE}
Example:        NIFTY29MAY2524000CE
"""

from __future__ import annotations

import datetime
import logging

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

logger = logging.getLogger(__name__)


# ======================================================================
# Expiry helpers
# ======================================================================

def get_expiry(roll_if_today: bool = True) -> str:
    """
    Return the nearest NIFTY weekly expiry date as a string in
    'DDMMMYY' format (e.g. '29MAY25').

    NIFTY weekly options expire every Thursday.

    Parameters
    ----------
    roll_if_today : bool
        If True and today is Thursday (expiry day), return NEXT
        Thursday's expiry instead of today's.
    """
    today = datetime.date.today()
    # weekday(): Monday=0 … Sunday=6; Thursday=3
    days_to_thursday = (3 - today.weekday()) % 7

    if days_to_thursday == 0 and roll_if_today:
        # Today is expiry day — roll to next week
        days_to_thursday = 7
        logger.info("Today is NIFTY weekly expiry. Rolling to next-week contract.")

    expiry_date = today + datetime.timedelta(days=days_to_thursday)
    return expiry_date.strftime("%d%b%y").upper()   # e.g. "29MAY25"


# ======================================================================
# Strike rounding
# ======================================================================

def nearest_strike(value: float, step: int = 50) -> int:
    """Round ``value`` to the nearest multiple of ``step``."""
    return round(value / step) * step


# ======================================================================
# Main selector
# ======================================================================

def select_strike(spot: float, option_type: str) -> str:
    """
    Build the NIFTY option instrument symbol.

    Parameters
    ----------
    spot        : Current NIFTY 50 index price (float)
    option_type : 'CE' (call) or 'PE' (put)

    Returns
    -------
    str
        e.g. 'NIFTY29MAY2524000CE'

    Strike logic
    ------------
      1. ATM  = spot rounded to nearest 50
      2. OTM offset = 1% of spot, rounded to nearest 50
      3. CE strike = ATM + offset
         PE strike = ATM - offset
    """
    option_type = option_type.upper()
    if option_type not in ("CE", "PE"):
        raise ValueError(f"option_type must be 'CE' or 'PE', got '{option_type}'")

    atm    = nearest_strike(spot)
    offset = nearest_strike(spot * 0.01)        # 1% of spot → nearest 50

    if option_type == "CE":
        strike = atm + offset
    else:
        strike = atm - offset

    expiry = get_expiry(roll_if_today=True)
    symbol = f"NIFTY{expiry}{strike}{option_type}"

    logger.info(
        "Strike selected: spot=%.2f  ATM=%d  offset=%d  strike=%d  "
        "expiry=%s  symbol=%s",
        spot, atm, offset, strike, expiry, symbol,
    )
    return symbol

