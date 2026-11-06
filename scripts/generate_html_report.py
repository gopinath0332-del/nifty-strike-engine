"""
scripts/generate_html_report.py
===============================
Generates a comprehensive, professional, interactive HTML report for the backtest.

Includes:
  1. Quant & Risk Metrics:
     - Net P&L, Win Rate %, Profit Factor, Max Drawdown
     - Risk-Reward Ratio (Avg Win vs Avg Loss)
     - Win & Loss Streaks (Max consecutive wins & losses)
     - Return on Margin (ROI %)
     - Annualized Sharpe & Sortino Ratios
     - Equity Curve & Drawdown Underwater Chart (Dual Chart.js)
  2. Temporal & Behavioral Breakdown:
     - Day of Week Analysis (Monday - Friday)
     - Time of Day / Entry Window (Morning, Mid-Day, Afternoon)
     - Holding Duration Stats (Avg, Winners vs Losers, Min, Max)
  3. Strategy & Signal Diagnostics:
     - Exit Reason Performance (Supertrend Flip vs EOD 15:00)
     - Indicator Diagnostics (RSI at Entry for Winners vs Losers)
  4. Interactive UI Controls:
     - Filter Tabs (All, CE, PE, Wins, Losses, ST Flip, EOD)
     - Live Search Bar (Date, Strike, Symbol, Reason)
     - Sortable Columns on Click
     - Direct CSV Export Button
"""

from __future__ import annotations

import json
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import get_settings

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_2026.csv")
HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_report_2026.html")


