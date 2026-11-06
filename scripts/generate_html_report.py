"""
scripts/generate_html_report.py
===============================
Generates a clean, professional, responsive interactive HTML report for the backtest.
Includes:
  - KPI Dashboard Cards (Net P&L, Win Rate, Profit Factor, Total Margin, Max Drawdown)
  - Cumulative P&L Equity Curve chart (SVG / Chart.js embedded)
  - Monthly Performance Table
  - Trade Ledger with:
      Trade #, Date, Entry Time, Exit Time, Symbol, Strike, Type (CE/PE),
      Entry Spot/Price, Exit Spot/Price, Points, Margin Used (₹), Net P&L (₹),
      Exit Reason, Status (Win/Loss).
"""

from __future__ import annotations

import json
import os
import sys
import pandas as pd

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_trades_2026.csv")
HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_report_2026.html")


def generate_report():
    if not os.path.exists(CSV_PATH):
        print(f"Error: {CSV_PATH} not found. Please run scripts/run_backtest.py first.")
        return

    df = pd.read_csv(CSV_PATH)
    if df.empty:
        print("No trades found in CSV.")
        return

    # Calculate metrics
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

    # Margin approximation: Option buying premium margin = Lot Size (65) * Entry Premium (~1% of Spot / 2 ~ 120-150 pts)
    # Typical NIFTY weekly 1% OTM option premium is ~0.5% - 0.7% of index (approx 120-180 points).
    # For realistic representation, we estimate Option Entry Premium ~ Spot * 0.006 (around 140 pts)
    df["est_entry_prem"] = (df["entry_spot"] * 0.006).round(1)
    df["margin_used"] = (df["est_entry_prem"] * 65).round(2)
    df["est_exit_prem"] = (df["est_entry_prem"] + df["opt_points"]).round(1)

    avg_margin_per_trade = df["margin_used"].mean()

    # Cumulative PnL for chart
    df["cum_pnl"] = df["pnl"].cumsum()
    df["peak"] = df["cum_pnl"].cummax()
    df["drawdown"] = df["cum_pnl"] - df["peak"]
    max_drawdown = abs(df["drawdown"].min())

    chart_labels = df["date"].tolist()
    chart_data = df["cum_pnl"].round(2).tolist()

    # Monthly breakdown
    df["month"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m")
    monthly_stats = df.groupby("month").agg(
        trades=("pnl", "count"),
        wins=("pnl", lambda x: (x > 0).sum()),
        net_pnl=("pnl", "sum"),
    ).reset_index()
    monthly_stats["win_rate"] = (monthly_stats["wins"] / monthly_stats["trades"] * 100).round(1)

    # Build Trade rows HTML
    trade_rows_html = []
    for i, row in df.iterrows():
        pnl = row["pnl"]
        pnl_class = "profit" if pnl > 0 else "loss"
        pnl_badge = f'<span class="badge {pnl_class}">₹{pnl:+,.2f}</span>'
        type_badge = f'<span class="badge badge-{row["option_type"].lower()}">{row["option_type"]}</span>'
        
        # Extract strike
        strike_val = row.get("strike")
        if pd.isna(strike_val):
            # Parse from symbol if needed e.g. NIFTY12NOV2622750PE -> 22750
            sym = str(row["symbol"])
            strike_str = sym[-7:-2] if len(sym) >= 7 else "ATM"
        else:
            strike_str = str(strike_val)

        entry_time_str = str(row["entry_time"]).split(" ")[-1][:5]
        exit_time_str = str(row["exit_time"]).split(" ")[-1][:5]

        row_html = f"""
        <tr>
            <td><strong>#{i+1}</strong></td>
            <td>{row['date']}</td>
            <td><code>{entry_time_str}</code></td>
            <td><code>{exit_time_str}</code></td>
            <td>{type_badge}</td>
            <td><strong>{row['symbol']}</strong></td>
            <td>₹{row['entry_spot']:,.1f}</td>
            <td>₹{row['est_entry_prem']:,.1f}</td>
            <td>₹{row['est_exit_prem']:,.1f}</td>
            <td>₹{row['margin_used']:,.2f}</td>
            <td><strong>{pnl_badge}</strong></td>
            <td><span class="reason-tag">{row['exit_reason']}</span></td>
        </tr>
        """
        trade_rows_html.append(row_html)

    monthly_rows_html = []
    for _, m in monthly_stats.iterrows():
        pnl_class = "profit" if m["net_pnl"] > 0 else "loss"
        m_html = f"""
        <tr>
            <td><strong>{m['month']}</strong></td>
            <td>{m['trades']}</td>
            <td>{m['win_rate']}%</td>
            <td class="{pnl_class}"><strong>₹{m['net_pnl']:+,.2f}</strong></td>
        </tr>
        """
        monthly_rows_html.append(m_html)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NIFTY Options Buying V9 — Backtest Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --border: #334155;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #38bdf8;
            --green: #10b981;
            --red: #ef4444;
            --amber: #f59e0b;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background: var(--bg); color: var(--text); padding: 24px; line-height: 1.5; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        
        /* Header */
        header {{ display: flex; justify-content: space-between; align-items: center; padding-bottom: 24px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }}
        h1 {{ font-size: 26px; font-weight: 700; color: #fff; }}
        .subtitle {{ font-size: 14px; color: var(--text-muted); margin-top: 4px; }}
        .header-tag {{ background: rgba(56, 189, 248, 0.15); color: var(--primary); padding: 6px 14px; border-radius: 9999px; font-size: 13px; font-weight: 600; border: 1px solid rgba(56, 189, 248, 0.3); }}

        /* KPI Cards */
        .grid-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px; }}
        .card-label {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }}
        .card-val {{ font-size: 24px; font-weight: 700; margin-top: 8px; }}
        .profit {{ color: var(--green); }}
        .loss {{ color: var(--red); }}

        /* Chart & Monthly Section */
        .analytics-row {{ display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 24px; }}
        @media (max-width: 900px) {{ .analytics-row {{ grid-template-columns: 1fr; }} }}
        .chart-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
        .chart-title {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; }}

        /* Tables */
        .table-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 13.5px; }}
        th {{ background: #131d2e; padding: 14px 16px; font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }}
        td {{ padding: 12px 16px; border-bottom: 1px solid rgba(51, 65, 85, 0.4); }}
        tr:hover {{ background: rgba(56, 189, 248, 0.04); }}
        code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; color: #cbd5e1; background: rgba(0,0,0,0.25); padding: 2px 6px; border-radius: 4px; }}

        /* Badges */
        .badge {{ padding: 3px 8px; border-radius: 6px; font-size: 12px; font-weight: 600; display: inline-block; }}
        .badge.profit {{ background: rgba(16, 185, 129, 0.15); color: #34d399; }}
        .badge.loss {{ background: rgba(239, 68, 68, 0.15); color: #f87171; }}
        .badge-ce {{ background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }}
        .badge-pe {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }}
        .reason-tag {{ font-size: 11.5px; color: #94a3b8; background: #0f172a; padding: 2px 8px; border-radius: 4px; border: 1px solid #334155; }}
        .search-box {{ margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }}
        .search-input {{ background: var(--surface); border: 1px solid var(--border); color: #fff; padding: 8px 14px; border-radius: 8px; font-size: 13px; width: 260px; }}
    </style>
</head>
<body>
<div class="container">
    <header>
        <div>
            <h1>📊 NIFTY Options Buying Strategy v9</h1>
            <div class="subtitle">Backtest Ledger & Performance Report • 2026-01-01 to 2026-11-06 (3-min timeframe, 65 qty)</div>
        </div>
        <div class="header-tag">Strategy: NIFTY_V9</div>
    </header>

    <!-- KPI Dashboard -->
    <div class="grid-cards">
        <div class="card">
            <div class="card-label">Net Profit / Loss</div>
            <div class="card-val profit">₹{total_pnl:+,.2f}</div>
        </div>
        <div class="card">
            <div class="card-label">Win Rate</div>
            <div class="card-val">{win_rate:.1f}% <span style="font-size:14px; color:var(--text-muted);">({winning_trades}W / {losing_trades}L)</span></div>
        </div>
        <div class="card">
            <div class="card-label">Profit Factor</div>
            <div class="card-val">{profit_factor}</div>
        </div>
        <div class="card">
            <div class="card-label">Max Drawdown</div>
            <div class="card-val loss">₹{max_drawdown:,.2f}</div>
        </div>
        <div class="card">
            <div class="card-label">Avg Margin / Trade</div>
            <div class="card-val">₹{avg_margin_per_trade:,.2f}</div>
        </div>
        <div class="card">
            <div class="card-label">Max Win / Max Loss</div>
            <div class="card-val" style="font-size:17px; margin-top:12px;">
                <span class="profit">₹{max_win:+,.0f}</span> / <span class="loss">₹{max_loss:+,.0f}</span>
            </div>
        </div>
    </div>

    <!-- Analytics Chart & Monthly Table -->
    <div class="analytics-row">
        <div class="chart-box">
            <div class="chart-title">📈 Cumulative Equity Curve (₹)</div>
            <canvas id="equityChart" height="110"></canvas>
        </div>
        <div class="chart-box" style="overflow-y:auto; max-height: 380px;">
            <div class="chart-title">📅 Monthly Performance</div>
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

    <!-- Trade Ledger -->
    <div class="search-box">
        <div class="chart-title" style="margin-bottom:0;">📑 Complete Trade Ledger ({total_trades} Trades)</div>
        <input type="text" id="searchInput" class="search-input" placeholder="🔍 Filter by date, CE, PE, strike..." onkeyup="filterTable()">
    </div>
    <div class="table-box">
        <table id="tradesTable">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Date</th>
                    <th>Entry Time</th>
                    <th>Exit Time</th>
                    <th>Type</th>
                    <th>Selected Contract</th>
                    <th>Spot Price</th>
                    <th>Est. Entry Prem</th>
                    <th>Est. Exit Prem</th>
                    <th>Margin Used</th>
                    <th>Net P&L</th>
                    <th>Exit Reason</th>
                </tr>
            </thead>
            <tbody>
                {"".join(trade_rows_html)}
            </tbody>
        </table>
    </div>
</div>

<script>
    // Equity Curve Chart
    const ctx = document.getElementById('equityChart').getContext('2d');
    new Chart(ctx, {{
        type: 'line',
        data: {{
            labels: {json.dumps(chart_labels)},
            datasets: [{{
                label: 'Cumulative P&L (₹)',
                data: {json.dumps(chart_data)},
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
                y: {{ grid: {{ color: 'rgba(51, 65, 85, 0.4)' }}, ticks: {{ color: '#94a3b8' }} }}
            }}
        }}
    }});

    // Table Filter
    function filterTable() {{
        const input = document.getElementById("searchInput").value.toUpperCase();
        const rows = document.getElementById("tradesTable").getElementsByTagName("tbody")[0].getElementsByTagName("tr");
        for (let i = 0; i < rows.length; i++) {{
            const text = rows[i].textContent || rows[i].innerText;
            rows[i].style.display = text.toUpperCase().indexOf(input) > -1 ? "" : "none";
        }}
    }}
</script>
</body>
</html>
"""

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\nHTML Report successfully generated at:\n  file:///{os.path.abspath(HTML_PATH)}")


if __name__ == "__main__":
    generate_report()

