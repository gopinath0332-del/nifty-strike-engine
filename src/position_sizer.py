"""
src/position_sizer.py
=====================
Dynamic Position Sizing & Scaling Engine for Nifty Strike Engine.

Implements a Tiered Profit-Cushion (Fixed-Ratio) model with strict Step-Down
Drawdown protection.

Core Rules:
-----------
1. Scale-Up Gate:
   - Account must have accumulated enough profit cushion (e.g. ₹40,000 per lot).
   - Must have executed a minimum number of trades on the current tier (e.g. 40 trades).
   - Drawdown must be below the step-down threshold.
2. Step-Down Circuit Breaker:
   - If current drawdown from peak equity exceeds the threshold (e.g. 12%),
     the engine immediately recommends de-scaling (e.g. dropping from 2 lots to 1 lot).
3. Dual Persistence:
   - Loads trade history from Google Cloud Firestore if connected.
   - Saves and mirrors every completed trade to a local CSV ledger (`data/live_trades.csv`).
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_LIVE_TRADES_CSV = os.path.join(
    os.path.dirname(__file__), "..", "data", "live_trades.csv"
)


@dataclass
class SizingDecision:
    """Encapsulates the recommendation and status from the Sizing Engine."""
    recommended_lots: int
    current_lots: int
    action: str              # "MAINTAIN", "SCALE_UP", "STEP_DOWN"
    current_equity: float
    peak_equity: float
    drawdown_inr: float
    drawdown_pct: float
    total_trades: int
    net_pnl: float
    win_rate: float
    trades_on_tier: int
    trades_needed: int
    pnl_needed: float
    pnl_progress_pct: float
    headline: str
    reason: str

    @property
    def recommended_units(self) -> int:
        return self.recommended_lots * 65


class PositionSizer:
    """
    Evaluates trading history against quantitative scaling criteria and
    determines recommended lot sizes.
    """

    def __init__(
        self,
        initial_capital: float = 100000.0,
        base_lots: int = 1,
        max_lots: int = 10,
        profit_step_per_lot: float = 40000.0,
        min_trades_per_scale: int = 40,
        step_down_dd_pct: float = 12.0,
        units_per_lot: int = 65,
        alloc_pct: float = 0.35,
    ) -> None:
        self.initial_capital = float(initial_capital)
        self.base_lots = int(base_lots)
        self.max_lots = int(max_lots)
        self.profit_step_per_lot = float(profit_step_per_lot)
        self.min_trades_per_scale = int(min_trades_per_scale)
        self.step_down_dd_pct = float(step_down_dd_pct)
        self.units_per_lot = int(units_per_lot)
        self.alloc_pct = float(alloc_pct)

    def calculate_compounding_lots(self, capital: float, option_premium: float) -> int:
        """
        Computes dynamic lot count using capital-allocation compounding:
            lots = clamp(floor((capital * alloc_pct) / (option_premium * units_per_lot)), base_lots, max_lots)
        """
        est_lot_cost = float(option_premium) * self.units_per_lot
        if est_lot_cost <= 0 or capital <= 0:
            return self.base_lots
        lots = int((float(capital) * self.alloc_pct) // est_lot_cost)
        return max(self.base_lots, min(self.max_lots, lots))

    # ------------------------------------------------------------------
    # Evaluation Logic
    # ------------------------------------------------------------------

    @staticmethod
    def fetch_broker_cash(kite: Any) -> float | None:
        """
        Queries Kite equity margins to retrieve real-time available liquid cash balance.
        Returns float in INR or None on error/simulation.
        """
        if kite is None:
            return None
        try:
            margins = kite.margins(segment="equity")
            available = margins.get("available", {})
            cash = available.get("cash") or available.get("live_balance") or margins.get("net")
            if cash is not None:
                return float(cash)
        except Exception as exc:
            logger.warning("Could not fetch Zerodha equity margins: %s", exc)
        return None

    def evaluate(
        self,
        trades: list[dict[str, Any]] | pd.DataFrame,
        current_lots: int | None = None,
        broker_capital: float | None = None,
    ) -> SizingDecision:
        """
        Evaluate past trade history and decide whether to scale up, step down, or maintain size.
        If broker_capital is provided (from live Kite margins), it validates real margin availability.
        """
        active_lots = current_lots if current_lots is not None else self.base_lots

        if isinstance(trades, list):
            df = pd.DataFrame(trades) if trades else pd.DataFrame()
        else:
            df = trades.copy() if not trades.empty else pd.DataFrame()

        effective_base_cap = broker_capital if (broker_capital is not None and broker_capital > 0) else self.initial_capital

        if df.empty or "pnl" not in df.columns:
            # No trades executed yet
            return SizingDecision(
                recommended_lots=self.base_lots,
                current_lots=active_lots,
                action="MAINTAIN",
                current_equity=effective_base_cap,
                peak_equity=effective_base_cap,
                drawdown_inr=0.0,
                drawdown_pct=0.0,
                total_trades=0,
                net_pnl=0.0,
                win_rate=0.0,
                trades_on_tier=0,
                trades_needed=self.min_trades_per_scale,
                pnl_needed=self.profit_step_per_lot,
                pnl_progress_pct=0.0,
                headline="Base Sizing Active",
                reason=f"No trades logged yet. Starting at base size of {self.base_lots} lot ({self.base_lots * self.units_per_lot} units) with ₹{effective_base_cap:,.2f} capital.",
            )

        # Clean and calculate running performance
        df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)
        total_trades = len(df)
        net_pnl = float(df["pnl"].sum())
        winning_trades = int((df["pnl"] > 0).sum())
        win_rate = round((winning_trades / total_trades) * 100, 1) if total_trades > 0 else 0.0

        cum_pnl = df["pnl"].cumsum()
        running_equity = self.initial_capital + cum_pnl
        peak_equity = float(max(self.initial_capital, running_equity.max()))
        current_equity = broker_capital if (broker_capital is not None and broker_capital > 0) else float(self.initial_capital + net_pnl)

        drawdown_inr = round(max(0.0, peak_equity - (self.initial_capital + net_pnl)), 2)
        drawdown_pct = round((drawdown_inr / peak_equity) * 100, 2) if peak_equity > 0 else 0.0

        # Target cumulative profit required to justify active_lots + 1
        tier_step = active_lots - self.base_lots + 1
        target_pnl_for_next = tier_step * self.profit_step_per_lot
        pnl_needed = max(0.0, target_pnl_for_next - net_pnl)
        pnl_progress_pct = min(100.0, max(0.0, (net_pnl / target_pnl_for_next) * 100)) if target_pnl_for_next > 0 else 100.0

        # Estimate trades on current tier (fallback to total_trades % min_trades if not explicitly recorded)
        trades_on_tier = total_trades if active_lots == self.base_lots else (total_trades - (tier_step - 1) * self.min_trades_per_scale)
        trades_on_tier = max(0, trades_on_tier)
        trades_needed = max(0, self.min_trades_per_scale - trades_on_tier)

        # 1. Check Step-Down Circuit Breaker first (Capital Preservation)
        if drawdown_pct >= self.step_down_dd_pct and active_lots > self.base_lots:
            new_lots = max(self.base_lots, active_lots - 1)
            return SizingDecision(
                recommended_lots=new_lots,
                current_lots=active_lots,
                action="STEP_DOWN",
                current_equity=current_equity,
                peak_equity=peak_equity,
                drawdown_inr=drawdown_inr,
                drawdown_pct=drawdown_pct,
                total_trades=total_trades,
                net_pnl=net_pnl,
                win_rate=win_rate,
                trades_on_tier=trades_on_tier,
                trades_needed=trades_needed,
                pnl_needed=pnl_needed,
                pnl_progress_pct=pnl_progress_pct,
                headline="⚠️ Step-Down Triggered",
                reason=(
                    f"Drawdown ({drawdown_pct:.1f}%) breached threshold ({self.step_down_dd_pct:.1f}%). "
                    f"Protect capital by stepping down from {active_lots} to {new_lots} lot(s)."
                ),
            )

        # 2. Check Scale-Up Criteria
        can_scale_up = (
            active_lots < self.max_lots
            and trades_on_tier >= self.min_trades_per_scale
            and net_pnl >= target_pnl_for_next
            and drawdown_pct < (self.step_down_dd_pct / 2.0)  # Must be healthy (not near DD)
        )

        if can_scale_up:
            new_lots = active_lots + 1
            # Check live broker margin capacity if broker capital is provided
            est_margin_needed = new_lots * 24000.0 * 1.5  # 1.5x buffer for drawdowns
            if broker_capital is not None and broker_capital < est_margin_needed:
                return SizingDecision(
                    recommended_lots=active_lots,
                    current_lots=active_lots,
                    action="MAINTAIN",
                    current_equity=current_equity,
                    peak_equity=peak_equity,
                    drawdown_inr=drawdown_inr,
                    drawdown_pct=drawdown_pct,
                    total_trades=total_trades,
                    net_pnl=net_pnl,
                    win_rate=win_rate,
                    trades_on_tier=trades_on_tier,
                    trades_needed=0,
                    pnl_needed=0.0,
                    pnl_progress_pct=100.0,
                    headline="Margin Insufficient for Scale-Up",
                    reason=(
                        f"Strategy profit target reached (₹{net_pnl:+,.2f}), but Zerodha available cash (₹{broker_capital:,.2f}) "
                        f"is below recommended margin buffer of ₹{est_margin_needed:,.2f} for {new_lots} lots. Holding at {active_lots} lot(s)."
                    ),
                )

            cap_note = f" (Zerodha Cash: ₹{broker_capital:,.2f})" if broker_capital is not None else ""
            return SizingDecision(
                recommended_lots=new_lots,
                current_lots=active_lots,
                action="SCALE_UP",
                current_equity=current_equity,
                peak_equity=peak_equity,
                drawdown_inr=drawdown_inr,
                drawdown_pct=drawdown_pct,
                total_trades=total_trades,
                net_pnl=net_pnl,
                win_rate=win_rate,
                trades_on_tier=trades_on_tier,
                trades_needed=0,
                pnl_needed=0.0,
                pnl_progress_pct=100.0,
                headline="🚀 Scale-Up Milestone Reached!",
                reason=(
                    f"Completed {trades_on_tier} trades on tier with ₹{net_pnl:+,.2f} profit "
                    f"(target was ₹{target_pnl_for_next:,.2f}){cap_note}. "
                    f"Eligible to scale from {active_lots} lot(s) to {new_lots} lot(s) ({new_lots * self.units_per_lot} units)."
                ),
            )

        # 3. Maintain Current Size
        return SizingDecision(
            recommended_lots=active_lots,
            current_lots=active_lots,
            action="MAINTAIN",
            current_equity=current_equity,
            peak_equity=peak_equity,
            drawdown_inr=drawdown_inr,
            drawdown_pct=drawdown_pct,
            total_trades=total_trades,
            net_pnl=net_pnl,
            win_rate=win_rate,
            trades_on_tier=trades_on_tier,
            trades_needed=trades_needed,
            pnl_needed=pnl_needed,
            pnl_progress_pct=pnl_progress_pct,
            headline="Maintaining Current Lot Size",
            reason=(
                f"Currently on {active_lots} lot(s). "
                f"Progress to next lot: {trades_on_tier}/{self.min_trades_per_scale} trades, "
                f"₹{net_pnl:+,.2f}/₹{target_pnl_for_next:,.2f} profit ({pnl_progress_pct:.1f}%)."
            ),
        )

    # ------------------------------------------------------------------
    # Backtest Simulation
    # ------------------------------------------------------------------

    def simulate_backtest(self, trades_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Simulates dynamic position scaling over a historical backtest dataframe.
        Compares dynamic scaling results directly against fixed 1-lot baseline.
        """
        if trades_df.empty:
            return trades_df, {}

        sim_df = trades_df.copy().reset_index(drop=True)
        active_lots = self.base_lots
        trades_on_tier = 0

        sim_lots = []
        sim_pnl = []
        sim_equity = []
        sim_actions = []

        running_equity = self.initial_capital
        peak_equity = self.initial_capital

        events = []

        for idx, row in sim_df.iterrows():
            opt_pts = float(row.get("opt_points", 0.0))
            trade_dt = str(row.get("date", ""))

            # Calculate PnL with currently active lots
            trade_pnl = round(opt_pts * (active_lots * self.units_per_lot), 2)
            running_equity += trade_pnl
            peak_equity = max(peak_equity, running_equity)

            dd_inr = peak_equity - running_equity
            dd_pct = (dd_inr / peak_equity) * 100 if peak_equity > 0 else 0.0

            trades_on_tier += 1
            action_taken = "MAINTAIN"

            # Check Step-Down
            if dd_pct >= self.step_down_dd_pct and active_lots > self.base_lots:
                old_lots = active_lots
                active_lots = max(self.base_lots, active_lots - 1)
                trades_on_tier = 0
                action_taken = f"STEP_DOWN ({old_lots}->{active_lots})"
                events.append({
                    "date": trade_dt,
                    "trade_num": idx + 1,
                    "type": "STEP_DOWN",
                    "details": f"Drawdown reached {dd_pct:.1f}%. Stepped down from {old_lots} to {active_lots} lot(s).",
                })

            # Check Scale-Up
            else:
                tier_step = active_lots - self.base_lots + 1
                target_pnl = tier_step * self.profit_step_per_lot
                total_profit = running_equity - self.initial_capital

                if (
                    active_lots < self.max_lots
                    and trades_on_tier >= self.min_trades_per_scale
                    and total_profit >= target_pnl
                    and dd_pct < (self.step_down_dd_pct / 2.0)
                ):
                    old_lots = active_lots
                    active_lots = min(self.max_lots, active_lots + 1)
                    trades_on_tier = 0
                    action_taken = f"SCALE_UP ({old_lots}->{active_lots})"
                    events.append({
                        "date": trade_dt,
                        "trade_num": idx + 1,
                        "type": "SCALE_UP",
                        "details": f"Profit hit ₹{total_profit:,.2f} over {self.min_trades_per_scale}+ trades. Scaled up to {active_lots} lot(s).",
                    })

            sim_lots.append(active_lots)
            sim_pnl.append(trade_pnl)
            sim_equity.append(running_equity)
            sim_actions.append(action_taken)

        sim_df["dynamic_lots"] = sim_lots
        sim_df["dynamic_pnl"] = sim_pnl
        sim_df["dynamic_equity"] = sim_equity
        sim_df["dynamic_action"] = sim_actions

        # Cumulative DD for dynamic sizing
        sim_df["dynamic_peak"] = sim_df["dynamic_equity"].cummax()
        sim_df["dynamic_dd_inr"] = sim_df["dynamic_equity"] - sim_df["dynamic_peak"]
        sim_df["dynamic_dd_pct"] = (sim_df["dynamic_dd_inr"] / sim_df["dynamic_peak"]) * 100

        # Summary Metrics
        fixed_pnl = float(sim_df["pnl"].sum())
        dynamic_pnl = float(sim_df["dynamic_pnl"].sum())
        fixed_dd = float(abs((sim_df["pnl"].cumsum() - sim_df["pnl"].cumsum().cummax()).min()))
        dynamic_dd = float(abs(sim_df["dynamic_dd_inr"].min()))
        dynamic_max_dd_pct = float(abs(sim_df["dynamic_dd_pct"].min()))

        metrics = {
            "initial_capital": self.initial_capital,
            "final_fixed_equity": self.initial_capital + fixed_pnl,
            "final_dynamic_equity": self.initial_capital + dynamic_pnl,
            "fixed_net_pnl": fixed_pnl,
            "dynamic_net_pnl": dynamic_pnl,
            "pnl_uplift_pct": round(((dynamic_pnl - fixed_pnl) / fixed_pnl) * 100, 1) if fixed_pnl > 0 else 0.0,
            "fixed_max_dd_inr": fixed_dd,
            "dynamic_max_dd_inr": dynamic_dd,
            "dynamic_max_dd_pct": dynamic_max_dd_pct,
            "max_lots_reached": int(sim_df["dynamic_lots"].max()),
            "scale_events": events,
        }

        return sim_df, metrics

    def simulate_compounding_backtest(
        self,
        trades_df: pd.DataFrame,
        initial_capital: float = 100000.0,
        alloc_pct: float = 0.35,
        max_lots: int = 10,
        slippage_pts: float = 1.0,
        include_charges: bool = True,
        brokerage_per_order: float = 20.0,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Simulates capital-compounding dynamic lot sizing over a historical backtest,
        including Indian NSE options buying charges (Brokerage, STT, Exchange, SEBI,
        Stamp Duty, GST) and round-trip slippage.

        Formula:
            capital_t = initial_capital + cumulative_net_pnl_{t-1}
            est_lot_cost_t = entry_spot_t * 0.01 * units_per_lot
            lots_t = clamp(floor((capital_t * alloc_pct) / est_lot_cost_t), 1, max_lots)
        """
        if trades_df.empty:
            return trades_df, {}

        sim_df = trades_df.copy().reset_index(drop=True)
        running_equity = float(initial_capital)
        peak_equity = float(initial_capital)
        prev_lots = 1

        cap_before_list: list[float] = []
        lots_list: list[int] = []
        qty_list: list[int] = []
        margin_list: list[float] = []
        fixed_gross_list: list[float] = []
        fixed_charges_list: list[float] = []
        fixed_slip_list: list[float] = []
        fixed_net_list: list[float] = []
        dyn_gross_list: list[float] = []
        dyn_charges_list: list[float] = []
        dyn_slip_list: list[float] = []
        pnl_list: list[float] = []
        equity_list: list[float] = []
        action_list: list[str] = []
        events: list[dict[str, Any]] = []

        for idx, row in sim_df.iterrows():
            opt_pts = float(row.get("opt_points", 0.0))
            entry_spot = float(row.get("entry_spot", 25000.0))
            trade_dt = str(row.get("date", ""))

            entry_prem = entry_spot * 0.01
            exit_prem = max(0.05, entry_prem + opt_pts)
            est_lot_cost = entry_prem * self.units_per_lot

            lots = int((running_equity * alloc_pct) // est_lot_cost) if est_lot_cost > 0 else 1
            lots = max(1, min(max_lots, lots))
            qty = lots * self.units_per_lot
            margin_used = round(est_lot_cost * lots, 2)

            if idx == 0:
                action_taken = "INITIAL"
            elif lots > prev_lots:
                action_taken = f"SCALE_UP ({prev_lots}->{lots})"
                events.append({
                    "date": trade_dt,
                    "trade_num": idx + 1,
                    "type": "SCALE_UP",
                    "from_lots": prev_lots,
                    "to_lots": lots,
                    "capital": round(running_equity, 2),
                    "details": f"Capital reached ₹{running_equity:,.2f}. Scaled up from {prev_lots} to {lots} lot(s) ({qty} qty).",
                })
            elif lots < prev_lots:
                action_taken = f"STEP_DOWN ({prev_lots}->{lots})"
                events.append({
                    "date": trade_dt,
                    "trade_num": idx + 1,
                    "type": "STEP_DOWN",
                    "from_lots": prev_lots,
                    "to_lots": lots,
                    "capital": round(running_equity, 2),
                    "details": f"Capital dipped to ₹{running_equity:,.2f}. Stepped down from {prev_lots} to {lots} lot(s) ({qty} qty).",
                })
            else:
                action_taken = "MAINTAIN"

            # Fixed 1-lot gross, charges, slippage, net
            f_gross = round(opt_pts * self.units_per_lot, 2)
            f_chg = round(
                self.calculate_options_charges(
                    entry_prem * self.units_per_lot,
                    exit_prem * self.units_per_lot,
                    brokerage_per_order=brokerage_per_order,
                )["total"],
                2,
            ) if include_charges else 0.0
            f_slip = round(slippage_pts * self.units_per_lot, 2)
            f_net = round(f_gross - f_chg - f_slip, 2)

            # Dynamic lots gross, charges, slippage, net
            d_gross = round(opt_pts * qty, 2)
            d_chg = round(
                self.calculate_options_charges(
                    entry_prem * qty,
                    exit_prem * qty,
                    brokerage_per_order=brokerage_per_order,
                )["total"],
                2,
            ) if include_charges else 0.0
            d_slip = round(slippage_pts * qty, 2)
            d_net = round(d_gross - d_chg - d_slip, 2)

            cap_before_list.append(round(running_equity, 2))
            running_equity = round(running_equity + d_net, 2)
            peak_equity = max(peak_equity, running_equity)

            lots_list.append(lots)
            qty_list.append(qty)
            margin_list.append(margin_used)
            fixed_gross_list.append(f_gross)
            fixed_charges_list.append(f_chg)
            fixed_slip_list.append(f_slip)
            fixed_net_list.append(f_net)
            dyn_gross_list.append(d_gross)
            dyn_charges_list.append(d_chg)
            dyn_slip_list.append(d_slip)
            pnl_list.append(d_net)
            equity_list.append(running_equity)
            action_list.append(action_taken)
            prev_lots = lots

        sim_df["gross_pnl"] = fixed_gross_list
        sim_df["fixed_charges"] = fixed_charges_list
        sim_df["fixed_slippage"] = fixed_slip_list
        sim_df["pnl"] = fixed_net_list

        sim_df["capital_before"] = cap_before_list
        sim_df["dynamic_lots"] = lots_list
        sim_df["dynamic_qty"] = qty_list
        sim_df["dynamic_margin"] = margin_list
        sim_df["dynamic_gross_pnl"] = dyn_gross_list
        sim_df["dynamic_charges"] = dyn_charges_list
        sim_df["dynamic_slippage"] = dyn_slip_list
        sim_df["dynamic_pnl"] = pnl_list
        sim_df["dynamic_equity"] = equity_list
        sim_df["dynamic_action"] = action_list

        sim_df["dynamic_peak"] = sim_df["dynamic_equity"].cummax()
        sim_df["dynamic_dd_inr"] = sim_df["dynamic_equity"] - sim_df["dynamic_peak"]
        sim_df["dynamic_dd_pct"] = (sim_df["dynamic_dd_inr"] / sim_df["dynamic_peak"]) * 100

        fixed_pnl = float(sim_df["pnl"].sum())
        dynamic_pnl = float(sim_df["dynamic_pnl"].sum())
        fixed_dd = float(abs((sim_df["pnl"].cumsum() - sim_df["pnl"].cumsum().cummax()).min()))
        dynamic_dd = float(abs(sim_df["dynamic_dd_inr"].min()))
        dynamic_max_dd_pct = float(abs(sim_df["dynamic_dd_pct"].min()))

        metrics = {
            "initial_capital": float(initial_capital),
            "alloc_pct": alloc_pct,
            "slippage_pts": slippage_pts,
            "include_charges": include_charges,
            "final_fixed_equity": float(initial_capital) + fixed_pnl,
            "final_dynamic_equity": float(initial_capital) + dynamic_pnl,
            "fixed_gross_pnl": float(sim_df["gross_pnl"].sum()),
            "fixed_charges": float(sim_df["fixed_charges"].sum()),
            "fixed_slippage": float(sim_df["fixed_slippage"].sum()),
            "fixed_net_pnl": fixed_pnl,
            "dynamic_gross_pnl": float(sim_df["dynamic_gross_pnl"].sum()),
            "dynamic_charges": float(sim_df["dynamic_charges"].sum()),
            "dynamic_slippage": float(sim_df["dynamic_slippage"].sum()),
            "dynamic_net_pnl": dynamic_pnl,
            "pnl_uplift_pct": round(((dynamic_pnl - fixed_pnl) / fixed_pnl) * 100, 1) if fixed_pnl > 0 else 0.0,
            "fixed_max_dd_inr": fixed_dd,
            "dynamic_max_dd_inr": dynamic_dd,
            "dynamic_max_dd_pct": dynamic_max_dd_pct,
            "max_lots_reached": int(sim_df["dynamic_lots"].max()),
            "avg_lots": round(float(sim_df["dynamic_lots"].mean()), 2),
            "scale_events": events,
        }

        return sim_df, metrics

    @staticmethod
    def calculate_options_charges(
        buy_value: float,
        sell_value: float,
        brokerage_per_order: float = 20.0,
        stt_rate: float = 0.001,
        exchange_rate: float = 0.0003503,
        sebi_rate: float = 0.000001,
        stamp_rate: float = 0.00003,
        gst_rate: float = 0.18,
    ) -> dict[str, float]:
        """
        Computes NSE equity options buying round-trip statutory & brokerage charges.
        Defaults match Zerodha/NSE rates:
          - Brokerage: ₹20/order (₹40 round-trip)
          - STT: 0.1% on sell value
          - Exchange txn: 0.03503% on turnover
          - SEBI: 0.0001% (₹10/crore) on turnover
          - Stamp duty: 0.003% on buy value
          - GST: 18% on (Brokerage + Exchange + SEBI)
        """
        turnover = max(0.0, buy_value) + max(0.0, sell_value)
        brokerage = 2.0 * brokerage_per_order
        stt = max(0.0, sell_value) * stt_rate
        exchange = turnover * exchange_rate
        sebi = turnover * sebi_rate
        stamp = max(0.0, buy_value) * stamp_rate
        gst = (brokerage + exchange + sebi) * gst_rate
        total = brokerage + stt + exchange + sebi + stamp + gst
        return {
            "brokerage": brokerage,
            "stt": stt,
            "exchange": exchange,
            "sebi": sebi,
            "stamp": stamp,
            "gst": gst,
            "total": total,
        }

    # ------------------------------------------------------------------
    # Persistence Helpers (Local CSV Ledger)
    # ------------------------------------------------------------------

    @staticmethod
    def save_live_trade(
        trade: dict[str, Any],
        filepath: str = DEFAULT_LIVE_TRADES_CSV,
    ) -> None:
        """
        Appends a closed trade to the local CSV ledger.
        Creates file and header if not present.
        """
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0

        fieldnames = [
            "trade_id", "date", "entry_time", "exit_time", "symbol", "option_type",
            "strike", "spot_price", "lot_size", "entry_price", "exit_price",
            "exit_reason", "pnl", "spot_points", "opt_points", "live_trade",
        ]

        # Extract only matching fields
        row = {k: trade.get(k, "") for k in fieldnames}

        try:
            with open(filepath, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)
            logger.info("Trade %s appended to local ledger: %s", trade.get("trade_id"), filepath)
        except Exception as exc:
            logger.error("Failed to append trade to local ledger: %s", exc)

    @staticmethod
    def load_live_trades(filepath: str = DEFAULT_LIVE_TRADES_CSV) -> list[dict[str, Any]]:
        """
        Loads closed trades from the local CSV ledger.
        """
        if not os.path.exists(filepath):
            return []

        try:
            df = pd.read_csv(filepath)
            return df.to_dict(orient="records")
        except Exception as exc:
            logger.warning("Could not read local trade ledger from %s: %s", filepath, exc)
            return []

