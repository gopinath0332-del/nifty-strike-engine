"""
src/candle_builder.py
=====================
Aggregates raw tick prices into fixed-width OHLCV candles.

Usage
-----
    builder = CandleBuilder(interval_minutes=3)
    closed  = builder.on_tick(price, volume, timestamp)
    if closed:
        candle = builder.last_closed_candle
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

logger = logging.getLogger(__name__)


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime      # candle open time (IST-aware)
    is_closed: bool = False

    def __repr__(self) -> str:
        ts = self.timestamp.strftime("%H:%M")
        return (
            f"Candle({ts} O={self.open:.2f} H={self.high:.2f} "
            f"L={self.low:.2f} C={self.close:.2f} V={self.volume:.0f})"
        )


class CandleBuilder:
    """
    Builds OHLCV candles from individual price ticks.

    Parameters
    ----------
    interval_minutes : int
        Width of each candle in minutes (default 3).
    """

    def __init__(self, interval_minutes: int = 3) -> None:
        self.interval_minutes = interval_minutes
        self._current: Candle | None = None
        self._current_slot: int | None = None
        self.last_closed_candle: Candle | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_tick(self, price: float, volume: float, ts: datetime) -> bool:
        """
        Feed a single tick into the builder.

        Returns True when a candle has just closed (i.e. the previous slot
        ended). Callers should read ``last_closed_candle`` in that case.

        Parameters
        ----------
        price  : last traded price
        volume : cumulative volume from exchange (we store delta per candle)
        ts     : tick timestamp (timezone-aware; will be converted to IST)
        """
        ist_ts  = ts.astimezone(IST)
        slot    = self._candle_slot(ist_ts)
        closed  = False

        if self._current_slot is None:
            # Very first tick
            self._open_new_candle(price, volume, ist_ts, slot)

        elif slot != self._current_slot:
            # Candle boundary crossed → close current, open new
            self._current.is_closed = True
            self.last_closed_candle = self._current
            logger.debug("Candle closed: %s", self._current)
            self._open_new_candle(price, volume, ist_ts, slot)
            closed = True

        else:
            # Same candle — update OHLCV
            c = self._current
            c.high   = max(c.high, price)
            c.low    = min(c.low, price)
            c.close  = price
            c.volume += volume

        return closed

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _candle_slot(self, ts: datetime) -> int:
        """
        Unique integer slot index for a candle boundary.
        Slots are counted from midnight in ``interval_minutes`` steps.
        """
        minutes_since_midnight = ts.hour * 60 + ts.minute
        return minutes_since_midnight // self.interval_minutes

    def _open_new_candle(
        self, price: float, volume: float, ts: datetime, slot: int
    ) -> None:
        # Align timestamp to candle-open time
        slot_minute = slot * self.interval_minutes
        open_time   = ts.replace(
            hour=slot_minute // 60,
            minute=slot_minute % 60,
            second=0,
            microsecond=0,
        )
        self._current      = Candle(
            open=price, high=price, low=price, close=price,
            volume=volume, timestamp=open_time
        )
        self._current_slot = slot
        logger.debug("Candle opened: %s", self._current)

