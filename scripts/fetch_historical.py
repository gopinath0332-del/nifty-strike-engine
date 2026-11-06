"""
scripts/fetch_historical.py
===========================
Fetches NIFTY 50 3-minute historical data in chunks (up to Zerodha's limit per request)
from 2026-01-01 to current date and saves to data/nifty_3min_2026.csv.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime, timedelta

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

import pandas as pd
from dotenv import dotenv_values

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.kite_auth import build_authenticated_kite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fetch_historical")

NIFTY_TOKEN = 256265  # NSE NIFTY 50 index token
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "nifty_3min_2026.csv")


def fetch_all_candles(
    start_date: date = date(2025, 12, 20),
    end_date: date | None = None,
    interval: str = "3minute",
) -> pd.DataFrame:
    if end_date is None:
        end_date = datetime.now(tz=IST).date()

    cfg = dotenv_values(".env")
    api_key = cfg.get("KITE_API_KEY", "")
    access_token = cfg.get("KITE_ACCESS_TOKEN", "")

    if not api_key or not access_token:
        raise ValueError("Missing KITE_API_KEY or KITE_ACCESS_TOKEN in .env")

    kite = build_authenticated_kite(api_key, access_token)
    logger.info("Fetching NIFTY %s historical candles from %s to %s...", interval, start_date, end_date)

    all_records = []
    current_start = start_date
    chunk_days = 60  # fetch in 60-day blocks to stay well within Kite limits

    while current_start <= end_date:
        current_end = min(current_start + timedelta(days=chunk_days), end_date)
        dt_from = datetime.combine(current_start, datetime.min.time().replace(hour=9, minute=15), tzinfo=IST)
        dt_to = datetime.combine(current_end, datetime.min.time().replace(hour=15, minute=30), tzinfo=IST)

        logger.info("  Downloading chunk: %s to %s...", current_start, current_end)
        try:
            records = kite.historical_data(
                instrument_token=NIFTY_TOKEN,
                from_date=dt_from,
                to_date=dt_to,
                interval=interval,
                continuous=False,
                oi=False,
            )
            if records:
                all_records.extend(records)
                logger.info("  Retrieved %d candles in chunk (Total so far: %d)", len(records), len(all_records))
            time.sleep(0.35)  # respect rate limits
        except Exception as exc:
            logger.error("Error fetching chunk %s to %s: %s", current_start, current_end, exc)

        current_start = current_end + timedelta(days=1)

    if not all_records:
        logger.warning("No candle data returned.")
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["date_only"] = df["date"].dt.date

    out_file = os.path.join(os.path.dirname(__file__), "..", "data", f"nifty_{interval}_2026.csv")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    df.to_csv(out_file, index=False)
    logger.info("Saved %d total candles to %s", len(df), out_file)
    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", default="3minute", choices=["3minute", "5minute", "minute", "15minute"])
    args = parser.parse_args()
    fetch_all_candles(interval=args.interval)

