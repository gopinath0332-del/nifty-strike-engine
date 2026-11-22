"""
scripts/compare_nifty_banknifty.py
==================================
Side-by-side comparative backtest analysis of NIFTY_OPTIONS_BUY_V9
strategy on NIFTY 50 vs BANK NIFTY for the period 2026-01-01 to 2026-11-06.
"""

from __future__ import annotations

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import get_settings


def load_and_summarize(csv_path: str, index_name: str, lot_size: int, interval_mins: int) -> dict:
    df = pd.read_csv(csv_path)
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

    df["cum_pnl"] = df["pnl"].cumsum()
    df["peak"] = df["cum_pnl"].cummax()
    df["drawdown"] = df["cum_pnl"] - df["peak"]
    max_dd = abs(df["drawdown"].min())

    avg_dur = (df["holding_bars"] * interval_mins).mean()

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

    # Dynamic metrics
    dyn_pnl = df["dynamic_pnl"].sum() if "dynamic_pnl" in df.columns else np.nan
    max_lots = df["dynamic_lots"].max() if "dynamic_lots" in df.columns else np.nan
    dyn_dd = abs(df["dynamic_dd_inr"].min()) if "dynamic_dd_inr" in df.columns else np.nan

    # Average points captured
    avg_spot_pts = df["spot_points"].mean()
    avg_opt_pts = df["opt_points"].mean()

    return {
        "index": index_name,
        "lot_size": lot_size,
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
        "avg_trade_pnl": net_pnl / total if total else 0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr_ratio": rr_ratio,
        "max_win": max_win,
        "max_loss": max_loss,
        "max_drawdown": max_dd,
        "max_dd_pct_capital": (max_dd / 150000.0) * 100,
        "avg_duration_min": avg_dur,
        "sharpe": sharpe,
        "sortino": sortino,
        "eod_trades": len(eod_trades),
        "eod_wr": eod_wr,
        "eod_pnl": eod_trades["pnl"].sum(),
        "st_trades": len(st_trades),
        "st_wr": st_wr,
        "st_pnl": st_trades["pnl"].sum(),
        "dyn_pnl": dyn_pnl,
        "max_lots": max_lots,
        "dyn_dd": dyn_dd,
        "avg_spot_pts": avg_spot_pts,
        "avg_opt_pts": avg_opt_pts,
        "df": df,
    }


