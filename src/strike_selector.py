"""
src/strike_selector.py
======================
Selects the correct NIFTY weekly option contract symbol.

Selection Modes:
  1. Premium-Based Strike (Live Mode with KiteConnect):
     - Calculates target premium = 1% of NIFTY spot price (e.g. 24,650 spot -> ~246.50 target premium).
     - Identifies target weekly expiry (current week; rolls to next week if today is expiry day).
     - Scans option chain candidate strikes around ATM via Kite LTP batch API.
     - Picks the strike whose live market price (LTP) is closest to target premium.
  2. Fallback / Offline Mode:
     - Strike = ATM +/- 1% of spot, rounded to nearest 50.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kiteconnect import KiteConnect

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

    Parameters
    ----------
    roll_if_today : bool
        If True and today is Thursday (expiry day), return NEXT
        Thursday's expiry instead of today's.
    """
    today = datetime.date.today()
    days_to_thursday = (3 - today.weekday()) % 7

    if days_to_thursday == 0 and roll_if_today:
        days_to_thursday = 7
        logger.info("Today is NIFTY weekly expiry. Rolling to next-week contract.")

    expiry_date = today + datetime.timedelta(days=days_to_thursday)
    return expiry_date.strftime("%d%b%y").upper()


def nearest_strike(value: float, step: int = 50) -> int:
    """Round ``value`` to the nearest multiple of ``step``."""
    return round(value / step) * step


# ======================================================================
# Strike Selector Class (Live + Cached)
# ======================================================================

class StrikeSelector:
    """
    Intelligent Strike Selector using Zerodha Kite Connect live option chain quotes.
    """

    def __init__(self, kite: KiteConnect | None = None) -> None:
        self._kite = kite
        self._cached_instruments: list[dict[str, Any]] | None = None
        self._cache_date: datetime.date | None = None

    def set_kite(self, kite: KiteConnect) -> None:
        self._kite = kite

    def get_instruments(self) -> list[dict[str, Any]]:
        """Fetch and cache NFO instruments list for current day."""
        today = datetime.date.today()
        if self._cached_instruments is not None and self._cache_date == today:
            return self._cached_instruments

        if not self._kite:
            return []

        try:
            logger.info("Fetching NFO instruments master from Kite...")
            all_instruments = self._kite.instruments("NFO")
            nifty_options = [
                inst for inst in all_instruments
                if inst.get("name") == "NIFTY" and inst.get("instrument_type") in ("CE", "PE")
            ]
            self._cached_instruments = nifty_options
            self._cache_date = today
            logger.info("Cached %d NIFTY option instruments for %s", len(nifty_options), today)
            return self._cached_instruments
        except Exception as exc:
            logger.warning("Failed to fetch Kite instruments: %s", exc)
            return []

    def get_target_expiry(
        self,
        current_date: datetime.date | None = None,
        roll_if_today: bool = True,
    ) -> datetime.date | None:
        """Find the target weekly expiry date from active Kite instruments."""
        instruments = self.get_instruments()
        today = current_date or datetime.date.today()

        valid_expiries = sorted(list(set(
            inst["expiry"] for inst in instruments
            if inst.get("expiry") and inst["expiry"] >= today
        )))

        if not valid_expiries:
            return None

        earliest = valid_expiries[0]
        if earliest == today and roll_if_today and len(valid_expiries) > 1:
            logger.info("Today is expiry day (%s). Rolling to next expiry (%s).", earliest, valid_expiries[1])
            return valid_expiries[1]

        return earliest

    def select_by_premium(
        self,
        spot: float,
        option_type: str,
        current_date: datetime.date | None = None,
        roll_if_today: bool = True,
    ) -> tuple[str, float]:
        """
        Scans candidate option contracts and picks the one with LTP closest to 1% of spot.

        Returns
        -------
        tuple[str, float]
            (tradingsymbol, ltp)
        """
        option_type = option_type.upper()
        if option_type not in ("CE", "PE"):
            raise ValueError(f"option_type must be 'CE' or 'PE', got '{option_type}'")

        target_premium = round(spot * 0.01, 2)
        instruments = self.get_instruments()
        target_expiry = self.get_target_expiry(current_date=current_date, roll_if_today=roll_if_today)

        if not instruments or not target_expiry or not self._kite:
            fallback_sym = select_strike_fallback(spot, option_type)
            return fallback_sym, target_premium

        # Filter candidate options for expiry and type
        candidates = [
            inst for inst in instruments
            if inst.get("expiry") == target_expiry and inst.get("instrument_type") == option_type
        ]

        # Limit to candidates near ATM (+/- 1200 points) to minimize query payload
        candidates = [c for c in candidates if abs(float(c.get("strike", 0.0)) - spot) <= 1200]

        if not candidates:
            fallback_sym = select_strike_fallback(spot, option_type)
            return fallback_sym, target_premium

        # Query live LTP for candidate strikes
        sym_map = {f"NFO:{c['tradingsymbol']}": c for c in candidates}
        try:
            ltp_data = self._kite.ltp(list(sym_map.keys()))
        except Exception as exc:
            logger.warning("Failed to fetch LTP for strike selection: %s", exc)
            fallback_sym = select_strike_fallback(spot, option_type)
            return fallback_sym, target_premium

        best_symbol = None
        best_diff = float("inf")
        best_ltp = target_premium
        best_strike = 0.0

        for key, data in ltp_data.items():
            ltp = float(data.get("last_price", 0.0))
            if ltp <= 0:
                continue
            diff = abs(ltp - target_premium)
            if diff < best_diff:
                best_diff = diff
                best_symbol = sym_map[key]["tradingsymbol"]
                best_ltp = ltp
                best_strike = float(sym_map[key]["strike"])

        if best_symbol:
            logger.info(
                "Premium-based strike selected: spot=%.1f  target_prem=%.2f  "
                "symbol=%s  strike=%.0f  LTP=%.2f  (diff=%.2f)",
                spot, target_premium, best_symbol, best_strike, best_ltp, best_diff,
            )
            return best_symbol, best_ltp

        fallback_sym = select_strike_fallback(spot, option_type)
        return fallback_sym, target_premium


def select_strike_fallback(spot: float, option_type: str) -> str:
    """Offline analytical fallback strike formula."""
    option_type = option_type.upper()
    if option_type not in ("CE", "PE"):
        raise ValueError(f"option_type must be 'CE' or 'PE', got '{option_type}'")

    atm = nearest_strike(spot)
    offset = nearest_strike(spot * 0.01)

    strike = (atm + offset) if option_type == "CE" else (atm - offset)
    expiry = get_expiry(roll_if_today=True)
    symbol = f"NIFTY{expiry}{strike}{option_type}"
    return symbol


# Global default selector instance
_default_selector = StrikeSelector()


def select_strike(
    spot: float,
    option_type: str,
    kite: KiteConnect | None = None,
) -> str:
    """
    Main strike selector function.
    Uses live LTP scan if kite is provided; otherwise uses fallback formula.
    """
    if kite:
        _default_selector.set_kite(kite)
        symbol, _ = _default_selector.select_by_premium(spot, option_type)
        return symbol
    return select_strike_fallback(spot, option_type)
