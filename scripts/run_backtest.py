"""
scripts/run_backtest.py
=======================
Runs a comprehensive backtest of NIFTY_OPTIONS_BUY_V9 strategy on NIFTY 50 3-min
historical candles from 2026-01-01 to 2026-11-06.

Strategy Rules:
  - Timeframe: 3-minute candles
  - Entry conditions:
      CE: Bull Trend (Supertrend 10, 3) AND RSI > 70 AND Close > R1 AND Close > Upper BB
      PE: Bear Trend (Supertrend 10, 3) AND RSI < 30 AND Close < S1 AND Close < Lower BB
  - Daily limit: Max 1 trade per day (CE or PE)
  - Time filters: Entry allowed before 14:45 IST
  - Exit conditions:
      1. Supertrend Flip: Bear flip exits CE; Bull flip exits PE
      2. EOD Hard Exit: 15:00 IST sharp
  - Lot size: 65 units (1 contract)
  - Options Delta / Premium estimation:
      Underlying point change * Delta (0.50 ATM/Near-OTM delta)
      or option points * 65 for P&L.
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
from src.strike_selector import select_strike

def run_backtest(
    compounding: bool = True,
    initial_capital: float = 100000.0,
    alloc_pct: float = 0.35,
    max_lots: int = 10,
    slippage_pts: float = 1.0,
    include_charges: bool = True,
):
    cfg = get_settings()
    interval_str = f"{cfg.candle_interval_minutes}minute" if cfg.candle_interval_minutes != 3 else "3min"
    data_file = os.path.join(os.path.dirname(__file__), "..", "data", f"nifty_{interval_str}_2026.csv")

    if not os.path.exists(data_file):
        # Fallback to alternate naming convention
        alt_file = os.path.join(os.path.dirname(__file__), "..", "data", f"nifty_{cfg.candle_interval_minutes}min_2026.csv")
        data_file = alt_file if os.path.exists(alt_file) else data_file

    if not os.path.exists(data_file):
        print(f"Data file not found at {data_file}. Please run scripts/fetch_historical.py first.")
        return

    print(f"Loading historical {cfg.candle_interval_minutes}-minute candle data from {data_file}...")
    df = pd.read_csv(data_file)
    df["datetime"] = pd.to_datetime(df["date"])
    df["date"] = df["datetime"].dt.date

    print("Calculating technical indicators (Supertrend, RSI, Bollinger Bands, Traditional Pivots)...")
    df = apply_all(
        df,
        st_atr_period=cfg.st_atr_period,
        st_factor=cfg.st_factor,
        st_buffer_points=cfg.st_buffer_points,
        rsi_length=cfg.rsi_length,
        bb_length=cfg.bb_length,
        bb_mult=cfg.bb_mult,
    )

    # Filter evaluation starting from 2026-01-01 (earlier dates served as indicator warmup & pivot reference)
    backtest_start = date(2026, 1, 1)
    eval_df = df[df["date"] >= backtest_start].copy().reset_index(drop=True)

    print(f"Backtesting from {eval_df['date'].min()} to {eval_df['date'].max()} ({len(eval_df)} candles)...")

    engine = SignalEngine(
        entry_cutoff=time(*cfg.entry_cutoff_hhmm),
        eod_exit=time(*cfg.eod_exit_hhmm),
        skip_first_candle=getattr(cfg, "skip_first_candle", False),
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

            # Approximate option premium points using delta ~0.50 for near-the-money option
            delta = 0.50
            opt_pts = round(spot_change * delta, 2)
            pnl = round(opt_pts * cfg.lot_size, 2)

            current_trade["exit_time"] = candle_dt
            current_trade["exit_spot"] = exit_price
            current_trade["exit_reason"] = signal.exit_reason
            current_trade["spot_points"] = round(spot_change, 2)
            current_trade["opt_points"] = opt_pts
            current_trade["pnl"] = pnl
            current_trade["holding_bars"] = idx - current_trade["entry_bar_idx"]

            trades.append(current_trade)
            engine.on_exit_filled()
            current_trade = None

        # Handle Entry
        elif signal.ce_entry and current_trade is None:
            symbol = select_strike(row["close"], "CE")
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
            symbol = select_strike(row["close"], "PE")
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

    # Summary Statistics
    print("\n" + "=" * 70)
    print(f"  BACKTEST RESULTS: {cfg.strategy_name}")
    print(f"  Period: {eval_df['date'].min()} to {eval_df['date'].max()}")
    print("=" * 70)

    if trades_df.empty:
        print("No trades generated during this period.")
        return

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
    print(f"Exit Reasons         : {exit_reasons}")
    print("=" * 70)

    # Dynamic Position Sizing Simulation
    print("\n" + "=" * 70)
    if compounding:
        print(f"  CAPITAL-COMPOUNDING DYNAMIC LOT SIMULATION (Initial: ₹{initial_capital:,.0f}, Alloc: {alloc_pct*100:.0f}%)")
    else:
        print("  DYNAMIC POSITION SIZING SIMULATION (Fixed 1-Lot vs. Dynamic Scaling)")
    print("=" * 70)
    sizer = PositionSizer(
        initial_capital=initial_capital if compounding else cfg.sizing_initial_capital,
        base_lots=cfg.sizing_base_lots,
        max_lots=max_lots if compounding else cfg.sizing_max_lots,
        profit_step_per_lot=cfg.sizing_profit_step_per_lot,
        min_trades_per_scale=cfg.sizing_min_trades_per_scale,
        step_down_dd_pct=cfg.sizing_step_down_dd_pct,
        units_per_lot=cfg.lot_size,
    )
    if compounding:
        sim_df, sz_metrics = sizer.simulate_compounding_backtest(
            trades_df,
            initial_capital=initial_capital,
            alloc_pct=alloc_pct,
            max_lots=max_lots,
            slippage_pts=slippage_pts,
            include_charges=include_charges,
        )
    else:
        sim_df, sz_metrics = sizer.simulate_backtest(trades_df)

    print(f"Starting Capital         : ₹{sz_metrics['initial_capital']:,.2f}")
    if compounding:
        print(f"Fixed 1-Lot Gross P&L    : ₹{sz_metrics['fixed_gross_pnl']:+,.2f} (Charges: -₹{sz_metrics['fixed_charges']:,.2f}, Slip: -₹{sz_metrics['fixed_slippage']:,.2f})")
    print(f"Fixed 1-Lot Net P&L      : ₹{sz_metrics['fixed_net_pnl']:+,.2f} (Final Equity: ₹{sz_metrics['final_fixed_equity']:,.2f})")
    if compounding:
        print(f"Dynamic Gross P&L        : ₹{sz_metrics['dynamic_gross_pnl']:+,.2f} (Charges: -₹{sz_metrics['dynamic_charges']:,.2f}, Slip: -₹{sz_metrics['dynamic_slippage']:,.2f})")
    print(f"Dynamic Scaled Net P&L   : ₹{sz_metrics['dynamic_net_pnl']:+,.2f} (Final Equity: ₹{sz_metrics['final_dynamic_equity']:,.2f})")
    print(f"Net Profit Uplift        : {sz_metrics['pnl_uplift_pct']:+.1f}%")
    print(f"Fixed Max Drawdown (₹)   : ₹{sz_metrics['fixed_max_dd_inr']:,.2f}")
    print(f"Dynamic Max Drawdown (₹) : ₹{sz_metrics['dynamic_max_dd_inr']:,.2f} ({sz_metrics['dynamic_max_dd_pct']:.2f}% of peak equity)")
    print(f"Max Lots Reached         : {sz_metrics['max_lots_reached']} lot(s) ({sz_metrics['max_lots_reached'] * cfg.lot_size} units)")

    scale_events = sz_metrics.get("scale_events", [])
    if scale_events:
        print("\n  Scaling Timeline Events:")
        for ev in scale_events:
            print(f"    • [{ev['date']}] Trade #{ev['trade_num']:03d} | {ev['type']} -> {ev['details']}")
    else:
        print("\n  No scaling events triggered (Criteria not reached during period).")

    print("=" * 70)

    # Save detailed trade ledger to CSV (graceful fallback if Excel has the file open)
    ledger_path = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_2026.csv")
    saved_csv = ledger_path
    try:
        sim_df.to_csv(ledger_path, index=False)
        print(f"\nDetailed trade ledger (with dynamic sizing) saved to: {ledger_path}")
    except PermissionError:
        alt_path = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_latest.csv")
        sim_df.to_csv(alt_path, index=False)
        saved_csv = alt_path
        print(f"[Notice] backtest_trades_2026.csv is open in Excel; saved to: {alt_path}")

    # Generate HTML report automatically
    from scripts.generate_html_report import generate_report
    generate_report(csv_path=saved_csv)

    # Display monthly breakdown
    sim_df["month"] = pd.to_datetime(sim_df["date"]).dt.strftime("%Y-%m")
    monthly = sim_df.groupby("month").agg(
        trades=("pnl", "count"),
        win_rate=("pnl", lambda x: f"{(x > 0).mean() * 100:.1f}%"),
        fixed_pnl=("pnl", "sum"),
        dynamic_pnl=("dynamic_pnl", "sum"),
        avg_lots=("dynamic_lots", "mean"),
    )
    print("\nMonthly Performance Breakdown (Fixed vs Dynamic):")
    print(monthly.to_string())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run NIFTY Options Buying V9 Backtest")
    parser.add_argument("--no-compounding", action="store_true", help="Use Fixed-Ratio tier model instead of capital-compounding")
    parser.add_argument("--initial-capital", type=float, default=100000.0, help="Initial capital in INR (default: 100000)")
    parser.add_argument("--alloc-pct", type=float, default=0.35, help="Capital allocation fraction per trade (default: 0.35)")
    parser.add_argument("--max-lots", type=int, default=10, help="Maximum lots cap (default: 10)")
    parser.add_argument("--slippage-pts", type=float, default=1.0, help="Round-trip slippage in option points (default: 1.0)")
    parser.add_argument("--no-charges", action="store_true", help="Exclude brokerage, STT, exchange, SEBI, stamp duty, and GST")
    args = parser.parse_args()
    run_backtest(
        compounding=not args.no_compounding,
        initial_capital=args.initial_capital,
        alloc_pct=args.alloc_pct,
        max_lots=args.max_lots,
        slippage_pts=args.slippage_pts,
        include_charges=not args.no_charges,
    )