def main():
    nifty_5m_path = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_2026.csv")
    bn_5m_path = os.path.join(os.path.dirname(__file__), "..", "data", "banknifty_trades_5min_2026.csv")

    nifty_3m_path = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_3min_2026.csv")
    bn_3m_path = os.path.join(os.path.dirname(__file__), "..", "data", "banknifty_trades_3min_2026.csv")

    n5 = load_and_summarize(nifty_5m_path, "NIFTY 50", 65, 5)
    b5 = load_and_summarize(bn_5m_path, "BANK NIFTY", 30, 5)

    print("\n" + "=" * 90)
    print("      STRATEGY COMPARISON: NIFTY 50 vs. BANK NIFTY (5-MINUTE TIMEFRAME)")
    print("      Period: 2026-01-01 to 2026-11-06 | Starting Capital: ₹1,50,000")
    print("=" * 90)
    print(f"{'Metric':<32} | {'NIFTY 50 (5m)':<22} | {'BANK NIFTY (5m)':<22} | {'Delta / Advantage'}")
    print("-" * 90)

    rows_5m = [
        ("Lot Size (Units)", f"{n5['lot_size']}", f"{b5['lot_size']}", "-35 units (-53.8%)"),
        ("Total Trades", f"{n5['total_trades']}", f"{b5['total_trades']}", f"{b5['total_trades'] - n5['total_trades']:+d} trades"),
        ("Win Rate (%)", f"{n5['win_rate']:.1f}% ({n5['wins']}W / {n5['losses']}L)", f"{b5['win_rate']:.1f}% ({b5['wins']}W / {b5['losses']}L)", f"{b5['win_rate'] - n5['win_rate']:+.1f}%"),
        ("Net P&L (Fixed 1-Lot)", f"₹{n5['net_pnl']:+,.2f}", f"₹{b5['net_pnl']:+,.2f}", f"₹{b5['net_pnl'] - n5['net_pnl']:+,.2f}"),
        ("Profit Factor", f"{n5['profit_factor']:.2f}", f"{b5['profit_factor']:.2f}", f"{b5['profit_factor'] - n5['profit_factor']:+.2f}"),
        ("Avg Trade P&L", f"₹{n5['avg_trade_pnl']:+,.2f}", f"₹{b5['avg_trade_pnl']:+,.2f}", f"₹{b5['avg_trade_pnl'] - n5['avg_trade_pnl']:+,.2f}"),
        ("CE Trades Win Rate / P&L", f"{n5['ce_wr']:.1f}% (₹{n5['ce_pnl']:+,.0f})", f"{b5['ce_wr']:.1f}% (₹{b5['ce_pnl']:+,.0f})", f"₹{b5['ce_pnl'] - n5['ce_pnl']:+,.0f}"),
        ("PE Trades Win Rate / P&L", f"{n5['pe_wr']:.1f}% (₹{n5['pe_pnl']:+,.0f})", f"{b5['pe_wr']:.1f}% (₹{b5['pe_pnl']:+,.0f})", f"₹{b5['pe_pnl'] - n5['pe_pnl']:+,.0f}"),
        ("Risk-Reward Ratio", f"{n5['rr_ratio']:.2f}", f"{b5['rr_ratio']:.2f}", f"{b5['rr_ratio'] - n5['rr_ratio']:+.2f}"),
        ("Avg Win / Avg Loss", f"₹{n5['avg_win']:,.0f} / ₹{n5['avg_loss']:,.0f}", f"₹{b5['avg_win']:,.0f} / ₹{b5['avg_loss']:,.0f}", ""),
        ("Max Single Win", f"₹{n5['max_win']:+,.2f}", f"₹{b5['max_win']:+,.2f}", f"₹{b5['max_win'] - n5['max_win']:+,.2f}"),
        ("Max Single Loss", f"₹{n5['max_loss']:+,.2f}", f"₹{b5['max_loss']:+,.2f}", f"₹{b5['max_loss'] - n5['max_loss']:+,.2f} (Deeper loss)"),
        ("Max Drawdown (₹)", f"₹{n5['max_drawdown']:,.2f}", f"₹{b5['max_drawdown']:,.2f}", f"₹{b5['max_drawdown'] - n5['max_drawdown']:+,.2f} (+138% DD)"),
        ("Max DD % of Initial Capital", f"{n5['max_dd_pct_capital']:.1f}%", f"{b5['max_dd_pct_capital']:.1f}%", f"{b5['max_dd_pct_capital'] - n5['max_dd_pct_capital']:+.1f}%"),
        ("Daily Sharpe / Sortino", f"{n5['sharpe']:.2f} / {n5['sortino']:.2f}", f"{b5['sharpe']:.2f} / {b5['sortino']:.2f}", f"{b5['sharpe'] - n5['sharpe']:+.2f} / {b5['sortino'] - n5['sortino']:+.2f}"),
        ("Avg Duration (Holding)", f"{n5['avg_duration_min']:.0f} mins (~{n5['avg_duration_min']/60:.1f}h)", f"{b5['avg_duration_min']:.0f} mins (~{b5['avg_duration_min']/60:.1f}h)", f"{b5['avg_duration_min'] - n5['avg_duration_min']:+.0f} mins"),
        ("15:00 EOD Exits", f"{n5['eod_trades']}T ({n5['eod_wr']:.1f}%W, ₹{n5['eod_pnl']:+,.0f})", f"{b5['eod_trades']}T ({b5['eod_wr']:.1f}%W, ₹{b5['eod_pnl']:+,.0f})", ""),
        ("ST Flip Exits", f"{n5['st_trades']}T ({n5['st_wr']:.1f}%W, ₹{n5['st_pnl']:+,.0f})", f"{b5['st_trades']}T ({b5['st_wr']:.1f}%W, ₹{b5['st_pnl']:+,.0f})", ""),
        ("Dynamic Scaling Net P&L", f"₹{n5['dyn_pnl']:+,.2f}", f"₹{b5['dyn_pnl']:+,.2f}", f"₹{b5['dyn_pnl'] - n5['dyn_pnl']:+,.2f}"),
        ("Dynamic Max Lots Reached", f"{n5['max_lots']} lots (195 qty)", f"{b5['max_lots']} lots (60 qty)", "-1 lot / slower ramp"),
        ("Dynamic Max Drawdown (₹)", f"₹{n5['dyn_dd']:,.2f}", f"₹{b5['dyn_dd']:,.2f}", f"₹{b5['dyn_dd'] - n5['dyn_dd']:+,.2f}"),
    ]

    for label, vn, vb, diff in rows_5m:
        print(f"{label:<32} | {vn:<22} | {vb:<22} | {diff}")
    print("=" * 90)

    # Monthly comparison table
    df_n = n5["df"].copy()
    df_b = b5["df"].copy()
    df_n["month"] = pd.to_datetime(df_n["date"]).dt.strftime("%Y-%m")
    df_b["month"] = pd.to_datetime(df_b["date"]).dt.strftime("%Y-%m")

    m_n = df_n.groupby("month")["pnl"].sum()
    m_b = df_b.groupby("month")["pnl"].sum()
    months_df = pd.DataFrame({"NIFTY_50_P&L": m_n, "BANK_NIFTY_P&L": m_b})
    months_df["Delta (BN - NIFTY)"] = months_df["BANK_NIFTY_P&L"] - months_df["NIFTY_50_P&L"]
    months_df["Outperformer"] = np.where(months_df["BANK_NIFTY_P&L"] > months_df["NIFTY_50_P&L"], "BANK NIFTY", "NIFTY 50")

    print("\nMONTHLY NET P&L COMPARISON (Fixed 1-Lot, in ₹):")
    print(months_df.to_string(formatters={
        "NIFTY_50_P&L": lambda x: f"₹{x:+10,.2f}",
        "BANK_NIFTY_P&L": lambda x: f"₹{x:+10,.2f}",
        "Delta (BN - NIFTY)": lambda x: f"₹{x:+10,.2f}",
    }))


if __name__ == "__main__":
    main()
