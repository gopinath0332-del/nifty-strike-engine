"""
src/signal_engine.py
====================
Evaluates entry and exit conditions on each closed 3-minute candle,
mirroring the NIFTY Options Buying Strategy v9 Pine Script logic exactly.

State managed here
------------------
  position     : None | 'CE' | 'PE'   — currently held option type
  trade_today  : int (0 or 1)         — single counter; resets daily
                 Once a trade fires (CE or PE), no more entries that day.

Entry conditions (CE — Call)
-----------------------------
  bull_trend  AND rsi > 70  AND close > R1  AND close > upper_bb
  AND no open position  AND trade_today == 0  AND time < ENTRY_CUTOFF

Entry conditions (PE — Put)
-----------------------------
  bear_trend  AND rsi < 30  AND close < S1  AND close < lower_bb
  AND no open position  AND trade_today == 0  AND time < ENTRY_CUTOFF

Exit conditions
---------------
  Supertrend flip  : CE held AND bear_trend  → "ST Flip Exit"
                     PE held AND bull_trend  → "ST Flip Exit"
  Hard EOD exit    : time >= EOD_EXIT_TIME AND open position → "EOD 15:00 Exit"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

from src.candle_builder import Candle

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """Returned by ``SignalEngine.evaluate()`` for every closed candle."""

    # Entry signals
    ce_entry: bool = False
    pe_entry: bool = False

    # Exit signals
    exit_ce: bool = False
    exit_pe: bool = False
    exit_reason: str = ""

    # Indicator snapshot (for Discord notifications)
    rsi: float | None = None
    bull_trend: bool = False
    bear_trend: bool = False
    close: float | None = None
    r1: float | None = None
    s1: float | None = None
    upper_bb: float | None = None
    lower_bb: float | None = None

    @property
    def has_entry(self) -> bool:
        return self.ce_entry or self.pe_entry

    @property
    def has_exit(self) -> bool:
        return self.exit_ce or self.exit_pe


class SignalEngine:
    """
    Stateful signal evaluator.  Feed it one closed candle at a time via
    ``evaluate()``.

    Parameters
    ----------
    entry_cutoff : datetime.time
        No new entries at or after this time (default 14:45 IST).
    eod_exit     : datetime.time
        Force-close all positions at this time (default 15:00 IST).
    """

    def __init__(
        self,
        entry_cutoff: time = time(14, 45),
        eod_exit: time = time(15, 0),
    ) -> None:
        self.entry_cutoff = entry_cutoff
        self.eod_exit     = eod_exit

        # Mutable state
        self._position: str | None = None   # None | 'CE' | 'PE'
        self._trade_today: int     = 0      # 0 or 1
        self._last_reset_date: date | None = None

    # ------------------------------------------------------------------
    # Properties (read-only view for order manager / session manager)
    # ------------------------------------------------------------------

    @property
    def position(self) -> str | None:
        return self._position

    @property
    def trade_today(self) -> int:
        return self._trade_today

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def _maybe_reset_daily(self, candle_date: date) -> None:
        """Reset per-day counters when the trading date changes."""
        if candle_date != self._last_reset_date:
            if self._last_reset_date is not None:
                logger.info(
                    "New trading day %s — resetting daily counters.", candle_date
                )
            self._trade_today      = 0
            self._last_reset_date  = candle_date

    # ------------------------------------------------------------------
    # State setters (called by OrderManager after fills confirmed)
    # ------------------------------------------------------------------

    def on_entry_filled(self, option_type: str) -> None:
        """Call after a CE or PE buy order is confirmed filled."""
        self._position    = option_type.upper()
        self._trade_today = 1
        logger.info("Position opened: %s  trade_today=%d", self._position, self._trade_today)

    def on_exit_filled(self) -> None:
        """Call after an exit order is confirmed filled."""
        logger.info("Position closed: was %s", self._position)
        self._position = None

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def evaluate(self, candle: Candle, indicators: dict) -> SignalResult:
        """
        Evaluate entry and exit signals for a just-closed candle.

        Parameters
        ----------
        candle     : The closed Candle object.
        indicators : Dict with keys:
                       bull_trend, bear_trend, rsi,
                       upper_bb, lower_bb, r1, s1

        Returns
        -------
        SignalResult
        """
        result = SignalResult(
            rsi      = indicators.get("rsi"),
            bull_trend = indicators.get("bull_trend", False),
            bear_trend = indicators.get("bear_trend", False),
            close    = candle.close,
            r1       = indicators.get("r1"),
            s1       = indicators.get("s1"),
            upper_bb = indicators.get("upper_bb"),
            lower_bb = indicators.get("lower_bb"),
        )

        candle_time = candle.timestamp.astimezone(IST).time()
        candle_date = candle.timestamp.astimezone(IST).date()

        # Reset daily counters on new day
        self._maybe_reset_daily(candle_date)

        # ---- EOD force-close (highest priority) ----------------------
        if candle_time >= self.eod_exit and self._position is not None:
            if self._position == "CE":
                result.exit_ce    = True
            else:
                result.exit_pe    = True
            result.exit_reason = "EOD 15:00 Exit"
            logger.info("EOD exit triggered at %s", candle_time)
            return result

        bull = indicators.get("bull_trend", False)
        bear = indicators.get("bear_trend", False)
        rsi_val   = indicators.get("rsi")
        close     = candle.close
        r1        = indicators.get("r1")
        s1        = indicators.get("s1")
        upper_bb  = indicators.get("upper_bb")
        lower_bb  = indicators.get("lower_bb")

        # ---- Supertrend flip exits -----------------------------------
        if self._position == "CE" and bear:
            result.exit_ce     = True
            result.exit_reason = "ST Flip Exit"
            logger.info("Supertrend flip exit (CE) at %s", candle_time)
            return result

        if self._position == "PE" and bull:
            result.exit_pe     = True
            result.exit_reason = "ST Flip Exit"
            logger.info("Supertrend flip exit (PE) at %s", candle_time)
            return result

        # ---- Entry conditions ----------------------------------------
        entry_allowed = (
            candle_time < self.entry_cutoff
            and self._position is None
            and self._trade_today == 0
            and rsi_val is not None
            and r1 is not None
            and s1 is not None
            and upper_bb is not None
            and lower_bb is not None
        )

        if entry_allowed:
            # CE (Call) entry
            if bull and rsi_val > 70 and close > r1 and close > upper_bb:
                result.ce_entry = True
                logger.info(
                    "CE entry signal at %s | close=%.2f R1=%.2f upperBB=%.2f RSI=%.2f",
                    candle_time, close, r1, upper_bb, rsi_val,
                )

            # PE (Put) entry — mutually exclusive (only one trade/day)
            elif bear and rsi_val < 30 and close < s1 and close < lower_bb:
                result.pe_entry = True
                logger.info(
                    "PE entry signal at %s | close=%.2f S1=%.2f lowerBB=%.2f RSI=%.2f",
                    candle_time, close, s1, lower_bb, rsi_val,
                )

        return result

