"""
src/session_manager.py
======================
Top-level orchestrator that ties together:
  - Kite WebSocket (live tick subscription)
  - CandleBuilder (3-min OHLCV aggregation)
  - Indicators (Supertrend, RSI, BB, Pivots)
  - SignalEngine (entry / exit evaluation)
  - OrderManager (order placement / simulation)
  - DiscordNotifier (alerts)

NIFTY 50 instrument token: 256265 (NSE)
This is the index token used for tick subscription.

Data flow
---------
  Kite WS tick
    └─► CandleBuilder.on_tick()
          └─► [candle closed] → update rolling DataFrame
                └─► apply_all(indicators)
                      └─► SignalEngine.evaluate()
                            └─► OrderManager.handle_*()
                                  └─► DiscordNotifier.*()
"""

from __future__ import annotations

import logging
import threading
import time as _time
from collections import deque
from datetime import datetime, time, timezone

import pandas as pd

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

from kiteconnect import KiteTicker

from config.settings import Settings
from src.candle_builder import Candle, CandleBuilder
from src.discord_notifier import DiscordNotifier
from src.firestore_service import FirestoreService
from src.indicators import apply_all
from src.kite_auth import build_authenticated_kite
from src.order_manager import OrderManager
from src.signal_engine import SignalEngine

logger = logging.getLogger(__name__)
signals_logger = logging.getLogger("signals")
NIFTY_TOKEN = 256265           # Kite instrument token for NIFTY 50 index

# Minimum candles in the rolling buffer before signals are evaluated
# (needs at least BB_LENGTH=20 candles for valid indicators)
MIN_CANDLES = 25


