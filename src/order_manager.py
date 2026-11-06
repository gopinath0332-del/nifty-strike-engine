"""
src/order_manager.py
====================
Wraps Zerodha Kite Connect REST API for placing and closing option orders.

LIVE_ORDER_PLACEMENT gate
--------------------------
  If ``live_order_placement=False`` (default / simulation mode):
    - All logic executes normally
    - Discord alerts fire
    - No real orders are sent to the exchange
    - Simulated fills are acknowledged immediately

  Set ``live_order_placement=True`` only when ready to trade real money.

Order details
-------------
  Product  : MIS (intraday margin)
  Order type: MARKET
  Validity : DAY
  Exchange : NFO (NSE Futures & Options)
  Qty      : as configured (default 65 units per lot)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

from kiteconnect import KiteConnect

from src.discord_notifier import DiscordNotifier
from src.signal_engine import SignalEngine
from src.strike_selector import select_strike

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Places and closes option orders via Kite Connect.

    Parameters
    ----------
    kite                 : Authenticated KiteConnect instance.
    signal_engine        : SignalEngine for state callbacks.
    notifier             : DiscordNotifier instance.
    lot_size             : Units per lot (default 65).
    live_order_placement : If False, simulate fills without hitting exchange.
    """

    def __init__(
        self,
        kite: KiteConnect,
        signal_engine: SignalEngine,
        notifier: DiscordNotifier,
        lot_size: int = 65,
        live_order_placement: bool = False,
    ) -> None:
        self._kite   = kite
        self._engine = signal_engine
        self._notify = notifier
        self._qty    = lot_size
        self._live   = live_order_placement

        # In-memory position tracking
        self._open_symbol: str | None   = None
        self._entry_price: float | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def open_symbol(self) -> str | None:
        return self._open_symbol

    @property
    def entry_price(self) -> float | None:
        return self._entry_price

    def handle_ce_entry(self, spot: float, candle_time: datetime, indicators: dict) -> None:
        """Buy a CE (call) option based on current spot price."""
        symbol = select_strike(spot, "CE")
        self._buy(symbol, "CE", spot, candle_time, indicators)

    def handle_pe_entry(self, spot: float, candle_time: datetime, indicators: dict) -> None:
        """Buy a PE (put) option based on current spot price."""
        symbol = select_strike(spot, "PE")
        self._buy(symbol, "PE", spot, candle_time, indicators)

    def handle_exit(self, reason: str, exit_price: float, candle_time: datetime) -> None:
        """Close the currently open option position."""
        if self._open_symbol is None:
            logger.warning("handle_exit called but no open position tracked.")
            return

        symbol = self._open_symbol
        logger.info("Exiting position %s | reason=%s | price=%.2f", symbol, reason, exit_price)

        if self._live:
            try:
                order_id = self._kite.place_order(
                    variety=KiteConnect.VARIETY_REGULAR,
                    exchange=KiteConnect.EXCHANGE_NFO,
                    tradingsymbol=symbol,
                    transaction_type=KiteConnect.TRANSACTION_TYPE_SELL,
                    quantity=self._qty,
                    product=KiteConnect.PRODUCT_MIS,
                    order_type=KiteConnect.ORDER_TYPE_MARKET,
                    validity=KiteConnect.VALIDITY_DAY,
                )
                logger.info("LIVE exit order placed: order_id=%s", order_id)
            except Exception as exc:
                logger.error("Exit order failed: %s", exc)
                self._notify.error(f"Exit order failed for {symbol}: {exc}")
                return
        else:
            logger.info("[SIMULATION] Exit order skipped (LIVE_ORDER_PLACEMENT=false)")

        # Notify Discord
        self._notify.exit_signal(
            symbol=symbol,
            reason=reason,
            entry_price=self._entry_price,
            exit_price=exit_price,
            candle_time=candle_time,
        )

        # Update state
        self._engine.on_exit_filled()
        self._open_symbol = None
        self._entry_price = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _buy(
        self,
        symbol: str,
        option_type: str,
        spot: float,
        candle_time: datetime,
        indicators: dict,
    ) -> None:
        logger.info(
            "Placing %s buy | symbol=%s qty=%d live=%s",
            option_type, symbol, self._qty, self._live,
        )

        fill_price: float | None = None

        if self._live:
            try:
                order_id = self._kite.place_order(
                    variety=KiteConnect.VARIETY_REGULAR,
                    exchange=KiteConnect.EXCHANGE_NFO,
                    tradingsymbol=symbol,
                    transaction_type=KiteConnect.TRANSACTION_TYPE_BUY,
                    quantity=self._qty,
                    product=KiteConnect.PRODUCT_MIS,
                    order_type=KiteConnect.ORDER_TYPE_MARKET,
                    validity=KiteConnect.VALIDITY_DAY,
                )
                logger.info("LIVE buy order placed: order_id=%s", order_id)
                # Fetch average fill price (best-effort)
                fill_price = self._fetch_fill_price(order_id)
            except Exception as exc:
                logger.error("Buy order failed: %s", exc)
                self._notify.error(f"Buy order failed for {symbol}: {exc}")
                return
        else:
            logger.info("[SIMULATION] Buy order skipped (LIVE_ORDER_PLACEMENT=false)")

        # Update state regardless of mode so logic continues correctly
        self._open_symbol = symbol
        self._entry_price = fill_price
        self._engine.on_entry_filled(option_type)

        # Send Discord alert
        rsi      = indicators.get("rsi", 0.0)
        upper_bb = indicators.get("upper_bb", 0.0)
        lower_bb = indicators.get("lower_bb", 0.0)
        r1       = indicators.get("r1", 0.0)
        s1       = indicators.get("s1", 0.0)
        strike   = int(symbol[-8:-2]) if len(symbol) >= 8 else 0

        if option_type == "CE":
            self._notify.ce_entry(
                symbol=symbol, strike=strike, spot=spot, qty=self._qty,
                candle_time=candle_time, rsi=rsi, upper_bb=upper_bb, r1=r1,
            )
        else:
            self._notify.pe_entry(
                symbol=symbol, strike=strike, spot=spot, qty=self._qty,
                candle_time=candle_time, rsi=rsi, lower_bb=lower_bb, s1=s1,
            )

    def _fetch_fill_price(self, order_id: str) -> float | None:
        """
        Fetch average fill price from Kite order book.
        Returns None if unavailable (graceful degradation).
        """
        try:
            orders = self._kite.orders()
            for o in orders:
                if str(o.get("order_id")) == str(order_id):
                    return float(o.get("average_price") or 0) or None
        except Exception as exc:
            logger.warning("Could not fetch fill price: %s", exc)
        return None

