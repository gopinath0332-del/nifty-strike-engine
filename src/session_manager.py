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
from datetime import datetime, time, timedelta, timezone

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
from src.state_manager import StateManager

logger = logging.getLogger(__name__)
signals_logger = logging.getLogger("signals")
NIFTY_TOKEN = 256265           # Kite instrument token for NIFTY 50 index

# Minimum candles in the rolling buffer before signals are evaluated
# (needs at least BB_LENGTH=20 candles for valid indicators)
MIN_CANDLES = 25


def is_weekend(dt: datetime) -> bool:
    """Returns True if dt (in IST) falls on Saturday (5) or Sunday (6)."""
    return dt.weekday() >= 5


def is_market_hours(dt: datetime) -> bool:
    """
    Returns True if dt (in IST) is within active NSE trading hours.
    NSE regular market hours: 09:15 to 15:30 IST on Monday-Friday.
    We allow tick stream connection starting at 09:14 IST.
    """
    if is_weekend(dt):
        return False
    t = dt.time()
    return time(9, 14) <= t <= time(15, 30)


class SessionManager:
    """
    Manages a full intraday trading session.

    Parameters
    ----------
    settings : Settings
        Loaded application settings.
    """

    def __init__(
        self,
        settings: Settings,
        kite_client: KiteConnect | None = None,
        notifier: DiscordNotifier | None = None,
        firestore: FirestoreService | None = None,
        state_manager: StateManager | None = None,
    ) -> None:
        self._s = settings

        # Build Kite REST client (or use injected client)
        self._kite = (
            kite_client
            if kite_client is not None
            else build_authenticated_kite(
                settings.kite_api_key, settings.kite_access_token
            )
        )

        # Core components
        self._notifier = notifier or DiscordNotifier(
            webhook_url=settings.discord_webhook_url,
            live_order_placement=settings.live_order_placement,
        )
        self._firestore = firestore or FirestoreService(
            credentials_path=settings.firestore_credentials_path,
            collection_name=settings.firestore_collection,
        )
        # State Manager (Crash Recovery & Position Persistence)
        self._state_manager = state_manager or StateManager()

        self._engine = SignalEngine(
            entry_cutoff=time(*settings.entry_cutoff_hhmm),
            eod_exit=time(*settings.eod_exit_hhmm),
            state_manager=self._state_manager,
        )
        self._order_mgr = OrderManager(
            kite=self._kite,
            signal_engine=self._engine,
            notifier=self._notifier,
            lot_size=settings.lot_size,
            live_order_placement=settings.live_order_placement,
            strategy_name=settings.strategy_name,
            firestore_service=self._firestore,
            state_manager=self._state_manager,
        )

        # Run startup reconciliation and recovery
        logger.info("Running startup state reconciliation...")
        rec_result = self._state_manager.reconcile(
            kite=self._kite,
            order_manager=self._order_mgr,
            signal_engine=self._engine,
            live_mode=settings.live_order_placement,
            notifier=self._notifier,
        )
        logger.info("Reconciliation complete: %s", rec_result.get("status"))
        self._candle_builder = CandleBuilder(
            interval_minutes=settings.candle_interval_minutes
        )

        # Rolling OHLCV buffer (deque of dicts → converted to DataFrame on demand)
        self._candle_buffer: deque[dict] = deque(maxlen=200)

        # Pre-seed historical candles on startup so indicators & pivots are ready
        self.preseed_historical_candles(count=50)

        # Latest known NIFTY spot price (from ticks)
        self._spot: float = 0.0

        # Daily metrics for EOD summary
        self._candles_evaluated_today: int = 0
        self._session_date: date = datetime.now(tz=IST).date()

        self._ticker: KiteTicker | None = None
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public entry point & Market Hours Orchestration
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the session. Enforces market hours and weekend bypass."""
        mode = "LIVE" if self._s.live_order_placement else "SIMULATION"
        logger.info("Starting Nifty Strike Engine in %s mode.", mode)
        self._running = True

        if not self._s.enforce_market_hours:
            logger.info("Market hours enforcement disabled. Connecting WebSocket directly.")
            self._start_active_session()
            return

        now_ist = datetime.now(tz=IST)

        # 1. Weekend Check (Saturday & Sunday)
        if is_weekend(now_ist):
            day_name = now_ist.strftime("%A")
            logger.info(
                "⏸️ Today is %s (Weekend). NSE market is closed. WebSocket connection bypassed.",
                day_name,
            )
            self._notifier.info(
                f"⏸️ **Nifty Strike Engine in Weekend Standby**\n"
                f"• Today is **{day_name}** — Indian markets (NSE) are closed.\n"
                f"• WebSocket connection bypassed.\n"
                f"• Engine is sleeping in standby mode, awaiting Monday 09:14 IST open."
            )
            self._standby_loop()
            return

        # 2. Weekday Outside Market Hours Check
        if not is_market_hours(now_ist):
            t = now_ist.time()
            if t < time(9, 14):
                logger.info(
                    "⏸️ Pre-market standby (%s IST). Waiting for market open at 09:14 IST.",
                    now_ist.strftime("%H:%M"),
                )
                self._notifier.info(
                    f"⏳ **Nifty Strike Engine — Pre-Market Standby**\n"
                    f"• Time: **{now_ist.strftime('%H:%M:%S')} IST**\n"
                    f"• Historical Pre-seed: **{len(self._candle_buffer)} candles loaded** ✅\n"
                    f"• Status: Service online & waiting for 09:14 IST morning briefing."
                )
            else:
                logger.info(
                    "⏸️ Post-market standby (%s IST). Market closed for today.",
                    now_ist.strftime("%H:%M"),
                )
            self._standby_loop()
            return

        # 3. Active market session!
        self._start_active_session()

    def _start_active_session(self) -> None:
        """Starts WebSocket and watchdog for an active trading session."""
        if len(self._candle_buffer) < MIN_CANDLES:
            self.preseed_historical_candles(count=50)
        self._send_morning_briefing()
        threading.Thread(target=self._market_close_watchdog, daemon=True).start()
        self._start_ticker()

    def preseed_historical_candles(self, count: int = 50) -> int:
        """
        Pre-seeds historical NIFTY 50 candles into `_candle_buffer`.

        Fetches the last `count` closed 5-minute candles from the Kite historical API.
        This warms up Supertrend, RSI, Bollinger Bands, and Traditional Pivots so
        the engine is ready to evaluate signals on the very first candle of the day
        without waiting 2 hours (25 candles) for buffer warm-up.

        Returns the number of candles loaded into the buffer.
        """
        if not self._kite:
            logger.debug("Kite client not initialized; skipping candle pre-seeding.")
            return 0

        try:
            now_ist = datetime.now(tz=IST)
            from_dt = now_ist.date() - timedelta(days=6)
            interval_str = (
                "minute"
                if self._s.candle_interval_minutes == 1
                else f"{self._s.candle_interval_minutes}minute"
            )

            records = self._kite.historical_data(
                instrument_token=NIFTY_TOKEN,
                from_date=from_dt,
                to_date=now_ist,
                interval=interval_str,
            )

            if not records:
                logger.warning("No historical candles returned for NIFTY 50 token %d.", NIFTY_TOKEN)
                return 0

            # Take the latest `count` closed bars
            recent_records = records[-count:] if len(records) > count else records

            self._candle_buffer.clear()
            for rec in recent_records:
                rec_dt = rec.get("date")
                if hasattr(rec_dt, "tzinfo") and rec_dt.tzinfo is not None:
                    ist_ts = rec_dt.astimezone(IST)
                elif hasattr(rec_dt, "replace"):
                    ist_ts = rec_dt.replace(tzinfo=IST)
                elif isinstance(rec_dt, str):
                    try:
                        parsed_dt = datetime.fromisoformat(rec_dt)
                        ist_ts = parsed_dt if parsed_dt.tzinfo else parsed_dt.replace(tzinfo=IST)
                    except Exception:
                        ist_ts = now_ist
                else:
                    ist_ts = now_ist

                self._candle_buffer.append({
                    "open": float(rec.get("open", 0.0)),
                    "high": float(rec.get("high", 0.0)),
                    "low": float(rec.get("low", 0.0)),
                    "close": float(rec.get("close", 0.0)),
                    "volume": float(rec.get("volume", 0.0)),
                    "timestamp": ist_ts,
                    "date": ist_ts.date(),
                })

            logger.info(
                "Pre-seeded %d historical %s candles into buffer (Buffer size: %d).",
                len(self._candle_buffer),
                interval_str,
                len(self._candle_buffer),
            )
            return len(self._candle_buffer)

        except Exception as exc:
            logger.warning("Could not pre-seed historical candles: %s", exc)
            return len(self._candle_buffer)

    def _send_morning_briefing(self) -> None:
        """Gathers account, token, and daily pivot data to dispatch the 09:14 AM morning briefing."""
        mode = "LIVE" if self._s.live_order_placement else "SIMULATION"
        user_id = "Zerodha User"
        available_cash: float | None = None
        prev_close: float | None = None
        pivot: float | None = None
        r1: float | None = None
        s1: float | None = None

        # 1. Fetch user profile / ID
        try:
            profile = self._kite.profile()
            user_id = profile.get("user_id", user_id)
        except Exception as exc:
            logger.debug("Could not fetch user profile: %s", exc)

        # 2. Fetch cash margin
        try:
            margins = self._kite.margins(segment="equity")
            avail = margins.get("available", {})
            available_cash = float(avail.get("live_balance") or avail.get("cash") or margins.get("net") or 0.0)
        except Exception as exc:
            logger.debug("Could not fetch margins: %s", exc)

        # 3. Fetch previous day High, Low, Close and compute Pivots
        try:
            today = datetime.now(tz=IST).date()
            from_dt = today - timedelta(days=6)
            records = self._kite.historical_data(
                instrument_token=NIFTY_TOKEN,
                from_date=from_dt,
                to_date=today,
                interval="day",
            )
            if records:
                prev_bar = records[-1]
                rec_date = prev_bar.get("date")
                if hasattr(rec_date, "date") and rec_date.date() == today and len(records) >= 2:
                    prev_bar = records[-2]

                ph = float(prev_bar["high"])
                pl = float(prev_bar["low"])
                pc = float(prev_bar["close"])

                prev_close = pc
                p = (ph + pl + pc) / 3.0
                pivot = round(p, 2)
                r1 = round(2.0 * p - pl, 2)
                s1 = round(2.0 * p - ph, 2)
        except Exception as exc:
            logger.debug("Could not compute daily pivots from historical data: %s", exc)

        # 4. Dispatch Discord briefing
        try:
            self._notifier.morning_briefing(
                user_id=user_id,
                mode=mode,
                lot_size=self._s.lot_size,
                timeframe=self._s.candle_interval_minutes,
                entry_cutoff=self._s.entry_cutoff_time,
                eod_exit=self._s.eod_exit_time,
                available_cash=available_cash,
                prev_close=prev_close,
                pivot=pivot,
                r1=r1,
                s1=s1,
            )
            logger.info(
                "Morning briefing dispatched to Discord: User=%s, Cash=%s, Pivots(P=%.2f, R1=%.2f, S1=%.2f)",
                user_id,
                f"₹{available_cash:,.2f}" if available_cash is not None else "N/A",
                pivot or 0.0,
                r1 or 0.0,
                s1 or 0.0,
            )
        except Exception as exc:
            logger.warning("Failed to send morning briefing: %s", exc)

    def _standby_loop(self) -> None:
        """Standby loop when market is closed, checking every 60s until market opens."""
        logger.info("Entering low-power standby loop. Checking market status every 60s...")
        while self._running:
            now_ist = datetime.now(tz=IST)
            if is_market_hours(now_ist):
                logger.info("Market session is now active (%s IST)! Launching WebSocket...", now_ist.strftime("%H:%M:%S"))
                self._start_active_session()
                break

            # Sleep in small increments so SIGINT/SIGTERM cleanly interrupt
            for _ in range(60):
                if not self._running:
                    break
                _time.sleep(1)

    def _market_close_watchdog(self) -> None:
        """Monitors time during active session and shuts down WebSocket at 15:30 IST."""
        while self._running:
            now_ist = datetime.now(tz=IST)
            if now_ist.time() >= time(15, 30):
                logger.info("Market close reached (15:30 IST). Dispatching EOD summary and stopping session...")
                self._send_eod_summary(now_ist)
                self.stop()
                break
            _time.sleep(10)

    def _send_eod_summary(self, now_ist: datetime) -> None:
        """Dispatches the 15:30 IST EOD Session Summary embed to Discord."""
        date_str = now_ist.strftime("%Y-%m-%d")
        today = now_ist.date()

        # 1. Day Range of NIFTY
        day_high: float | None = None
        day_low: float | None = None

        # Inspect local candle buffer for today's bars
        today_candles = [c for c in self._candle_buffer if c.get("date") == today]
        if today_candles:
            day_high = max(c["high"] for c in today_candles)
            day_low = min(c["low"] for c in today_candles)

        # Fallback to Kite OHLC API if local buffer is empty or partial
        if (day_high is None or day_low is None) and self._kite:
            try:
                ohlc_resp = self._kite.ohlc(["NSE:NIFTY 50"])
                data = ohlc_resp.get("NSE:NIFTY 50", {}).get("ohlc", {})
                day_high = day_high or data.get("high")
                day_low = day_low or data.get("low")
            except Exception as exc:
                logger.debug("Could not fetch NIFTY OHLC for EOD summary: %s", exc)

        range_str = (
            f"₹{day_low:,.2f} – ₹{day_high:,.2f}"
            if (day_high and day_low and day_high > 0 and day_low > 0)
            else "N/A"
        )

        # 2. Trades taken summary
        trades_count = self._engine.trade_today
        if trades_count == 0:
            trades_str = "0 (No breakout met all 4 filter criteria)"
        else:
            trades_str = f"{trades_count} (Completed & Managed)"

        # 3. Next session label (Friday -> Monday, Mon-Thu -> tomorrow)
        next_session = "Monday 08:50 AM IST" if now_ist.weekday() == 4 else "tomorrow 08:50 AM IST"

        # 4. Dispatch embed
        try:
            self._notifier.eod_summary(
                date_str=date_str,
                signals_evaluated=self._candles_evaluated_today,
                trades_taken_str=trades_str,
                day_range_str=range_str,
                next_session_str=next_session,
            )
            logger.info(
                "EOD Summary dispatched: Date=%s, Candles=%d, Trades=%d, Range=%s",
                date_str, self._candles_evaluated_today, trades_count, range_str,
            )
        except Exception as exc:
            logger.warning("Failed to dispatch EOD summary: %s", exc)

    def stop(self) -> None:
        """Gracefully stop the WebSocket and exit."""
        logger.info("Stopping session manager...")
        self._running = False
        if self._ticker:
            try:
                self._ticker.stop()
            except Exception as exc:
                logger.debug("Error stopping ticker: %s", exc)
        now_ist = datetime.now(tz=IST)
        if is_market_hours(now_ist):
            self._notifier.info("🛑 Nifty Strike Engine stopped during active market session.")
        else:
            self._notifier.info("⏸️ Nifty Strike Engine stopped (Pre-market restart / standby).")
        self._notifier.stop()

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

            # Track daily evaluation count
            c_date = ist_ts.date()
            if c_date != self._session_date:
                self._candles_evaluated_today = 0
                self._session_date = c_date
            self._candles_evaluated_today += 1

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
                st_buffer_points=self._s.st_buffer_points,
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

