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
import re
import time
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
from src.firestore_service import FirestoreService
from src.greeks import calculate_greeks
from src.position_sizer import PositionSizer
from src.signal_engine import SignalEngine
from src.strike_selector import StrikeSelector, select_strike

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
        strategy_name: str = "NIFTY Options Buying Strategy v9",
        firestore_service: FirestoreService | None = None,
        state_manager: Any = None,
        position_sizer: PositionSizer | None = None,
        use_broker_balance: bool = True,
        sizing_mode: str = "FIXED",
    ) -> None:
        self._kite   = kite
        self._engine = signal_engine
        self._notify = notifier
        self._base_qty = lot_size
        self._qty    = lot_size
        self._live   = live_order_placement
        self._strategy_name = strategy_name
        self._firestore = firestore_service
        self._state_manager = state_manager
        self._sizer = position_sizer or PositionSizer(units_per_lot=65)
        self._use_broker_balance = use_broker_balance
        self._sizing_mode = sizing_mode.strip().upper()
        self._strike_selector = StrikeSelector(kite=kite)

        # In-memory position tracking
        self._open_symbol: str | None   = None
        self._entry_price: float | None = None
        self._entry_time: datetime | None = None
        self._current_trade_id: str | None = None
        self._open_product: str = KiteConnect.PRODUCT_MIS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def open_symbol(self) -> str | None:
        return self._open_symbol

    @property
    def entry_price(self) -> float | None:
        return self._entry_price

    @property
    def open_product(self) -> str:
        return self._open_product

    def restore_state(
        self,
        symbol: str | None,
        entry_price: float | None,
        trade_id: str | None = None,
        qty: int | None = None,
        product: str | None = None,
    ) -> None:
        """Restores open position state from disk/reconciliation on startup."""
        self._open_symbol = symbol
        self._entry_price = entry_price
        self._current_trade_id = trade_id
        self._open_product = (product or KiteConnect.PRODUCT_MIS).upper()
        if qty is not None:
            self._qty = qty
        logger.info(
            "OrderManager state restored: symbol=%s entry_price=%s trade_id=%s qty=%s product=%s",
            self._open_symbol, self._entry_price, self._current_trade_id, self._qty, self._open_product,
        )

    def check_margin_sufficient(self, required_margin: float, symbol: str) -> tuple[bool, float]:
        """
        Queries Kite equity margins to verify available live cash balance.
        Returns (is_sufficient, available_balance).
        """
        try:
            margins = self._kite.margins(segment="equity")
            available = margins.get("available", {})
            avail_balance = float(available.get("live_balance") or available.get("cash") or margins.get("net") or 0.0)
            is_sufficient = avail_balance >= required_margin
            return is_sufficient, avail_balance
        except Exception as exc:
            logger.warning("Could not verify Kite account margins: %s. Proceeding with caution.", exc)
            return True, -1.0

    def handle_ce_entry(self, spot: float, candle_time: datetime, indicators: dict) -> None:
        """Buy a CE (call) option based on 1% target premium of current spot price."""
        symbol, ltp = self._strike_selector.select_by_premium(spot, "CE")
        self._buy(symbol, "CE", spot, candle_time, indicators, estimated_ltp=ltp)

    def handle_pe_entry(self, spot: float, candle_time: datetime, indicators: dict) -> None:
        """Buy a PE (put) option based on 1% target premium of current spot price."""
        symbol, ltp = self._strike_selector.select_by_premium(spot, "PE")
        self._buy(symbol, "PE", spot, candle_time, indicators, estimated_ltp=ltp)

    def handle_exit(
        self,
        reason: str,
        exit_price: float = 0.0,
        candle_time: datetime | None = None,
        spot: float | None = None,
    ) -> None:
        """Close the currently open option position."""
        if self._open_symbol is None:
            logger.warning("handle_exit called but no open position tracked.")
            return

        symbol = self._open_symbol
        now_ts = candle_time or datetime.now(tz=IST)

        # Safety: If spot price was accidentally passed as exit_price (> 5000), treat as spot
        if exit_price and exit_price > 5000.0:
            if not spot or spot <= 0:
                spot = exit_price
            exit_price = 0.0

        opt_exit_price: float | None = None

        if self._live:
            exit_product = self._open_product or KiteConnect.PRODUCT_MIS
            try:
                positions_data = self._kite.positions()
                net_positions = positions_data.get("net", []) if isinstance(positions_data, dict) else []
                if isinstance(net_positions, list):
                    for pos in net_positions:
                        if str(pos.get("tradingsymbol", "")) == symbol and int(pos.get("quantity", 0)) > 0:
                            broker_prod = str(pos.get("product") or "").upper()
                            if broker_prod:
                                if broker_prod != exit_product:
                                    logger.warning(
                                        "Exit product adjusted from %s to broker product %s for %s",
                                        exit_product, broker_prod, symbol,
                                    )
                                exit_product = broker_prod
                            break
            except Exception as exc:
                logger.warning("Could not verify broker position product before exit: %s", exc)

            try:
                order_id = self._kite.place_order(
                    variety=KiteConnect.VARIETY_REGULAR,
                    exchange=KiteConnect.EXCHANGE_NFO,
                    tradingsymbol=symbol,
                    transaction_type=KiteConnect.TRANSACTION_TYPE_SELL,
                    quantity=self._qty,
                    product=exit_product,
                    order_type=KiteConnect.ORDER_TYPE_MARKET,
                    validity=KiteConnect.VALIDITY_DAY,
                    market_protection=-1,
                )
                logger.info("LIVE exit order placed: order_id=%s product=%s", order_id, exit_product)
                fill = self._fetch_fill_price(order_id)
                if fill is not None:
                    opt_exit_price = fill
            except Exception as exc:
                logger.error("Exit order failed: %s", exc)
                self._notify.error(f"Exit order failed for {symbol}: {exc}")
                return
        else:
            logger.info("[SIMULATION] Exit order skipped (LIVE_ORDER_PLACEMENT=false)")

        # If fill price wasn't immediately available, try fetching option LTP from Kite
        if opt_exit_price is None and self._kite:
            try:
                ltp_resp = self._kite.ltp(f"NFO:{symbol}")
                if f"NFO:{symbol}" in ltp_resp:
                    last_price = float(ltp_resp[f"NFO:{symbol}"].get("last_price", 0.0))
                    if last_price > 0:
                        opt_exit_price = last_price
            except Exception:
                pass

        # If not determined via fill or Kite LTP, use provided exit_price if valid option price
        if opt_exit_price is None and exit_price and 0 < exit_price <= 5000.0:
            opt_exit_price = exit_price

        # Fallback to entry price if market closed or LTP unavailable (simulates flat exit instead of erroneous 0.0 or spot)
        if opt_exit_price is None or opt_exit_price <= 0:
            opt_exit_price = self._entry_price if (self._entry_price and self._entry_price > 0) else 0.0

        logger.info(
            "Exiting position %s | reason=%s | opt_price=%.2f | spot=%s",
            symbol,
            reason,
            opt_exit_price,
            f"{spot:.2f}" if spot else "N/A",
        )

        # Compute PnL
        pnl = None
        if self._entry_price is not None:
            pnl = round((opt_exit_price - self._entry_price) * self._qty, 2)

        # Notify Discord
        self._notify.exit_signal(
            symbol=symbol,
            reason=reason,
            entry_price=self._entry_price,
            exit_price=opt_exit_price,
            candle_time=now_ts,
            qty=self._qty,
        )

        # Update Firestore
        if self._firestore and self._current_trade_id:
            self._firestore.record_exit(
                trade_id=self._current_trade_id,
                exit_time=now_ts,
                exit_price=opt_exit_price,
                exit_reason=reason,
                pnl=pnl,
            )

        # Update persistent state
        if self._state_manager:
            try:
                self._state_manager.record_exit()
            except Exception as exc:
                logger.warning("Failed to record exit in state manager: %s", exc)

        # Record to local ledger and evaluate dynamic sizing criteria
        try:
            trade_id_val = self._current_trade_id or f"TRADE_{now_ts.strftime('%Y%m%d_%H%M%S')}"
            self._record_and_evaluate_sizing(
                trade_id=trade_id_val,
                symbol=symbol,
                entry_time=self._entry_time or now_ts,
                exit_time=now_ts,
                entry_price=self._entry_price,
                exit_price=opt_exit_price,
                exit_reason=reason,
                pnl=pnl if pnl is not None else 0.0,
                spot_price=spot or 0.0,
            )
        except Exception as exc:
            logger.error("Failed during position sizing evaluation: %s", exc)

        # Update state
        self._engine.on_exit_filled()
        self._open_symbol = None
        self._entry_price = None
        self._entry_time  = None
        self._current_trade_id = None
        self._open_product = KiteConnect.PRODUCT_MIS

    def _record_and_evaluate_sizing(
        self,
        trade_id: str,
        symbol: str,
        entry_time: datetime,
        exit_time: datetime,
        entry_price: float | None,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        spot_price: float = 0.0,
    ) -> None:
        """Appends closed trade to local ledger and runs position sizing evaluation."""
        match = re.search(r"(\d{4,5})(?:CE|PE)$", symbol)
        strike_val = int(match.group(1)) if match else 0
        trade_rec = {
            "trade_id": trade_id,
            "date": exit_time.astimezone(IST).date().isoformat() if hasattr(exit_time, "astimezone") else str(exit_time)[:10],
            "entry_time": entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time),
            "exit_time": exit_time.isoformat() if hasattr(exit_time, "isoformat") else str(exit_time),
            "symbol": symbol,
            "option_type": "CE" if "CE" in symbol else "PE",
            "strike": strike_val,
            "spot_price": spot_price,
            "lot_size": self._qty,
            "entry_price": entry_price or 0.0,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl": pnl,
            "live_trade": self._live,
        }

        # 1. Save to local CSV ledger
        self._sizer.save_live_trade(trade_rec)

        # 2. Fetch trade history: Try Firestore first; fallback to local ledger
        trades: list[dict[str, Any]] = []
        if self._firestore and self._firestore.is_connected:
            trades = self._firestore.get_closed_trades(strategy_name=self._strategy_name)

        if not trades:
            trades = self._sizer.load_live_trades()

        # 3. Evaluate sizing criteria (incorporating real-time Zerodha cash balance)
        broker_cash = None
        if self._use_broker_balance and self._live:
            broker_cash = self._sizer.fetch_broker_cash(self._kite)

        current_lots = max(1, self._qty // 65)
        decision = self._sizer.evaluate(trades, current_lots=current_lots, broker_capital=broker_cash)

        logger.info(
            "[SIZING ENGINE] %s | Current: %d lot(s) | Rec: %d lot(s) | PnL=₹%+,.2f | DD=%.1f%% | %s",
            decision.headline, decision.current_lots, decision.recommended_lots,
            decision.net_pnl, decision.drawdown_pct, decision.reason,
        )

        # 4. Trigger alert on Scale-Up, Step-Down, or every 10 trades milestone
        should_alert = (
            decision.action in ("SCALE_UP", "STEP_DOWN")
            or (decision.total_trades > 0 and decision.total_trades % 10 == 0)
        )

        if should_alert:
            self._notify.sizing_recommendation(
                headline=decision.headline,
                current_lots=decision.current_lots,
                recommended_lots=decision.recommended_lots,
                action=decision.action,
                total_trades=decision.total_trades,
                win_rate=decision.win_rate,
                net_pnl=decision.net_pnl,
                current_equity=decision.current_equity,
                drawdown_pct=decision.drawdown_pct,
                reason=decision.reason,
            )

    def evaluate_sizing_status(self) -> SizingDecision:
        """Runs a standalone sizing evaluation using current history and Zerodha cash."""
        trades: list[dict[str, Any]] = []
        if self._firestore and self._firestore.is_connected:
            trades = self._firestore.get_closed_trades(strategy_name=self._strategy_name)
        if not trades:
            trades = self._sizer.load_live_trades()

        broker_cash = None
        if self._use_broker_balance and self._live:
            broker_cash = self._sizer.fetch_broker_cash(self._kite)

        current_lots = max(1, self._qty // 65)
        return self._sizer.evaluate(trades, current_lots=current_lots, broker_capital=broker_cash)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sync_existing_broker_trade(self, candle_time: datetime) -> bool:
        """
        Checks Kite net positions before placing a new LIVE buy order.
        If an open NIFTY option position or completed trade from today already
        exists on the broker (e.g. placed manually), adopts/syncs engine state
        and returns True so a duplicate order is not placed.
        """
        try:
            positions_data = self._kite.positions()
            net_positions = positions_data.get("net", []) if isinstance(positions_data, dict) else []
            if not isinstance(net_positions, list):
                return False

            # 1. Check for an active open NIFTY option position
            for pos in net_positions:
                qty = int(pos.get("quantity", 0))
                sym = str(pos.get("tradingsymbol", ""))
                if qty > 0 and sym.startswith("NIFTY") and (sym.endswith("CE") or sym.endswith("PE")):
                    opt_type = "CE" if sym.endswith("CE") else "PE"
                    broker_product = str(pos.get("product") or KiteConnect.PRODUCT_MIS).upper()
                    fill = float(pos.get("average_price") or pos.get("buy_price") or 0.0)
                    trade_id = f"SYNC_{candle_time.strftime('%Y%m%d_%H%M%S')}_{opt_type}"
                    self.restore_state(
                        symbol=sym,
                        entry_price=fill,
                        trade_id=trade_id,
                        qty=qty,
                        product=broker_product,
                    )
                    self._entry_time = candle_time
                    self._engine.on_entry_filled(opt_type)
                    if self._state_manager:
                        try:
                            self._state_manager.record_entry(
                                trade_id=trade_id,
                                symbol=sym,
                                option_type=opt_type,
                                entry_price=fill,
                                entry_time=candle_time,
                                qty=qty,
                                spot_price=0.0,
                                product=broker_product,
                            )
                        except Exception as exc:
                            logger.warning("Failed to persist synced position: %s", exc)
                    msg = (
                        f"🔄 **Existing Broker Position Detected — Entry Skipped**\n"
                        f"• Contract: `{sym}` ({opt_type}, {broker_product}, qty={qty}, avg=₹{fill:.2f})\n"
                        f"• Adopted into engine management; skipping duplicate buy order."
                    )
                    logger.warning(msg)
                    self._notify.info(msg)
                    return True

            # 2. Check if a NIFTY option trade was already executed and closed today
            for pos in net_positions:
                sym = str(pos.get("tradingsymbol", ""))
                buy_qty = int(pos.get("day_buy_quantity") or pos.get("buy_quantity") or 0)
                if buy_qty > 0 and sym.startswith("NIFTY") and (sym.endswith("CE") or sym.endswith("PE")):
                    self._engine.restore_state(
                        position=None,
                        trade_today=1,
                        last_reset_date=candle_time.astimezone(IST).date(),
                    )
                    msg = (
                        f"ℹ️ **Broker Trade Already Executed Today — Entry Skipped**\n"
                        f"• Contract: `{sym}` (buy_qty={buy_qty})\n"
                        f"• `trade_today` synced to 1; skipping duplicate buy order."
                    )
                    logger.warning(msg)
                    self._notify.info(msg)
                    return True
        except Exception as exc:
            logger.warning("Could not verify existing Kite positions before entry: %s", exc)

        return False

    def _resolve_compounding_capital(self) -> float:
        """Returns current equity for dynamic lot sizing (live Zerodha cash or initial_capital + net PnL)."""
        if self._use_broker_balance and self._live:
            cash = self._sizer.fetch_broker_cash(self._kite)
            if cash is not None and cash > 0:
                return cash

        trades: list[dict[str, Any]] = []
        if self._firestore and self._firestore.is_connected:
            trades = self._firestore.get_closed_trades(strategy_name=self._strategy_name)
        if not trades:
            trades = self._sizer.load_live_trades()

        net_pnl = sum(float(t.get("pnl") or 0.0) for t in trades)
        return max(0.0, self._sizer.initial_capital + net_pnl)

    def _buy(
        self,
        symbol: str,
        option_type: str,
        spot: float,
        candle_time: datetime,
        indicators: dict,
        estimated_ltp: float | None = None,
    ) -> None:
        logger.info(
            "Placing %s buy | symbol=%s qty=%d live=%s",
            option_type, symbol, self._qty, self._live,
        )

        # Pre-entry broker sync (prevents duplicate orders if a manual trade exists)
        if self._live and self._sync_existing_broker_trade(candle_time):
            return

        # Margin pre-check
        premium_estimate = estimated_ltp if (estimated_ltp and estimated_ltp > 0) else (spot * 0.01)

        # Dynamic capital-compounding lot sizing (when sizing_mode == DYNAMIC)
        if self._sizing_mode == "DYNAMIC":
            capital = self._resolve_compounding_capital()
            dyn_lots = self._sizer.calculate_compounding_lots(capital, premium_estimate)
            self._qty = dyn_lots * self._sizer.units_per_lot
            logger.info(
                "[DYNAMIC SIZING] Capital=₹%,.2f | Alloc=%.0f%% | EstPrem=₹%.2f -> %d lot(s) (%d qty)",
                capital, self._sizer.alloc_pct * 100, premium_estimate, dyn_lots, self._qty,
            )
        else:
            self._qty = self._base_qty

        required_margin = round(premium_estimate * self._qty, 2)

        if self._live:
            is_sufficient, avail_balance = self.check_margin_sufficient(required_margin, symbol)
            if not is_sufficient:
                shortfall = required_margin - avail_balance
                logger.error(
                    "LIVE order aborted for %s: Insufficient margin (Required=₹%.2f, Available=₹%.2f, Shortfall=₹%.2f)",
                    symbol, required_margin, avail_balance, shortfall,
                )
                self._notify.margin_insufficient(
                    symbol=symbol,
                    option_type=option_type,
                    required_margin=required_margin,
                    available_balance=avail_balance,
                    qty=self._qty,
                )
                return

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
                    market_protection=-1,
                )
                logger.info("LIVE buy order placed: order_id=%s", order_id)
                # Fetch average fill price (best-effort, fall back to estimated LTP)
                fill_price = self._fetch_fill_price(order_id)
                if fill_price is None and estimated_ltp and estimated_ltp > 0:
                    fill_price = estimated_ltp
            except Exception as exc:
                logger.error("Buy order failed: %s", exc)
                self._notify.error(f"Buy order failed for {symbol}: {exc}")
                return
        else:
            logger.info("[SIMULATION] Buy order skipped (LIVE_ORDER_PLACEMENT=false)")
            fill_price = estimated_ltp

        # Update state regardless of mode so logic continues correctly
        self._open_symbol = symbol
        self._entry_price = fill_price
        self._entry_time  = candle_time
        self._open_product = KiteConnect.PRODUCT_MIS
        self._engine.on_entry_filled(option_type)

        # Send Discord alert
        rsi      = indicators.get("rsi", 0.0)
        upper_bb = indicators.get("upper_bb", 0.0)
        lower_bb = indicators.get("lower_bb", 0.0)
        r1       = indicators.get("r1", 0.0)
        s1       = indicators.get("s1", 0.0)
        # Strike & Entry Greeks (IV & Delta)
        inst = self._strike_selector.get_instrument(symbol) if hasattr(self._strike_selector, "get_instrument") else None
        strike_num = int(inst["strike"]) if (inst and inst.get("strike")) else None
        if not strike_num:
            match = re.search(r"(\d{4,5})(?:CE|PE)$", symbol)
            strike_num = int(match.group(1)) if match else int(round(spot / 50.0) * 50)

        expiry_date = inst.get("expiry") if inst else getattr(self._strike_selector, "last_selected_expiry", None)
        expiry_display = None
        if expiry_date:
            is_rolled = getattr(self._strike_selector, "last_rolled", False)
            roll_note = " (Rolled - Next Expiry)" if is_rolled else ""
            expiry_display = f"{expiry_date.strftime('%d-%b-%Y')}{roll_note}"

        final_premium = fill_price or estimated_ltp or (spot * 0.01)

        greeks = None
        try:
            greeks = calculate_greeks(
                symbol=symbol,
                spot=spot,
                strike=float(strike_num),
                option_type=option_type,
                premium=final_premium,
                candle_time=candle_time,
                expiry_date=expiry_date,
            )
            logger.info(
                "[ENTRY GREEKS] %s %s Strike=%d | Premium=₹%.2f | IV=%.2f%% | Delta=%+.3f | DTE=%.1fd",
                symbol, option_type, strike_num, final_premium, greeks.iv, greeks.delta, greeks.time_to_expiry_days,
            )
        except Exception as exc:
            logger.warning("Could not calculate Greeks for %s: %s", symbol, exc)

        # Unique Trade ID
        trade_id = f"TRADE_{candle_time.astimezone(IST).strftime('%Y%m%d_%H%M%S')}_{option_type}"
        self._current_trade_id = trade_id

        # Persist open position to disk for crash recovery
        if self._state_manager:
            try:
                self._state_manager.record_entry(
                    trade_id=trade_id,
                    symbol=symbol,
                    option_type=option_type,
                    entry_price=fill_price or spot,
                    entry_time=candle_time,
                    qty=self._qty,
                    spot_price=spot,
                    product=KiteConnect.PRODUCT_MIS,
                )
            except Exception as exc:
                logger.warning("Failed to record entry in state manager: %s", exc)

        # Persist entry to Firestore
        if self._firestore:
            greeks_dict = {
                "iv": greeks.iv,
                "delta": greeks.delta,
                "time_to_expiry_days": greeks.time_to_expiry_days,
            } if greeks else None

            self._firestore.record_entry(
                trade_id=trade_id,
                strategy_name=self._strategy_name,
                symbol=symbol,
                option_type=option_type,
                strike=strike_num,
                spot_price=spot,
                lot_size=self._qty,
                entry_time=candle_time,
                entry_price=fill_price or spot,
                indicators=indicators,
                live_trade=self._live,
                greeks=greeks_dict,
            )

        iv_val = greeks.iv if greeks else None
        delta_val = greeks.delta if greeks else None

        if option_type == "CE":
            self._notify.ce_entry(
                symbol=symbol, strike=strike_num, spot=spot, qty=self._qty,
                candle_time=candle_time, rsi=rsi, upper_bb=upper_bb, r1=r1,
                entry_price=fill_price,
                iv=iv_val,
                delta=delta_val,
                expiry=expiry_display,
            )
        else:
            self._notify.pe_entry(
                symbol=symbol, strike=strike_num, spot=spot, qty=self._qty,
                candle_time=candle_time, rsi=rsi, lower_bb=lower_bb, s1=s1,
                entry_price=fill_price,
                iv=iv_val,
                delta=delta_val,
                expiry=expiry_display,
            )

    def _fetch_fill_price(self, order_id: str, retries: int = 3, delay_s: float = 0.3) -> float | None:
        """
        Fetch average fill price from Kite order book with brief retries
        so market fills have time to settle in Kite's order API.
        Returns None if unavailable (graceful degradation).
        """
        for attempt in range(retries):
            try:
                orders = self._kite.orders()
                for o in orders:
                    if str(o.get("order_id")) == str(order_id):
                        avg = float(o.get("average_price") or 0)
                        if avg > 0:
                            return avg
                        status = str(o.get("status") or "").upper()
                        if status in ("REJECTED", "CANCELLED"):
                            return None
            except Exception as exc:
                logger.warning("Could not fetch fill price (attempt %d): %s", attempt + 1, exc)
                return None

            if attempt < retries - 1:
                time.sleep(delay_s)
        return None