class SessionManager:
    """
    Manages a full intraday trading session.

    Parameters
    ----------
    settings : Settings
        Loaded application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self._s = settings

        # Build Kite REST client
        self._kite = build_authenticated_kite(
            settings.kite_api_key, settings.kite_access_token
        )

        # Core components
        self._notifier = DiscordNotifier(
            webhook_url=settings.discord_webhook_url,
            live_order_placement=settings.live_order_placement,
        )
        self._firestore = FirestoreService(
            credentials_path=settings.firestore_credentials_path,
            collection_name=settings.firestore_collection,
        )
        self._engine = SignalEngine(
            entry_cutoff=time(*settings.entry_cutoff_hhmm),
            eod_exit=time(*settings.eod_exit_hhmm),
        )
        self._order_mgr = OrderManager(
            kite=self._kite,
            signal_engine=self._engine,
            notifier=self._notifier,
            lot_size=settings.lot_size,
            live_order_placement=settings.live_order_placement,
            strategy_name=settings.strategy_name,
            firestore_service=self._firestore,
        )
        self._candle_builder = CandleBuilder(
            interval_minutes=settings.candle_interval_minutes
        )

        # Rolling OHLCV buffer (deque of dicts → converted to DataFrame on demand)
        self._candle_buffer: deque[dict] = deque(maxlen=200)

        # Latest known NIFTY spot price (from ticks)
        self._spot: float = 0.0

        self._ticker: KiteTicker | None = None
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the WebSocket and block until the session ends."""
        mode = "LIVE" if self._s.live_order_placement else "SIMULATION"
        logger.info("Starting Nifty Strike Engine in %s mode.", mode)
        self._notifier.info(
            f"🚀 Nifty Strike Engine started in **{mode}** mode.\n"
            f"Timeframe: {self._s.candle_interval_minutes}m | "
            f"Lot: {self._s.lot_size} units | "
            f"Entry cutoff: {self._s.entry_cutoff_time} | "
            f"EOD exit: {self._s.eod_exit_time}"
        )
        self._running = True
        self._start_ticker()

    def stop(self) -> None:
        """Gracefully stop the WebSocket and exit."""
        logger.info("Stopping session manager...")
        self._running = False
        if self._ticker:
            self._ticker.stop()
        self._notifier.info("🛑 Nifty Strike Engine stopped.")

    # ------------------------------------------------------------------
    # Kite WebSocket callbacks
    # ------------------------------------------------------------------

    def _start_ticker(self) -> None:
        self._ticker = KiteTicker(
            api_key=self._s.kite_api_key,
            access_token=self._s.kite_access_token,
        )
        self._ticker.on_connect    = self._on_connect
        self._ticker.on_ticks      = self._on_ticks
        self._ticker.on_close      = self._on_close
        self._ticker.on_error      = self._on_error
        self._ticker.on_reconnect  = self._on_reconnect
        self._ticker.on_noreconnect = self._on_noreconnect

        # Blocking call — runs the event loop internally
        self._ticker.connect(threaded=False)

    def _on_connect(self, ws, response) -> None:  # noqa: ANN001
        logger.info("WebSocket connected. Subscribing to NIFTY 50 (token %d).", NIFTY_TOKEN)
        self._ticker.subscribe([NIFTY_TOKEN])
        self._ticker.set_mode(self._ticker.MODE_FULL, [NIFTY_TOKEN])

    def _on_ticks(self, ws, ticks: list[dict]) -> None:  # noqa: ANN001
        for tick in ticks:
            if tick.get("instrument_token") != NIFTY_TOKEN:
                continue

            ltp: float   = tick.get("last_price", 0.0)
            vol: float   = tick.get("volume_traded", 0.0)
            ts: datetime = tick.get("timestamp") or datetime.now(tz=timezone.utc)

            if ltp <= 0:
                continue

            self._spot = ltp
            closed     = self._candle_builder.on_tick(ltp, vol, ts)

            if closed:
                candle = self._candle_builder.last_closed_candle
                # Run indicator + signal evaluation in a separate thread
                # so we don't block the WebSocket receive loop
                threading.Thread(
                    target=self._process_closed_candle,
                    args=(candle,),
                    daemon=True,
                ).start()

    def _on_close(self, ws, code, reason) -> None:  # noqa: ANN001
        logger.warning("WebSocket closed: code=%s reason=%s", code, reason)

    def _on_error(self, ws, code, reason) -> None:  # noqa: ANN001
        logger.error("WebSocket error: code=%s reason=%s", code, reason)
        self._notifier.error(f"WebSocket error (code={code}): {reason}")

    def _on_reconnect(self, ws, attempts_count) -> None:  # noqa: ANN001
        logger.warning("WebSocket reconnecting (attempt %d)…", attempts_count)

    def _on_noreconnect(self, ws) -> None:  # noqa: ANN001
        logger.critical("WebSocket exhausted reconnect attempts. Shutting down.")
        self._notifier.error("WebSocket lost connection permanently. Bot is offline.")
        self._running = False

    # ------------------------------------------------------------------
    # Candle processing pipeline
    # ------------------------------------------------------------------

    def _process_closed_candle(self, candle: Candle) -> None:
        """
        Called (in a background thread) each time a 3-min candle closes.
        Updates indicator buffer, evaluates signals, dispatches orders.
        """
        try:
            ist_ts = candle.timestamp.astimezone(IST)

            # Append candle to rolling buffer
            self._candle_buffer.append({
                "open":    candle.open,
                "high":    candle.high,
                "low":     candle.low,
                "close":   candle.close,
                "volume":  candle.volume,
                "timestamp": ist_ts,
                "date":    ist_ts.date(),
            })

            if len(self._candle_buffer) < MIN_CANDLES:
                logger.debug(
                    "Buffer warming up: %d / %d candles",
                    len(self._candle_buffer), MIN_CANDLES,
                )
                return

            # Build DataFrame and compute indicators
            df = pd.DataFrame(list(self._candle_buffer))
            df = apply_all(
                df,
                st_atr_period=self._s.st_atr_period,
                st_factor=self._s.st_factor,
                rsi_length=self._s.rsi_length,
                bb_length=self._s.bb_length,
                bb_mult=self._s.bb_mult,
            )

            # Extract the latest row's indicator values
            last = df.iloc[-1]
            indicators = {
                "bull_trend": bool(last.get("bull_trend", False)),
                "bear_trend": bool(last.get("bear_trend", False)),
                "rsi":        float(last["rsi"])     if pd.notna(last.get("rsi"))      else None,
                "upper_bb":   float(last["bb_upper"]) if pd.notna(last.get("bb_upper")) else None,
                "lower_bb":   float(last["bb_lower"]) if pd.notna(last.get("bb_lower")) else None,
                "r1":         float(last["r1"])      if pd.notna(last.get("r1"))       else None,
                "s1":         float(last["s1"])      if pd.notna(last.get("s1"))       else None,
            }

            logger.debug(
                "Candle %s | C=%.2f bull=%s bear=%s RSI=%.1f",
                ist_ts.strftime("%H:%M"),
                candle.close,
                indicators["bull_trend"],
                indicators["bear_trend"],
                indicators.get("rsi") or 0,
            )

            # Evaluate signals
            result = self._engine.evaluate(candle, indicators)

            signals_logger.info(
                "[CANDLE EVAL] Time=%s | Close=%.2f | Bull=%s Bear=%s | RSI=%.1f | "
                "UpperBB=%.2f LowerBB=%.2f | R1=%.2f S1=%.2f | "
                "Signals: CE_Entry=%s PE_Entry=%s Exit_CE=%s Exit_PE=%s (Reason='%s')",
                ist_ts.strftime("%Y-%m-%d %H:%M:%S"),
                candle.close,
                indicators["bull_trend"],
                indicators["bear_trend"],
                indicators.get("rsi") or 0.0,
                indicators.get("upper_bb") or 0.0,
                indicators.get("lower_bb") or 0.0,
                indicators.get("r1") or 0.0,
                indicators.get("s1") or 0.0,
                result.ce_entry,
                result.pe_entry,
                result.exit_ce,
                result.exit_pe,
                result.exit_reason,
            )

            # Dispatch orders based on signals
            if result.exit_ce or result.exit_pe:
                self._order_mgr.handle_exit(
                    reason=result.exit_reason,
                    exit_price=self._spot,
                    candle_time=candle.timestamp,
                )

            elif result.ce_entry:
                self._order_mgr.handle_ce_entry(
                    spot=self._spot,
                    candle_time=candle.timestamp,
                    indicators=indicators,
                )

            elif result.pe_entry:
                self._order_mgr.handle_pe_entry(
                    spot=self._spot,
                    candle_time=candle.timestamp,
                    indicators=indicators,
                )

        except Exception as exc:
            logger.exception("Unhandled error in _process_closed_candle: %s", exc)
            self._notifier.error(f"Internal error processing candle: {exc}")

