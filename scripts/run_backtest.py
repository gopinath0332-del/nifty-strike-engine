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
from src.signal_engine import SignalEngine
from src.strike_selector import select_strike

def run_backtest():
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

    # Save detailed trade ledger to CSV (graceful fallback if Excel has the file open)
    ledger_path = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_2026.csv")
    saved_csv = ledger_path
    try:
        trades_df.to_csv(ledger_path, index=False)
        print(f"Detailed trade ledger saved to: {ledger_path}")
    except PermissionError:
        alt_path = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_latest.csv")
        trades_df.to_csv(alt_path, index=False)
        saved_csv = alt_path
        print(f"[Notice] backtest_trades_2026.csv is open in Excel; saved to: {alt_path}")

    # Generate HTML report automatically
    from scripts.generate_html_report import generate_report
    generate_report(csv_path=saved_csv)

    # Display monthly breakdown
    trades_df["month"] = pd.to_datetime(trades_df["date"]).dt.strftime("%Y-%m")
    monthly = trades_df.groupby("month").agg(
        trades=("pnl", "count"),
        win_rate=("pnl", lambda x: f"{(x > 0).mean() * 100:.1f}%"),
        net_pnl=("pnl", "sum"),
    )
    print("\nMonthly Performance Breakdown:")
    print(monthly.to_string())


if __name__ == "__main__":
    run_backtest()

