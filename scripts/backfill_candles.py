"""
scripts/backfill_candles.py
===========================
Fetches historical 3-minute OHLCV candles from Kite historical data API
and saves them to a CSV file for offline indicator testing and backtesting.

Usage
-----
    python scripts/backfill_candles.py --date 2025-01-15
    python scripts/backfill_candles.py --from 2025-01-01 --to 2025-01-31
    python scripts/backfill_candles.py --date 2025-01-15 --out data/candles.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

IST = pytz.timezone("Asia/Kolkata")
NIFTY_TOKEN = 256265   # NSE NIFTY 50 index instrument token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill NIFTY 3-min OHLCV candles")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="Single date (YYYY-MM-DD)")
    group.add_argument("--from", dest="date_from", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to",  dest="date_to",   help="End date (YYYY-MM-DD), used with --from")
    parser.add_argument(
        "--out",
        default="data/nifty_3min.csv",
        help="Output CSV path (default: data/nifty_3min.csv)",
    )
    return parser.parse_args()


def fetch_candles(
    kite,                        # KiteConnect instance
    from_dt: datetime,
    to_dt: datetime,
    interval: str = "3minute",
) -> pd.DataFrame:
    records = kite.historical_data(
        instrument_token=NIFTY_TOKEN,
        from_date=from_dt,
        to_date=to_dt,
        interval=interval,
        continuous=False,
        oi=False,
    )
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df["date_col"] = pd.to_datetime(df["date"]).dt.tz_convert(IST).dt.date
    return df


def main() -> None:
    args = parse_args()

    # Resolve date range
    if args.date:
        d_from = d_to = date.fromisoformat(args.date)
    else:
        d_from = date.fromisoformat(args.date_from)
        d_to   = date.fromisoformat(args.date_to) if args.date_to else d_from

    # Load credentials
    from dotenv import dotenv_values
    cfg         = dotenv_values(".env")
    api_key     = cfg.get("KITE_API_KEY", "")
    access_token = cfg.get("KITE_ACCESS_TOKEN", "")

    if not api_key or not access_token:
        print("[ERROR] KITE_API_KEY / KITE_ACCESS_TOKEN missing in .env")
        sys.exit(1)

    from src.kite_auth import build_authenticated_kite
    kite = build_authenticated_kite(api_key, access_token)

    from_dt = IST.localize(datetime.combine(d_from, datetime.min.time().replace(hour=9, minute=15)))
    to_dt   = IST.localize(datetime.combine(d_to,   datetime.min.time().replace(hour=15, minute=30)))

    print(f"Fetching NIFTY 3-min candles from {d_from} to {d_to}…")
    df = fetch_candles(kite, from_dt, to_dt)

    if df.empty:
        print("[WARNING] No data returned.")
        sys.exit(0)

    out_path = args.out
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"✅ {len(df)} candles saved to {out_path}")


if __name__ == "__main__":
    main()

