"""
scripts/run_banknifty_backtest.py
=================================
Runs backtest of the same NIFTY_OPTIONS_BUY_V9 strategy rules on BANKNIFTY
historical candles from 2026-01-01 to 2026-11-06 (matching Nifty period).

Strategy Rules (Exact Same Strategy):
  - Indicators:
      Supertrend (10, 3.0, buffer 0.0)
      RSI (14) -> CE > 70, PE < 30
      Bollinger Bands (20, 2.0) -> CE Close > Upper BB, PE Close < Lower BB
      Traditional Daily Pivots -> CE Close > R1, PE Close < S1
  - Daily limit: Max 1 trade per day (CE or PE, never both)
  - Time filters: Entry before 14:45 IST, skip first candle (09:15-09:20)
  - Exit conditions:
      1. Supertrend Flip: Bear flip exits CE; Bull flip exits PE
      2. EOD Hard Exit: 15:00 IST sharp
  - Bank Nifty Specifics:
      Lot size: 30 units (NSE current standard)
      Strike step: 100 points
      Delta: 0.50 ATM/Near-OTM estimation
      Trade P&L: opt_points * 30
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, time

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import get_settings
from src.indicators import apply_all
from src.position_sizer import PositionSizer
from src.signal_engine import SignalEngine
from src.strike_selector import nearest_strike, get_expiry


BANKNIFTY_LOT_SIZE = 30
BANKNIFTY_STRIKE_STEP = 100


def select_banknifty_strike_fallback(spot: float, option_type: str) -> str:
    """Analytical fallback strike for Bank Nifty (step=100)."""
    option_type = option_type.upper()
    atm = nearest_strike(spot, step=BANKNIFTY_STRIKE_STEP)
    offset = nearest_strike(spot * 0.01, step=BANKNIFTY_STRIKE_STEP)
    strike = (atm + offset) if option_type == "CE" else (atm - offset)
    expiry = get_expiry(roll_if_today=True)
    return f"BANKNIFTY{expiry}{strike}{option_type}"


def run_bn_backtest(interval_mins: int = 5, save_csv: bool = True):
    cfg = get_settings()
    interval_str = f"{interval_mins}minute" if interval_mins != 3 else "3min"
    data_file = os.path.join(os.path.dirname(__file__), "..", "data", f"banknifty_{interval_str}_2026.csv")

    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found at {data_file}")

    print(f"Loading BANKNIFTY {interval_mins}-minute candle data from {data_file}...")
    df = pd.read_csv(data_file)
    df["datetime"] = pd.to_datetime(df["date"])
    df["date"] = df["datetime"].dt.date

    print(f"Calculating technical indicators (Supertrend 10/3, RSI 14, BB 20/2, Daily Pivots)...")
    df = apply_all(
        df,
        st_atr_period=cfg.st_atr_period,
        st_factor=cfg.st_factor,
        st_buffer_points=cfg.st_buffer_points,
        rsi_length=cfg.rsi_length,
        bb_length=cfg.bb_length,
        bb_mult=cfg.bb_mult,
    )

    backtest_start = date(2026, 1, 1)
    eval_df = df[df["date"] >= backtest_start].copy().reset_index(drop=True)
    print(f"Backtesting from {eval_df['date'].min()} to {eval_df['date'].max()} ({len(eval_df)} candles)...")

    engine = SignalEngine(
        entry_cutoff=time(*cfg.entry_cutoff_hhmm),
        eod_exit=time(*cfg.eod_exit_hhmm),
        skip_first_candle=getattr(cfg, "skip_first_candle", True),
    )

    trades = []
    current_trade = None

    class MockCandle:
        def __init__(self, row):
            self.open = row["open"]
            self.high = row["high"]
            self.low = row["low"]
            self.close = row["close"]
            self.volume = row["volume"]
            self.timestamp = row["datetime"].replace(tzinfo=IST)

    for idx, row in eval_df.iterrows():
        candle = MockCandle(row)
        candle_dt = row["datetime"]
        indicators = {
            "bull_trend": bool(row["bull_trend"]),
            "bear_trend": bool(row["bear_trend"]),
            "rsi": float(row["rsi"]) if pd.notna(row["rsi"]) else None,
            "upper_bb": float(row["bb_upper"]) if pd.notna(row["bb_upper"]) else None,
            "lower_bb": float(row["bb_lower"]) if pd.notna(row["bb_lower"]) else None,
            "r1": float(row["r1"]) if pd.notna(row["r1"]) else None,
            "s1": float(row["s1"]) if pd.notna(row["s1"]) else None,
        }

        signal = engine.evaluate(candle, indicators)

        # Handle Exit
        if (signal.exit_ce or signal.exit_pe) and current_trade is not None:
            exit_price = row["close"]
            spot_change = exit_price - current_trade["entry_spot"]
            if current_trade["option_type"] == "PE":
                spot_change = -spot_change

            delta = 0.50
            opt_pts = round(spot_change * delta, 2)
            pnl = round(opt_pts * BANKNIFTY_LOT_SIZE, 2)

            current_trade["exit_time"] = candle_dt
            current_trade["exit_spot"] = exit_price
            current_trade["exit_reason"] = signal.exit_reason
            current_trade["spot_points"] = round(spot_change, 2)
            current_trade["opt_points"] = opt_pts
            current_trade["pnl"] = pnl
            current_trade["holding_bars"] = idx - current_trade["entry_bar_idx"]
            current_trade["holding_mins"] = current_trade["holding_bars"] * interval_mins

            trades.append(current_trade)
            engine.on_exit_filled()
            current_trade = None

        # Handle Entry
        elif signal.ce_entry and current_trade is None:
            symbol = select_banknifty_strike_fallback(row["close"], "CE")
            current_trade = {
                "date": row["date"],
                "option_type": "CE",
                "symbol": symbol,
                "entry_time": candle_dt,
                "entry_spot": row["close"],
                "entry_bar_idx": idx,
                "rsi": round(row["rsi"], 1),
                "r1": round(row["r1"], 1),
                "upper_bb": round(row["bb_upper"], 1),
            }
            engine.on_entry_filled("CE")

        elif signal.pe_entry and current_trade is None:
            symbol = select_banknifty_strike_fallback(row["close"], "PE")
            current_trade = {
                "date": row["date"],
                "option_type": "PE",
                "symbol": symbol,
                "entry_time": candle_dt,
                "entry_spot": row["close"],
                "entry_bar_idx": idx,
                "rsi": round(row["rsi"], 1),
                "s1": round(row["s1"], 1),
                "lower_bb": round(row["bb_lower"], 1),
            }
            engine.on_entry_filled("PE")

    trades_df = pd.DataFrame(trades)

    print("\n" + "=" * 70)
    print(f"  BANKNIFTY BACKTEST RESULTS: ({interval_mins}-min)")
    print(f"  Period: {eval_df['date'].min()} to {eval_df['date'].max()} | Lot Size: {BANKNIFTY_LOT_SIZE}")
    print("=" * 70)

    if trades_df.empty:
        print("No trades generated during this period.")
        return trades_df, {}

    total_trades = len(trades_df)
    ce_trades = len(trades_df[trades_df["option_type"] == "CE"])
    pe_trades = len(trades_df[trades_df["option_type"] == "PE"])
    winning_trades = len(trades_df[trades_df["pnl"] > 0])
    losing_trades = len(trades_df[trades_df["pnl"] <= 0])
    win_rate = (winning_trades / total_trades) * 100 if total_trades else 0

    total_pnl = trades_df["pnl"].sum()
    gross_profit = trades_df[trades_df["pnl"] > 0]["pnl"].sum()
    gross_loss = abs(trades_df[trades_df["pnl"] < 0]["pnl"].sum())
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else np.nan

    avg_trade_pnl = trades_df["pnl"].mean()
    max_win = trades_df["pnl"].max()
    max_loss = trades_df["pnl"].min()

    # Drawdown
    trades_df["cum_pnl"] = trades_df["pnl"].cumsum()
    trades_df["peak"] = trades_df["cum_pnl"].cummax()
    trades_df["drawdown"] = trades_df["cum_pnl"] - trades_df["peak"]
    max_drawdown = abs(trades_df["drawdown"].min())

    # Duration
    avg_dur = trades_df["holding_mins"].mean()
    win_dur = trades_df[trades_df["pnl"] > 0]["holding_mins"].mean()
    loss_dur = trades_df[trades_df["pnl"] < 0]["holding_mins"].mean()

    # Sharpe & Sortino (Daily)
    daily = trades_df.groupby("date")["pnl"].sum()
    sharpe = (daily.mean() / daily.std()) * np.sqrt(252) if daily.std() > 0 else 0
    downside = daily[daily < 0]
    sortino = (daily.mean() / downside.std()) * np.sqrt(252) if (not downside.empty and downside.std() > 0) else 0

    # Risk-Reward
    avg_win = trades_df[trades_df["pnl"] > 0]["pnl"].mean() if winning_trades else 0
    avg_loss = abs(trades_df[trades_df["pnl"] < 0]["pnl"].mean()) if losing_trades else 0
    rr_ratio = (avg_win / avg_loss) if avg_loss else 0

    exit_reasons = trades_df["exit_reason"].value_counts().to_dict()

    print(f"Total Trades         : {total_trades} (CE: {ce_trades}, PE: {pe_trades})")
    print(f"Winning Trades       : {winning_trades} ({win_rate:.1f}%)")
    print(f"Losing Trades        : {losing_trades} ({100 - win_rate:.1f}%)")
    print(f"Profit Factor        : {profit_factor}")
    print(f"Net P&L              : ₹{total_pnl:+,.2f}")
    print(f"Avg Trade P&L        : ₹{avg_trade_pnl:+,.2f}")
    print(f"Max Win (Single)     : ₹{max_win:+,.2f}")
    print(f"Max Loss (Single)    : ₹{max_loss:+,.2f}")
    print(f"Max Drawdown         : ₹{max_drawdown:,.2f}")
    print(f"Risk-Reward Ratio    : {rr_ratio:.2f} (Avg Win: ₹{avg_win:,.0f} / Avg Loss: ₹{avg_loss:,.0f})")
    print(f"Daily Sharpe/Sortino : {sharpe:.2f} / {sortino:.2f}")
    print(f"Exit Reasons         : {exit_reasons}")
    print("=" * 70)

    # Dynamic Position Sizing
    sizer = PositionSizer(
        initial_capital=cfg.sizing_initial_capital,
        base_lots=cfg.sizing_base_lots,
        max_lots=cfg.sizing_max_lots,
        profit_step_per_lot=cfg.sizing_profit_step_per_lot,
        min_trades_per_scale=cfg.sizing_min_trades_per_scale,
        step_down_dd_pct=cfg.sizing_step_down_dd_pct,
        units_per_lot=BANKNIFTY_LOT_SIZE,
    )
    sim_df, sz_metrics = sizer.simulate_backtest(trades_df)

    print("\n" + "=" * 70)
    print("  DYNAMIC POSITION SIZING SIMULATION (Fixed 1-Lot vs. Dynamic Scaling)")
    print("=" * 70)
    print(f"Starting Capital         : ₹{sz_metrics['initial_capital']:,.2f}")
    print(f"Fixed 1-Lot Net P&L      : ₹{sz_metrics['fixed_net_pnl']:+,.2f} (Final Equity: ₹{sz_metrics['final_fixed_equity']:,.2f})")
    print(f"Dynamic Scaled Net P&L   : ₹{sz_metrics['dynamic_net_pnl']:+,.2f} (Final Equity: ₹{sz_metrics['final_dynamic_equity']:,.2f})")
    print(f"Net Profit Uplift        : {sz_metrics['pnl_uplift_pct']:+.1f}%")
    print(f"Fixed Max Drawdown (₹)   : ₹{sz_metrics['fixed_max_dd_inr']:,.2f}")
    print(f"Dynamic Max Drawdown (₹) : ₹{sz_metrics['dynamic_max_dd_inr']:,.2f} ({sz_metrics['dynamic_max_dd_pct']:.2f}% of peak equity)")
    print(f"Max Lots Reached         : {sz_metrics['max_lots_reached']} lot(s) ({sz_metrics['max_lots_reached'] * BANKNIFTY_LOT_SIZE} units)")

    scale_events = sz_metrics.get("scale_events", [])
    if scale_events:
        print("\n  Scaling Timeline Events:")
        for ev in scale_events:
            print(f"    • [{ev['date']}] Trade #{ev['trade_num']:03d} | {ev['type']} -> {ev['details']}")
    else:
        print("\n  No scaling events triggered.")

    if save_csv:
        csv_path = os.path.join(os.path.dirname(__file__), "..", "data", f"banknifty_trades_{interval_mins}min_2026.csv")
        sim_df.to_csv(csv_path, index=False)
        print(f"\nSaved trade ledger to: {csv_path}")

    metrics = {
        "interval": f"{interval_mins}-min",
        "total_trades": total_trades,
        "ce_trades": ce_trades,
        "pe_trades": pe_trades,
        "wins": winning_trades,
        "losses": losing_trades,
        "win_rate": win_rate,
        "net_pnl": total_pnl,
        "profit_factor": profit_factor,
        "avg_trade_pnl": avg_trade_pnl,
        "max_win": max_win,
        "max_loss": max_loss,
        "max_drawdown": max_drawdown,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr_ratio": rr_ratio,
        "sharpe": sharpe,
        "sortino": sortino,
        "avg_duration": avg_dur,
        "win_duration": win_dur,
        "loss_duration": loss_dur,
        "exit_reasons": exit_reasons,
        "dynamic_net_pnl": sz_metrics['dynamic_net_pnl'],
        "dynamic_max_dd_inr": sz_metrics['dynamic_max_dd_inr'],
        "dynamic_max_dd_pct": sz_metrics['dynamic_max_dd_pct'],
        "max_lots_reached": sz_metrics['max_lots_reached'],
    }
    return sim_df, metrics


if __name__ == "__main__":
    print("=== RUNNING BANKNIFTY 5-MINUTE BACKTEST ===")
    df5, m5 = run_bn_backtest(5)

    print("\n=== RUNNING BANKNIFTY 3-MINUTE BACKTEST ===")
    df3, m3 = run_bn_backtest(3)
