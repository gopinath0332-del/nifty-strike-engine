"""
scripts/compare_timeframes.py
=============================
Backtests NIFTY_OPTIONS_BUY_V9 on both 3-Minute and 5-Minute timeframes
and produces a comprehensive side-by-side comparative analysis.
"""

from __future__ import annotations

import os
import sys
from datetime import date, time

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


def run_backtest_for_timeframe(data_path: str, interval_mins: int) -> pd.DataFrame:
    df = pd.read_csv(data_path)
    df["datetime"] = pd.to_datetime(df["date"])
    df["date"] = df["datetime"].dt.date

    cfg = get_settings()
    df = apply_all(
        df,
        st_atr_period=cfg.st_atr_period,
        st_factor=cfg.st_factor,
        st_buffer_points=cfg.st_buffer_points,
        rsi_length=cfg.rsi_length,
        bb_length=cfg.bb_length,
        bb_mult=cfg.bb_mult,
    )

    eval_df = df[df["date"] >= date(2026, 1, 1)].copy().reset_index(drop=True)
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

        if (signal.exit_ce or signal.exit_pe) and current_trade is not None:
            exit_price = row["close"]
            spot_change = exit_price - current_trade["entry_spot"]
            if current_trade["option_type"] == "PE":
                spot_change = -spot_change

            delta = 0.50
            opt_pts = round(spot_change * delta, 2)
            pnl = round(opt_pts * cfg.lot_size, 2)

            current_trade["exit_time"] = row["datetime"]
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

        elif signal.ce_entry and current_trade is None:
            symbol = select_strike(row["close"], "CE")
            current_trade = {
                "date": row["date"],
                "option_type": "CE",
                "symbol": symbol,
                "entry_time": row["datetime"],
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
                "entry_time": row["datetime"],
                "entry_spot": row["close"],
                "entry_bar_idx": idx,
                "rsi": round(row["rsi"], 1),
                "s1": round(row["s1"], 1),
                "lower_bb": round(row["bb_lower"], 1),
            }
            engine.on_entry_filled("PE")

    tdf = pd.DataFrame(trades)
    tdf["cum_pnl"] = tdf["pnl"].cumsum()
    tdf["peak"] = tdf["cum_pnl"].cummax()
    tdf["drawdown"] = tdf["cum_pnl"] - tdf["peak"]
    return tdf


def compute_metrics(df: pd.DataFrame, interval_mins: int) -> dict:
    total = len(df)
    wins = len(df[df["pnl"] > 0])
    losses = len(df[df["pnl"] <= 0])
    win_rate = (wins / total * 100) if total else 0

    net_pnl = df["pnl"].sum()
    gross_profit = df[df["pnl"] > 0]["pnl"].sum()
    gross_loss = abs(df[df["pnl"] < 0]["pnl"].sum())
    profit_factor = (gross_profit / gross_loss) if gross_loss else 0

    ce_df = df[df["option_type"] == "CE"]
    pe_df = df[df["option_type"] == "PE"]

    ce_wins = len(ce_df[ce_df["pnl"] > 0])
    ce_wr = (ce_wins / len(ce_df) * 100) if len(ce_df) else 0
    ce_pnl = ce_df["pnl"].sum()

    pe_wins = len(pe_df[pe_df["pnl"] > 0])
    pe_wr = (pe_wins / len(pe_df) * 100) if len(pe_df) else 0
    pe_pnl = pe_df["pnl"].sum()

    avg_win = df[df["pnl"] > 0]["pnl"].mean() if wins else 0
    avg_loss = abs(df[df["pnl"] < 0]["pnl"].mean()) if losses else 0
    rr_ratio = (avg_win / avg_loss) if avg_loss else 0

    max_win = df["pnl"].max()
    max_loss = df["pnl"].min()
    max_dd = abs(df["drawdown"].min())

    avg_dur = df["holding_mins"].mean()
    win_dur = df[df["pnl"] > 0]["holding_mins"].mean()
    loss_dur = df[df["pnl"] < 0]["holding_mins"].mean()

    # Sharpe & Sortino (Daily)
    daily = df.groupby("date")["pnl"].sum()
    sharpe = (daily.mean() / daily.std()) * np.sqrt(252) if daily.std() > 0 else 0
    downside = daily[daily < 0]
    sortino = (daily.mean() / downside.std()) * np.sqrt(252) if (not downside.empty and downside.std() > 0) else 0

    # Exit reason stats
    eod_trades = df[df["exit_reason"].str.contains("EOD", na=False)]
    st_trades = df[df["exit_reason"].str.contains("ST Flip", na=False)]
    eod_wr = (len(eod_trades[eod_trades["pnl"] > 0]) / len(eod_trades) * 100) if len(eod_trades) else 0
    st_wr = (len(st_trades[st_trades["pnl"] > 0]) / len(st_trades) * 100) if len(st_trades) else 0

    return {
        "interval": f"{interval_mins}-min",
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "profit_factor": profit_factor,
        "ce_trades": len(ce_df),
        "ce_wr": ce_wr,
        "ce_pnl": ce_pnl,
        "pe_trades": len(pe_df),
        "pe_wr": pe_wr,
        "pe_pnl": pe_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr_ratio": rr_ratio,
        "max_win": max_win,
        "max_loss": max_loss,
        "max_drawdown": max_dd,
        "avg_duration": avg_dur,
        "win_duration": win_dur,
        "loss_duration": loss_dur,
        "sharpe": sharpe,
        "sortino": sortino,
        "eod_trades": len(eod_trades),
        "eod_wr": eod_wr,
        "eod_pnl": eod_trades["pnl"].sum(),
        "st_trades": len(st_trades),
        "st_wr": st_wr,
        "st_pnl": st_trades["pnl"].sum(),
    }


