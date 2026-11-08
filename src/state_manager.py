"""
src/state_manager.py
====================
Manages persistent runtime state and crash recovery for Nifty Strike Engine.

Lifecycle:
----------
  1. When an option order is placed in the exchange:
     Creates `data/active_position.json` with the active trade details.
  2. When the position exits:
     Removes `data/active_position.json` from disk completely.
  3. On startup/crash recovery:
     - If `data/active_position.json` exists:
       Recovers open position and confirms with broker (Kite) in live mode.
     - If `data/active_position.json` does NOT exist:
       In live mode, queries Kite net positions to verify if any position is open,
       or if a trade was already completed earlier today (via `day_buy_quantity`).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

if TYPE_CHECKING:
    from kiteconnect import KiteConnect
    from src.discord_notifier import DiscordNotifier
    from src.order_manager import OrderManager
    from src.signal_engine import SignalEngine

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = "data/active_position.json"


class StateManager:
    """
    Persists and recovers active trading state.
    """

    def __init__(self, state_file: str = DEFAULT_STATE_FILE) -> None:
        self.state_file = state_file
        dirname = os.path.dirname(self.state_file)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

    # ------------------------------------------------------------------
    # Disk I/O (Atomic)
    # ------------------------------------------------------------------

    def load_active_position(self) -> dict[str, Any] | None:
        """
        Load active position from disk.
        Returns dict if file exists and is valid, else None.
        """
        if not os.path.exists(self.state_file):
            return None

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("symbol"):
                    return data
                return None
        except Exception as exc:
            logger.warning("Failed to read active position file '%s': %s", self.state_file, exc)
            return None

    def save_state(self, state: dict[str, Any]) -> None:
        """Atomically write active position to disk via a temporary file."""
        dirname = os.path.dirname(self.state_file) or "."
        try:
            temp_file = tempfile.NamedTemporaryFile("w", dir=dirname, delete=False, encoding="utf-8")
            json.dump(state, temp_file, indent=2, default=str)
            temp_file.flush()
            temp_file.close()
            os.replace(temp_file.name, self.state_file)
            logger.info("Created active position file: %s", self.state_file)
        except Exception as exc:
            logger.error("Failed to save active position to %s: %s", self.state_file, exc)
            if "temp_file" in locals() and os.path.exists(temp_file.name):
                try:
                    os.remove(temp_file.name)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Position Lifecycle
    # ------------------------------------------------------------------

    def record_entry(
        self,
        trade_id: str,
        symbol: str,
        option_type: str,
        entry_price: float,
        entry_time: datetime,
        qty: int,
        spot_price: float,
    ) -> None:
        """
        Creates active position json file only when a position is placed.
        """
        today_str = datetime.now(tz=IST).strftime("%Y-%m-%d")
        state: dict[str, Any] = {
            "date": today_str,
            "trade_id": trade_id,
            "symbol": symbol,
            "option_type": option_type.upper(),
            "entry_price": float(entry_price),
            "entry_time": entry_time.isoformat(),
            "qty": int(qty),
            "spot_price": float(spot_price),
        }
        self.save_state(state)

    def record_exit(self) -> None:
        """
        Removes the active position json file when a position exits.
        """
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
                logger.info("Position exited. Removed %s from disk.", self.state_file)
            except Exception as exc:
                logger.warning("Could not remove active position file %s: %s", self.state_file, exc)

    def record_daily_reset(self, new_date: date) -> None:
        """
        Removes any leftover active position file on a new trading day.
        """
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
                logger.info("New trading day %s. Removed stale %s.", new_date.isoformat(), self.state_file)
            except Exception as exc:
                logger.warning("Could not remove stale %s: %s", self.state_file, exc)

    # ------------------------------------------------------------------
    # Reconciliation & Crash Recovery
    # ------------------------------------------------------------------

    def reconcile(
        self,
        kite: KiteConnect | None,
        order_manager: OrderManager,
        signal_engine: SignalEngine,
        live_mode: bool,
        notifier: DiscordNotifier | None = None,
    ) -> dict[str, Any]:
        """
        Reconciles in-memory state with disk and broker positions on startup.
        """
        today = datetime.now(tz=IST).date()
        today_str = today.isoformat()
        active_pos = self.load_active_position()

        # --------------------------------------------------------------
        # Case 1: Active position file exists on disk
        # --------------------------------------------------------------
        if active_pos is not None:
            saved_date = active_pos.get("date", "")

            # Stale position from a previous calendar day
            if saved_date != today_str:
                logger.warning(
                    "Found stale active position from %s on disk. Cleaning up for fresh day %s.",
                    saved_date, today_str,
                )
                self.record_exit()
                signal_engine.restore_state(position=None, trade_today=0, last_reset_date=today)
                order_manager.restore_state(symbol=None, entry_price=None, trade_id=None)
                return {"status": "fresh_day", "trade_today": 0, "open_position": None}

            # File belongs to today!
            symbol = str(active_pos.get("symbol"))
            opt_type = str(active_pos.get("option_type", "CE"))
            entry_price = float(active_pos.get("entry_price", 0.0))
            trade_id = str(active_pos.get("trade_id", f"REC_{today_str}_{opt_type}"))
            qty = int(active_pos.get("qty", 65))

            if live_mode and kite:
                broker_pos = self._find_broker_open_position(kite)
                if broker_pos:
                    # Verified open on Kite! Re-adopt it.
                    broker_sym = broker_pos["tradingsymbol"]
                    broker_qty = int(broker_pos["quantity"])
                    broker_fill = float(broker_pos.get("average_price") or broker_pos.get("buy_price") or entry_price)

                    order_manager.restore_state(symbol=broker_sym, entry_price=broker_fill, trade_id=trade_id, qty=broker_qty)
                    signal_engine.restore_state(position=opt_type, trade_today=1, last_reset_date=today)

                    recovery_msg = (
                        f"🔄 **Crash Recovery: Position Re-Adopted**\n"
                        f"• Contract: `{broker_sym}` ({opt_type})\n"
                        f"• Quantity: {broker_qty} units\n"
                        f"• Entry Price: ₹{broker_fill:.2f}\n"
                        f"• Status: Re-linked to active trailing stop & 15:00 EOD exit."
                    )
                    logger.warning(recovery_msg)
                    if notifier:
                        notifier.info(recovery_msg)

                    return {"status": "recovered_live_position", "trade_today": 1, "symbol": broker_sym}
                else:
                    # File exists but position was closed on broker while offline!
                    logger.warning("Position %s was closed on broker while offline. Removing json file.", symbol)
                    self.record_exit()
                    signal_engine.restore_state(position=None, trade_today=1, last_reset_date=today)
                    order_manager.restore_state(symbol=None, entry_price=None, trade_id=None)

                    offline_exit_msg = (
                        f"⚠️ **Crash Recovery: Position Closed Externally**\n"
                        f"• Contract `{symbol}` closed on broker while engine was offline.\n"
                        f"• Removed active position file; `trade_today` preserved as 1."
                    )
                    if notifier:
                        notifier.info(offline_exit_msg)

                    return {"status": "closed_externally", "trade_today": 1, "open_position": None}

            else:
                # Simulation Mode
                order_manager.restore_state(symbol=symbol, entry_price=entry_price, trade_id=trade_id, qty=qty)
                signal_engine.restore_state(position=opt_type, trade_today=1, last_reset_date=today)
                logger.info("Restored simulated position from disk: %s (entry=%.2f)", symbol, entry_price)
                return {"status": "recovered_simulation_position", "trade_today": 1, "symbol": symbol}

        # --------------------------------------------------------------
        # Case 2: No active position file exists on disk
        # --------------------------------------------------------------
        if live_mode and kite:
            # Check if broker has an open position (e.g. orphan position)
            broker_pos = self._find_broker_open_position(kite)
            if broker_pos:
                broker_sym = broker_pos["tradingsymbol"]
                broker_qty = int(broker_pos["quantity"])
                opt_type = "CE" if broker_sym.endswith("CE") else "PE"
                broker_fill = float(broker_pos.get("average_price") or broker_pos.get("buy_price") or 0.0)
                trade_id = f"ORPHAN_{today_str}_{opt_type}"

                order_manager.restore_state(symbol=broker_sym, entry_price=broker_fill, trade_id=trade_id, qty=broker_qty)
                signal_engine.restore_state(position=opt_type, trade_today=1, last_reset_date=today)

                # Create the active position file now that it is adopted
                self.record_entry(
                    trade_id=trade_id,
                    symbol=broker_sym,
                    option_type=opt_type,
                    entry_price=broker_fill,
                    entry_time=datetime.now(tz=IST),
                    qty=broker_qty,
                    spot_price=0.0,
                )

                orphan_msg = (
                    f"🔄 **Orphan Position Discovered on Broker**\n"
                    f"• Contract: `{broker_sym}` ({opt_type})\n"
                    f"• Adopted into engine management. Created {self.state_file}."
                )
                logger.warning(orphan_msg)
                if notifier:
                    notifier.info(orphan_msg)

                return {"status": "orphan_position_adopted", "trade_today": 1, "symbol": broker_sym}

            # No open position on broker: check if a trade completed earlier today
            traded_today = self._has_broker_traded_today(kite)
            trade_today_val = 1 if traded_today else 0

            signal_engine.restore_state(position=None, trade_today=trade_today_val, last_reset_date=today)
            order_manager.restore_state(symbol=None, entry_price=None, trade_id=None)
            logger.info("No active position file. Broker status: traded_today=%s", traded_today)
            return {"status": "synced", "trade_today": trade_today_val, "open_position": None}

        # Simulation mode and no file
        signal_engine.restore_state(position=None, trade_today=0, last_reset_date=today)
        order_manager.restore_state(symbol=None, entry_price=None, trade_id=None)
        return {"status": "clean", "trade_today": 0, "open_position": None}

    # ------------------------------------------------------------------
    # Broker Inspection Helpers
    # ------------------------------------------------------------------

    def _find_broker_open_position(self, kite: KiteConnect) -> dict[str, Any] | None:
        """Inspects Kite net positions for an open NIFTY option."""
        try:
            positions_data = kite.positions()
            net_positions = positions_data.get("net", [])
            for pos in net_positions:
                qty = int(pos.get("quantity", 0))
                sym = str(pos.get("tradingsymbol", ""))
                product = str(pos.get("product", ""))
                if qty > 0 and sym.startswith("NIFTY") and (sym.endswith("CE") or sym.endswith("PE")):
                    logger.info("Found open broker position: %s (qty=%d product=%s)", sym, qty, product)
                    return pos
        except Exception as exc:
            logger.error("Failed to query Kite positions: %s", exc)
        return None

    def _has_broker_traded_today(self, kite: KiteConnect) -> bool:
        """Checks if a NIFTY option trade was already executed and closed today on Kite."""
        try:
            positions_data = kite.positions()
            net_positions = positions_data.get("net", [])
            for pos in net_positions:
                sym = str(pos.get("tradingsymbol", ""))
                buy_qty = int(pos.get("day_buy_quantity") or pos.get("buy_quantity") or 0)
                if buy_qty > 0 and sym.startswith("NIFTY") and (sym.endswith("CE") or sym.endswith("PE")):
                    logger.info("Found completed trade on broker today: %s (buy_qty=%d)", sym, buy_qty)
                    return True
        except Exception as exc:
            logger.error("Failed to check broker trade history: %s", exc)
        return False