def generate_report(csv_path: str | None = None, html_path: str | None = None, timeframe_label: str | None = None):
    try:
        cfg = get_settings()
        lot_size = cfg.lot_size
        default_interval = cfg.candle_interval_minutes
    except Exception:
        lot_size = 65
        default_interval = 5

    tf_label = timeframe_label or f"{default_interval}-min"

    if csv_path:
        csv_to_use = csv_path
    elif os.path.exists(CSV_PATH):
        csv_to_use = CSV_PATH
    else:
        alt_csv = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_latest.csv")
        csv_to_use = alt_csv if os.path.exists(alt_csv) else CSV_PATH

    target_html = html_path or HTML_PATH

    if not os.path.exists(csv_to_use):
        print(f"Error: Neither {CSV_PATH} nor {alt_csv} found. Please run scripts/run_backtest.py first.")
        return

    df = pd.read_csv(csv_to_use)
    if df.empty:
        print("No trades found in CSV.")
        return

    # Datetime and duration parsing
    interval_mins = int(tf_label.replace("-min", "")) if "-min" in tf_label else default_interval
    df["dt"] = pd.to_datetime(df["date"])
    df["weekday"] = df["dt"].dt.day_name()
    df["holding_mins"] = df["holding_bars"] * interval_mins

    entry_dt = pd.to_datetime(df["entry_time"])
    df["entry_hour"] = entry_dt.dt.hour
    df["entry_minute"] = entry_dt.dt.minute

    # Entry window classification
    df["time_slot"] = "Mid-Day (10:30-13:00)"
    df.loc[(df["entry_hour"] == 9) | ((df["entry_hour"] == 10) & (df["entry_minute"] <= 30)), "time_slot"] = "Morning (09:15-10:30)"
    df.loc[(df["entry_hour"] >= 13), "time_slot"] = "Afternoon (13:00-14:45)"

    # Base KPI Calculations
    total_trades = len(df)
    winning_trades = len(df[df["pnl"] > 0])
    losing_trades = len(df[df["pnl"] <= 0])
    win_rate = (winning_trades / total_trades) * 100 if total_trades else 0
    total_pnl = df["pnl"].sum()
    gross_profit = df[df["pnl"] > 0]["pnl"].sum()
    gross_loss = abs(df[df["pnl"] < 0]["pnl"].sum())
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else 0
    max_win = df["pnl"].max()
    max_loss = df["pnl"].min()

    # Premium & Margin (1% of Spot)
    df["est_entry_prem"] = (df["entry_spot"] * 0.01).round(1)
    df["margin_used"] = (df["est_entry_prem"] * lot_size).round(2)
    df["est_exit_prem"] = (df["est_entry_prem"] + df["opt_points"]).round(1)
    avg_margin_per_trade = df["margin_used"].mean()
    roi_margin = (total_pnl / avg_margin_per_trade * 100) if avg_margin_per_trade else 0

    # Risk-Reward & Streaks
    wins_series = df[df["pnl"] > 0]["pnl"]
    losses_series = abs(df[df["pnl"] < 0]["pnl"])
    avg_win = wins_series.mean() if not wins_series.empty else 0
    avg_loss = losses_series.mean() if not losses_series.empty else 0
    rr_ratio = (avg_win / avg_loss) if avg_loss else 0

    pnl_sign = (df["pnl"] > 0).astype(int)
    streaks = []
    cur_streak = 0
    prev_sign = None
    for s in pnl_sign:
        if prev_sign is None:
            cur_streak = 1 if s == 1 else -1
        elif s == prev_sign:
            cur_streak += 1 if s == 1 else -1
        else:
            cur_streak = 1 if s == 1 else -1
        prev_sign = s
        streaks.append(cur_streak)

    max_consec_wins = max([s for s in streaks if s > 0], default=0)
    max_consec_losses = abs(min([s for s in streaks if s < 0], default=0))

    # Sharpe & Sortino (Annualized based on 252 trading days)
    daily_pnl = df.groupby("date")["pnl"].sum()
    mean_daily = daily_pnl.mean()
    std_daily = daily_pnl.std()
    sharpe = (mean_daily / std_daily) * np.sqrt(252) if (std_daily and std_daily > 0) else 0
    downside_returns = daily_pnl[daily_pnl < 0]
    downside_std = downside_returns.std()
    sortino = (mean_daily / downside_std) * np.sqrt(252) if (downside_std and downside_std > 0) else 0

    # Holding Duration
    avg_duration = df["holding_mins"].mean()
    win_duration = df[df["pnl"] > 0]["holding_mins"].mean()
    loss_duration = df[df["pnl"] < 0]["holding_mins"].mean()
    min_duration = df["holding_mins"].min()
    max_duration = df["holding_mins"].max()

    # CE vs PE breakdown
    ce_df = df[df["option_type"] == "CE"]
    pe_df = df[df["option_type"] == "PE"]
    ce_total = len(ce_df)
    ce_wins = len(ce_df[ce_df["pnl"] > 0])
    ce_losses = len(ce_df[ce_df["pnl"] <= 0])
    ce_win_rate = (ce_wins / ce_total * 100) if ce_total else 0
    ce_pnl = ce_df["pnl"].sum()

    pe_total = len(pe_df)
    pe_wins = len(pe_df[pe_df["pnl"] > 0])
    pe_losses = len(pe_df[pe_df["pnl"] <= 0])
    pe_win_rate = (pe_wins / pe_total * 100) if pe_total else 0
    pe_pnl = pe_df["pnl"].sum()

    # Cumulative PnL & Drawdown for Charts
    df["cum_pnl"] = df["pnl"].cumsum()
    df["peak"] = df["cum_pnl"].cummax()
    df["drawdown"] = df["cum_pnl"] - df["peak"]
    max_drawdown = abs(df["drawdown"].min())

    chart_labels = df["date"].tolist()
    chart_pnl = df["cum_pnl"].round(2).tolist()
    chart_dd = df["drawdown"].round(2).tolist()

    # Monthly breakdown
    df["month"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m")
    monthly_stats = df.groupby("month").agg(
        trades=("pnl", "count"),
        wins=("pnl", lambda x: (x > 0).sum()),
        net_pnl=("pnl", "sum"),
    ).reset_index()
    monthly_stats["win_rate"] = (monthly_stats["wins"] / monthly_stats["trades"] * 100).round(1)

    monthly_rows_html = []
    for _, row in monthly_stats.iterrows():
        pnl = row["net_pnl"]
        cls = "profit" if pnl >= 0 else "loss"
        monthly_rows_html.append(f"""
        <tr>
            <td><strong>{row['month']}</strong></td>
            <td>{row['trades']}</td>
            <td>{row['win_rate']}%</td>
            <td class="{cls}">₹{pnl:+,.2f}</td>
        </tr>
        """)

    # Day of Week Breakdown
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    dow_stats = df.groupby("weekday").agg(
        trades=("pnl", "count"),
        wins=("pnl", lambda x: (x > 0).sum()),
        losses=("pnl", lambda x: (x <= 0).sum()),
        net_pnl=("pnl", "sum"),
    ).reindex(day_order).dropna().reset_index()
    dow_stats["win_rate"] = (dow_stats["wins"] / dow_stats["trades"] * 100).round(1)

    dow_rows_html = []
    for _, r in dow_stats.iterrows():
        pnl = r["net_pnl"]
        cls = "profit" if pnl >= 0 else "loss"
        dow_rows_html.append(f"""
        <tr>
            <td><strong>{r['weekday']}</strong></td>
            <td>{int(r['trades'])}</td>
            <td>{r['win_rate']}%</td>
            <td>{int(r['wins'])}W / {int(r['losses'])}L</td>
            <td class="{cls}">₹{pnl:+,.2f}</td>
        </tr>
        """)

    # Time Slot Breakdown
    slot_stats = df.groupby("time_slot").agg(
        trades=("pnl", "count"),
        wins=("pnl", lambda x: (x > 0).sum()),
        net_pnl=("pnl", "sum"),
    ).reset_index()
    slot_stats["win_rate"] = (slot_stats["wins"] / slot_stats["trades"] * 100).round(1)

    slot_rows_html = []
    for _, r in slot_stats.iterrows():
        pnl = r["net_pnl"]
        cls = "profit" if pnl >= 0 else "loss"
        slot_rows_html.append(f"""
        <tr>
            <td><strong>{r['time_slot']}</strong></td>
            <td>{int(r['trades'])}</td>
            <td>{r['win_rate']}%</td>
            <td class="{cls}">₹{pnl:+,.2f}</td>
        </tr>
        """)

    # Exit Reason Breakdown
    exit_stats = df.groupby("exit_reason").agg(
        trades=("pnl", "count"),
        wins=("pnl", lambda x: (x > 0).sum()),
        losses=("pnl", lambda x: (x <= 0).sum()),
        net_pnl=("pnl", "sum"),
    ).reset_index()
    exit_stats["win_rate"] = (exit_stats["wins"] / exit_stats["trades"] * 100).round(1)

    exit_rows_html = []
    for _, r in exit_stats.iterrows():
        pnl = r["net_pnl"]
        cls = "profit" if pnl >= 0 else "loss"
        exit_rows_html.append(f"""
        <tr>
            <td><strong>{r['exit_reason']}</strong></td>
            <td>{int(r['trades'])}</td>
            <td>{r['win_rate']}%</td>
            <td>{int(r['wins'])}W / {int(r['losses'])}L</td>
            <td class="{cls}">₹{pnl:+,.2f}</td>
        </tr>
        """)

    # Indicator Diagnostics (RSI averages)
    ce_win_rsi = ce_df[ce_df["pnl"] > 0]["rsi"].mean() if not ce_df[ce_df["pnl"] > 0].empty else 0
    ce_loss_rsi = ce_df[ce_df["pnl"] <= 0]["rsi"].mean() if not ce_df[ce_df["pnl"] <= 0].empty else 0
    pe_win_rsi = pe_df[pe_df["pnl"] > 0]["rsi"].mean() if not pe_df[pe_df["pnl"] > 0].empty else 0
    pe_loss_rsi = pe_df[pe_df["pnl"] <= 0]["rsi"].mean() if not pe_df[pe_df["pnl"] <= 0].empty else 0

    # Build Trade rows HTML
    trade_rows_html = []
    for i, row in df.iterrows():
        pnl = row["pnl"]
        pnl_class = "profit" if pnl > 0 else "loss"
        pnl_badge = f'<span class="badge {pnl_class}">₹{pnl:+,.2f}</span>'
        type_badge = f'<span class="badge badge-{row["option_type"].lower()}">{row["option_type"]}</span>'

        entry_time_str = str(row["entry_time"]).split(" ")[-1][:5]
        exit_time_str = str(row["exit_time"]).split(" ")[-1][:5]
        dur_mins = int(row["holding_mins"])

        row_html = f"""
        <tr data-type="{row['option_type']}" data-outcome="{'win' if pnl > 0 else 'loss'}" data-exit="{row['exit_reason']}">
            <td><strong>#{i+1}</strong></td>
            <td>{row['date']}</td>
            <td><code>{entry_time_str}</code></td>
            <td><code>{exit_time_str}</code></td>
            <td><span class="dur-tag">{dur_mins}m</span></td>
            <td>{type_badge}</td>
            <td><strong>{row['symbol']}</strong></td>
            <td>₹{row['entry_spot']:,.1f}</td>
            <td>₹{row['est_entry_prem']:.1f}</td>
            <td>₹{row['est_exit_prem']:.1f}</td>
            <td>₹{row['margin_used']:,.2f}</td>
            <td>{pnl_badge}</td>
            <td><span class="reason-tag">{row['exit_reason']}</span></td>
        </tr>
        """
        trade_rows_html.append(row_html)

    # HTML Document Template
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NIFTY Options Buying Strategy v9 - Advanced Backtest Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-base: #0b0f19;
            --surface: #111827;
            --surface-hover: #1f2937;
            --border: #1f293d;
            --border-light: #374151;
            --primary: #38bdf8;
            --green: #10b981;
            --green-dim: rgba(16, 185, 129, 0.12);
            --red: #f43f5e;
            --red-dim: rgba(244, 63, 94, 0.12);
            --amber: #f59e0b;
            --purple: #a855f7;
            --text-main: #f3f4f6;
            --text-muted: #94a3b8;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg-base);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            padding: 24px;
            line-height: 1.5;
        }}
        .container {{ max-width: 1440px; margin: 0 auto; }}
        
        /* Header */
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--border);
        }}
        h1 {{ font-size: 24px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 10px; }}
        .subtitle {{ font-size: 13.5px; color: var(--text-muted); margin-top: 4px; }}
        .header-tag {{
            background: #1e293b;
            padding: 6px 14px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 600;
            color: var(--primary);
            border: 1px solid #334155;
        }}

        /* Section Title */
        .section-header {{
            font-size: 16px;
            font-weight: 700;
            color: #fff;
            margin: 28px 0 14px 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        /* Grid Cards */
        .grid-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 14px;
            margin-bottom: 24px;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
            transition: transform 0.15s ease, border-color 0.15s ease;
        }}
        .card:hover {{ border-color: var(--border-light); transform: translateY(-1px); }}
        .card-label {{ font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 6px; }}
        .card-val {{ font-size: 21px; font-weight: 700; }}
        .card-sub {{ font-size: 12.5px; margin-top: 4px; color: var(--text-muted); }}

        .profit {{ color: var(--green); }}
        .loss {{ color: var(--red); }}

        /* Multi-box layouts */
        .analytics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .chart-row {{
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 16px;
            margin-bottom: 24px;
        }}
        @media (max-width: 1024px) {{ .chart-row {{ grid-template-columns: 1fr; }} }}

        .panel {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 18px;
        }}
        .panel-title {{
            font-size: 14px;
            font-weight: 700;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        /* Tables */
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{
            background: #151d2f;
            color: var(--text-muted);
            text-align: left;
            padding: 10px 12px;
            font-weight: 600;
            font-size: 11.5px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            user-select: none;
        }}
        th:hover {{ color: var(--primary); }}
        td {{ padding: 10px 12px; border-bottom: 1px solid #1a2234; white-space: nowrap; }}
        tr:hover td {{ background: var(--surface-hover); }}

        /* Badges */
        .badge {{ padding: 3px 8px; border-radius: 6px; font-weight: 600; font-size: 12px; }}
        .badge.profit {{ background: var(--green-dim); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.3); }}
        .badge.loss {{ background: var(--red-dim); color: var(--red); border: 1px solid rgba(244, 63, 94, 0.3); }}
        .badge-ce {{ background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }}
        .badge-pe {{ background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }}
        .dur-tag {{ font-size: 11.5px; color: #cbd5e1; background: #1e293b; padding: 2px 7px; border-radius: 4px; border: 1px solid #334155; }}
        .reason-tag {{ font-size: 11.5px; color: #94a3b8; background: #0f172a; padding: 2px 8px; border-radius: 4px; border: 1px solid #334155; }}

        /* Interactive Controls */
        .controls-bar {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 12px 16px;
            margin-bottom: 14px;
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
        }}
        .filter-group {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .btn-filter {{
            background: #1a2333;
            border: 1px solid var(--border);
            color: var(--text-muted);
            padding: 6px 13px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
        }}
        .btn-filter:hover {{ background: #243046; color: #fff; }}
        .btn-filter.active {{ background: var(--primary); color: #0b0f19; border-color: var(--primary); }}

        .search-input {{
            background: #0b0f19;
            border: 1px solid var(--border);
            color: #fff;
            padding: 7px 14px;
            border-radius: 6px;
            font-size: 12.5px;
            width: 240px;
        }}
        .search-input:focus {{ outline: none; border-color: var(--primary); }}

        .btn-export {{
            background: #059669;
            color: #fff;
            border: none;
            padding: 7px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s;
        }}
        .btn-export:hover {{ background: #10b981; }}
        .table-container {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow-x: auto; }}
    </style>
</head>
<body>
<div class="container">
    <header>
        <div>
            <h1>📊 NIFTY Options Buying Strategy v9 ({tf_label})</h1>
            <div class="subtitle">Backtest Ledger & Performance Intelligence • 2026-01-01 to 2026-11-06 ({tf_label} timeframe, {lot_size} qty)</div>
        </div>
        <div class="header-tag">Strategy: NIFTY_V9</div>
    </header>

    <!-- SECTION 1: Top-Level Quant & Risk KPI Cards -->
    <div class="grid-cards">
        <div class="card">
            <div class="card-label">Net Profit / Loss</div>
            <div class="card-val profit">₹{total_pnl:+,.2f}</div>
            <div class="card-sub">Return on Margin: <strong class="profit">{roi_margin:+.1f}%</strong></div>
        </div>
        <div class="card">
            <div class="card-label">Overall Win Rate</div>
            <div class="card-val">{win_rate:.1f}%</div>
            <div class="card-sub">{winning_trades} Wins / {losing_trades} Losses ({total_trades} Trades)</div>
        </div>
        <div class="card">
            <div class="card-label">📈 CE Trades Breakdown</div>
            <div class="card-val" style="color:var(--primary);">
                {ce_wins}W / {ce_losses}L
            </div>
            <div class="card-sub { 'profit' if ce_pnl >= 0 else 'loss' }">{ce_win_rate:.1f}% Win Rate • ₹{ce_pnl:+,.2f}</div>
        </div>
        <div class="card">
            <div class="card-label">📉 PE Trades Breakdown</div>
            <div class="card-val" style="color:var(--amber);">
                {pe_wins}W / {pe_losses}L
            </div>
            <div class="card-sub { 'profit' if pe_pnl >= 0 else 'loss' }">{pe_win_rate:.1f}% Win Rate • ₹{pe_pnl:+,.2f}</div>
        </div>
        <div class="card">
            <div class="card-label">Risk-Reward Ratio</div>
            <div class="card-val" style="color:var(--purple);">{rr_ratio:.2f} : 1</div>
            <div class="card-sub">Avg Win: <span class="profit">₹{avg_win:,.0f}</span> | Avg Loss: <span class="loss">₹{avg_loss:,.0f}</span></div>
        </div>
        <div class="card">
            <div class="card-label">Profit Factor & Drawdown</div>
            <div class="card-val">{profit_factor}</div>
            <div class="card-sub">Max DD: <strong style="color:var(--red);">₹{max_drawdown:,.2f}</strong></div>
        </div>
        <div class="card">
            <div class="card-label">Sharpe & Sortino</div>
            <div class="card-val" style="color:var(--primary);">{sharpe:.2f} <span style="font-size:14px; color:var(--text-muted);">/ {sortino:.2f}</span></div>
            <div class="card-sub">Daily Annualized Risk-Adjusted Return</div>
        </div>
        <div class="card">
            <div class="card-label">Win / Loss Streaks</div>
            <div class="card-val"><span class="profit">{max_consec_wins}W</span> / <span class="loss">{max_consec_losses}L</span></div>
            <div class="card-sub">Max Consecutive Run</div>
        </div>
    </div>

    <!-- Dual Charts & Monthly Panel -->
    <div class="chart-row">
        <div class="panel">
            <div class="panel-title">
                <span>📈 Cumulative Equity Curve (₹)</span>
                <span style="font-size:12px; color:var(--green);">Peak: ₹{df['cum_pnl'].max():+,.2f}</span>
            </div>
            <canvas id="equityChart" height="95"></canvas>
            
            <div class="panel-title" style="margin-top:20px;">
                <span>🌊 Drawdown Underwater Profile (₹)</span>
                <span style="font-size:12px; color:var(--red);">Max DD: -₹{max_drawdown:,.2f}</span>
            </div>
            <canvas id="drawdownChart" height="55"></canvas>
        </div>
        <div class="panel" style="overflow-y:auto; max-height: 480px;">
            <div class="panel-title">📅 Monthly Performance</div>
            <table>
                <thead>
                    <tr>
                        <th>Month</th>
                        <th>Trades</th>
                        <th>Win %</th>
                        <th>Net P&L</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(monthly_rows_html)}
                </tbody>
            </table>
        </div>
    </div>

    <!-- SECTION 2 & 3: Temporal Analytics & Signal Diagnostics -->
    <div class="section-header">🔍 Temporal & Diagnostic Breakdown</div>
    <div class="analytics-grid">
        <!-- Day of Week Panel -->
        <div class="panel">
            <div class="panel-title">🗓️ Day-of-Week Performance</div>
            <table>
                <thead>
                    <tr>
                        <th>Day</th>
                        <th>Trades</th>
                        <th>Win %</th>
                        <th>W / L</th>
                        <th>Net P&L</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(dow_rows_html)}
                </tbody>
            </table>
        </div>

        <!-- Time of Day & Holding Duration Panel -->
        <div class="panel">
            <div class="panel-title">⏰ Entry Window & Duration</div>
            <table>
                <thead>
                    <tr>
                        <th>Window</th>
                        <th>Trades</th>
                        <th>Win %</th>
                        <th>Net P&L</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(slot_rows_html)}
                </tbody>
            </table>
            <div style="margin-top:14px; padding-top:12px; border-top:1px solid var(--border); font-size:12.5px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                    <span style="color:var(--text-muted);">Avg Holding Time:</span>
                    <strong>{avg_duration:.0f} mins (~{avg_duration/60:.1f} hrs)</strong>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                    <span style="color:var(--text-muted);">Avg Win Duration:</span>
                    <strong class="profit">{win_duration:.0f} mins</strong>
                </div>
                <div style="display:flex; justify-content:space-between;">
                    <span style="color:var(--text-muted);">Avg Loss Duration:</span>
                    <strong class="loss">{loss_duration:.0f} mins</strong>
                </div>
            </div>
        </div>

        <!-- Exit Reason & Indicators Diagnostics -->
        <div class="panel">
            <div class="panel-title">🎯 Exit Diagnostics & Indicator Averages</div>
            <table>
                <thead>
                    <tr>
                        <th>Exit Rule</th>
                        <th>Trades</th>
                        <th>Win %</th>
                        <th>W / L</th>
                        <th>Net P&L</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(exit_rows_html)}
                </tbody>
            </table>
            <div style="margin-top:14px; padding-top:12px; border-top:1px solid var(--border); font-size:12.5px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                    <span style="color:var(--text-muted);">Avg CE Entry RSI:</span>
                    <span>Wins: <strong class="profit">{ce_win_rsi:.1f}</strong> | Losses: <strong class="loss">{ce_loss_rsi:.1f}</strong></span>
                </div>
                <div style="display:flex; justify-content:space-between;">
                    <span style="color:var(--text-muted);">Avg PE Entry RSI:</span>
                    <span>Wins: <strong class="profit">{pe_win_rsi:.1f}</strong> | Losses: <strong class="loss">{pe_loss_rsi:.1f}</strong></span>
                </div>
            </div>
        </div>
    </div>

    <!-- SECTION 4: Interactive Trade Ledger with Filters & Search -->
    <div class="section-header">📑 Complete Interactive Trade Ledger ({total_trades} Trades)</div>
    <div class="controls-bar">
        <div class="filter-group">
            <button class="btn-filter active" onclick="setFilter('all', this)">All ({total_trades})</button>
            <button class="btn-filter" onclick="setFilter('CE', this)">CE Only ({ce_total})</button>
            <button class="btn-filter" onclick="setFilter('PE', this)">PE Only ({pe_total})</button>
            <button class="btn-filter" onclick="setFilter('win', this)">Wins ({winning_trades})</button>
            <button class="btn-filter" onclick="setFilter('loss', this)">Losses ({losing_trades})</button>
            <button class="btn-filter" onclick="setFilter('EOD', this)">EOD Exits</button>
            <button class="btn-filter" onclick="setFilter('ST Flip', this)">ST Flip Exits</button>
        </div>
        <div style="display:flex; gap:10px; align-items:center;">
            <input type="text" id="searchInput" class="search-input" placeholder="🔍 Search date, strike, reason..." onkeyup="applyFilters()">
            <button class="btn-export" onclick="exportTableToCSV()">⬇️ Export CSV</button>
        </div>
    </div>

    <div class="table-container">
        <table id="tradesTable">
            <thead>
                <tr>
                    <th onclick="sortTable(0)"># ⬍</th>
                    <th onclick="sortTable(1)">Date ⬍</th>
                    <th onclick="sortTable(2)">Entry ⬍</th>
                    <th onclick="sortTable(3)">Exit ⬍</th>
                    <th onclick="sortTable(4)">Duration ⬍</th>
                    <th onclick="sortTable(5)">Type ⬍</th>
                    <th onclick="sortTable(6)">Contract Symbol ⬍</th>
                    <th onclick="sortTable(7)">Spot Price ⬍</th>
                    <th onclick="sortTable(8)">Est. Entry (1%) ⬍</th>
                    <th onclick="sortTable(9)">Est. Exit ⬍</th>
                    <th onclick="sortTable(10)">Margin ({lot_size} Qty) ⬍</th>
                    <th onclick="sortTable(11)">Net P&L (₹) ⬍</th>
                    <th onclick="sortTable(12)">Exit Reason ⬍</th>
                </tr>
            </thead>
            <tbody>
                {"".join(trade_rows_html)}
            </tbody>
        </table>
    </div>
</div>

<script>
    // 1. Equity Curve Chart
    const ctxEquity = document.getElementById('equityChart').getContext('2d');
    new Chart(ctxEquity, {{
        type: 'line',
        data: {{
            labels: {json.dumps(chart_labels)},
            datasets: [{{
                label: 'Cumulative P&L (₹)',
                data: {json.dumps(chart_pnl)},
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.08)',
                fill: true,
                tension: 0.25,
                borderWidth: 2,
                pointRadius: 1,
            }}]
        }},
        options: {{
            responsive: true,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', maxTicksLimit: 12 }} }},
                y: {{ grid: {{ color: 'rgba(51, 65, 85, 0.3)' }}, ticks: {{ color: '#94a3b8' }} }}
            }}
        }}
    }});

    // 2. Drawdown Underwater Chart
    const ctxDD = document.getElementById('drawdownChart').getContext('2d');
    new Chart(ctxDD, {{
        type: 'line',
        data: {{
            labels: {json.dumps(chart_labels)},
            datasets: [{{
                label: 'Drawdown (₹)',
                data: {json.dumps(chart_dd)},
                borderColor: '#f43f5e',
                backgroundColor: 'rgba(244, 63, 94, 0.15)',
                fill: true,
                tension: 0.2,
                borderWidth: 1.5,
                pointRadius: 0,
            }}]
        }},
        options: {{
            responsive: true,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ display: false }},
                y: {{ grid: {{ color: 'rgba(51, 65, 85, 0.3)' }}, ticks: {{ color: '#94a3b8', maxTicksLimit: 4 }} }}
            }}
        }}
    }});

    // 3. Interactive Filtering
    let currentCategory = 'all';

    function setFilter(cat, btn) {{
        currentCategory = cat;
        document.querySelectorAll('.btn-filter').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        applyFilters();
    }}

    function applyFilters() {{
        const search = document.getElementById("searchInput").value.toUpperCase();
        const rows = document.getElementById("tradesTable").getElementsByTagName("tbody")[0].getElementsByTagName("tr");

        for (let i = 0; i < rows.length; i++) {{
            const row = rows[i];
            const type = row.getAttribute('data-type');
            const outcome = row.getAttribute('data-outcome');
            const exit = row.getAttribute('data-exit');
            const text = (row.textContent || row.innerText).toUpperCase();

            let matchesCat = false;
            if (currentCategory === 'all') matchesCat = true;
            else if (currentCategory === 'CE' && type === 'CE') matchesCat = true;
            else if (currentCategory === 'PE' && type === 'PE') matchesCat = true;
            else if (currentCategory === 'win' && outcome === 'win') matchesCat = true;
            else if (currentCategory === 'loss' && outcome === 'loss') matchesCat = true;
            else if (currentCategory === 'EOD' && exit.includes('EOD')) matchesCat = true;
            else if (currentCategory === 'ST Flip' && exit.includes('ST Flip')) matchesCat = true;

            const matchesSearch = text.indexOf(search) > -1;
            row.style.display = (matchesCat && matchesSearch) ? "" : "none";
        }}
    }}

    // 4. Sortable Table Columns
    let sortDirections = {{}};
    function sortTable(colIndex) {{
        const table = document.getElementById("tradesTable");
        const tbody = table.getElementsByTagName("tbody")[0];
        const rows = Array.from(tbody.getElementsByTagName("tr"));
        const dir = sortDirections[colIndex] === 'asc' ? 'desc' : 'asc';
        sortDirections[colIndex] = dir;

        rows.sort((a, b) => {{
            let cellA = a.getElementsByTagName("td")[colIndex].innerText.trim();
            let cellB = b.getElementsByTagName("td")[colIndex].innerText.trim();

            // Strip symbols for numeric comparison
            let numA = parseFloat(cellA.replace(/[₹,+#m%]/g, ''));
            let numB = parseFloat(cellB.replace(/[₹,+#m%]/g, ''));

            if (!isNaN(numA) && !isNaN(numB)) {{
                return dir === 'asc' ? numA - numB : numB - numA;
            }}
            return dir === 'asc' ? cellA.localeCompare(cellB) : cellB.localeCompare(cellA);
        }});

        rows.forEach(r => tbody.appendChild(r));
    }}

    // 5. CSV Export
    function exportTableToCSV() {{
        let csv = [];
        const rows = document.querySelectorAll("#tradesTable tr");
        for (let i = 0; i < rows.length; i++) {{
            if (rows[i].style.display === "none") continue;
            let row = [], cols = rows[i].querySelectorAll("td, th");
            for (let j = 0; j < cols.length; j++) {{
                row.push('"' + cols[j].innerText.replace(/"/g, '""').trim() + '"');
            }}
            csv.push(row.join(","));
        }}
        const blob = new Blob([csv.join("\\n")], {{ type: "text/csv" }});
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "nifty_backtest_ledger_export.csv";
        a.click();
    }}
</script>
</body>
</html>
"""

    if os.path.exists(target_html):
        try:
            os.remove(target_html)
            print(f"Old report deleted: {target_html}")
        except Exception as exc:
            print(f"Warning: Could not remove old HTML report: {exc}")

    with open(target_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\nAdvanced HTML Report successfully generated at:\n  file:///{os.path.abspath(target_html)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--timeframe", default=None)
    args = parser.parse_args()
    generate_report(csv_path=args.csv, html_path=args.output, timeframe_label=args.timeframe)