def main():
    p3 = os.path.join(os.path.dirname(__file__), "..", "data", "nifty_3min_2026.csv")
    p5 = os.path.join(os.path.dirname(__file__), "..", "data", "nifty_5minute_2026.csv")

    print("Running 3-minute backtest...")
    df3 = run_backtest_for_timeframe(p3, 3)
    df3.to_csv(os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_3min_2026.csv"), index=False)

    print("Running 5-minute backtest...")
    df5 = run_backtest_for_timeframe(p5, 5)
    df5.to_csv(os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_5min_2026.csv"), index=False)

    m3 = compute_metrics(df3, 3)
    m5 = compute_metrics(df5, 5)

    print("\n" + "=" * 75)
    print("      NIFTY_OPTIONS_BUY_V9: TIMEFRAME COMPARISON (2026-01-01 to 2026-11-06)")
    print("=" * 75)
    print(f"{'Metric':<28} | {'3-Minute':<20} | {'5-Minute':<20} | {'Difference'}")
    print("-" * 75)

    comparisons = [
        ("Net P&L (₹)", f"₹{m3['net_pnl']:+,.2f}", f"₹{m5['net_pnl']:+,.2f}", f"₹{m5['net_pnl'] - m3['net_pnl']:+,.2f}"),
        ("Overall Win Rate", f"{m3['win_rate']:.1f}% ({m3['wins']}W/{m3['losses']}L)", f"{m5['win_rate']:.1f}% ({m5['wins']}W/{m5['losses']}L)", f"{m5['win_rate'] - m3['win_rate']:+.1f}%"),
        ("Profit Factor", f"{m3['profit_factor']:.2f}", f"{m5['profit_factor']:.2f}", f"{m5['profit_factor'] - m3['profit_factor']:+.2f}"),
        ("CE Trades Win Rate / P&L", f"{m3['ce_wr']:.1f}% (₹{m3['ce_pnl']:+,.0f})", f"{m5['ce_wr']:.1f}% (₹{m5['ce_pnl']:+,.0f})", f"{m5['ce_pnl'] - m3['ce_pnl']:+,.0f}"),
        ("PE Trades Win Rate / P&L", f"{m3['pe_wr']:.1f}% (₹{m3['pe_pnl']:+,.0f})", f"{m5['pe_wr']:.1f}% (₹{m5['pe_pnl']:+,.0f})", f"{m5['pe_pnl'] - m3['pe_pnl']:+,.0f}"),
        ("Risk-Reward Ratio (Avg W/L)", f"{m3['rr_ratio']:.2f} (₹{m3['avg_win']:,.0f} / ₹{m3['avg_loss']:,.0f})", f"{m5['rr_ratio']:.2f} (₹{m5['avg_win']:,.0f} / ₹{m5['avg_loss']:,.0f})", f"{m5['rr_ratio'] - m3['rr_ratio']:+.2f}"),
        ("Max Win / Max Loss", f"₹{m3['max_win']:+,.0f} / ₹{m3['max_loss']:+,.0f}", f"₹{m5['max_win']:+,.0f} / ₹{m5['max_loss']:+,.0f}", ""),
        ("Max Drawdown (₹)", f"₹{m3['max_drawdown']:,.2f}", f"₹{m5['max_drawdown']:,.2f}", f"₹{m5['max_drawdown'] - m3['max_drawdown']:+,.2f}"),
        ("Daily Sharpe / Sortino", f"{m3['sharpe']:.2f} / {m3['sortino']:.2f}", f"{m5['sharpe']:.2f} / {m5['sortino']:.2f}", f"{m5['sharpe'] - m3['sharpe']:+.2f} / {m5['sortino'] - m3['sortino']:+.2f}"),
        ("Avg Holding Duration", f"{m3['avg_duration']:.0f} mins (~{m3['avg_duration']/60:.1f}h)", f"{m5['avg_duration']:.0f} mins (~{m5['avg_duration']/60:.1f}h)", f"{m5['avg_duration'] - m3['avg_duration']:+.0f} mins"),
        ("15:00 EOD Exits", f"{m3['eod_trades']}T ({m3['eod_wr']:.1f}%W, ₹{m3['eod_pnl']:+,.0f})", f"{m5['eod_trades']}T ({m5['eod_wr']:.1f}%W, ₹{m5['eod_pnl']:+,.0f})", ""),
        ("ST Flip Exits", f"{m3['st_trades']}T ({m3['st_wr']:.1f}%W, ₹{m3['st_pnl']:+,.0f})", f"{m5['st_trades']}T ({m5['st_wr']:.1f}%W, ₹{m5['st_pnl']:+,.0f})", ""),
    ]

    for label, v3, v5, diff in comparisons:
        print(f"{label:<28} | {v3:<20} | {v5:<20} | {diff}")
    print("=" * 75)


if __name__ == "__main__":
    main()

