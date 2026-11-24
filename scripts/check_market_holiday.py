"""
scripts/check_market_holiday.py
===============================
Utility script to check NSE market holiday status, display upcoming holidays,
or dispatch a market holiday alert to Discord.

Usage:
  # Check today's status
  python scripts/check_market_holiday.py

  # Check a specific date
  python scripts/check_market_holiday.py --date 2026-11-24

  # List all official holidays for a specific year
  python scripts/check_market_holiday.py --list 2026

  # Send Discord notification if today is a holiday (or force test alert)
  python scripts/check_market_holiday.py --notify
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

from dotenv import dotenv_values

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.discord_notifier import DiscordNotifier
from src.market_calendar import (
    NSE_OFFICIAL_HOLIDAYS,
    get_all_holidays,
    get_holiday_name,
    get_next_trading_day,
    is_market_holiday,
    is_trading_day,
    is_weekend,
    sync_nse_holidays,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check NSE market holiday status")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to check in YYYY-MM-DD format (default: today IST)",
    )
    parser.add_argument(
        "--list",
        type=int,
        default=None,
        metavar="YEAR",
        help="List all official NSE holidays for the given year (e.g. 2026)",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Force a live synchronization with official NSE API",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Dispatch market holiday alert to Discord if date is a holiday",
    )
    parser.add_argument(
        "--env",
        default=os.path.join(os.path.dirname(__file__), "..", ".env"),
        help="Path to .env file for Discord webhook",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.sync:
        print("\nConnecting to official NSE API (https://www.nseindia.com/api/holiday-master?type=trading)...")
        synced = sync_nse_holidays(force=True)
        print(f"✅ Successfully synced {len(synced)} trading holidays directly from NSE!")
        print("Local cache updated at: data/nse_holidays_cache.json\n")

    if args.list:
        year_str = str(args.list)
        all_holidays = get_all_holidays()
        holidays_in_year = {k: v for k, v in all_holidays.items() if k.startswith(year_str)}
        print(f"\n========================================================")
        print(f"  NSE Trading Holidays for Year {year_str}")
        print(f"========================================================")
        if not holidays_in_year:
            print(f"  No holidays recorded for {year_str}.")
        else:
            for d_str, name in sorted(holidays_in_year.items()):
                dt = datetime.strptime(d_str, "%Y-%m-%d")
                day_name = dt.strftime("%A")
                is_wknd = is_weekend(dt)
                wknd_tag = " [Weekend]" if is_wknd else ""
                print(f"  • {d_str} ({day_name:9s}): {name}{wknd_tag}")
        print(f"========================================================\n")
        return

    # Check specific date or today
    if args.date:
        try:
            target_dt = datetime.strptime(args.date.strip(), "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Expected YYYY-MM-DD.")
            sys.exit(1)
    else:
        target_dt = datetime.now(tz=IST)

    target_date_str = target_dt.strftime("%Y-%m-%d")
    day_name = target_dt.strftime("%A")
    holiday = is_market_holiday(target_dt)
    weekend = is_weekend(target_dt)
    trading_day = is_trading_day(target_dt)
    holiday_name = get_holiday_name(target_dt)
    next_trade_date = get_next_trading_day(target_dt)

    print(f"\n========================================================")
    print(f"  NSE Market Status Check")
    print(f"========================================================")
    print(f"  Date          : {target_date_str} ({day_name})")
    print(f"  Weekend       : {'YES' if weekend else 'NO'}")
    print(f"  Market Holiday: {'YES' if holiday else 'NO'}")
    if holiday:
        print(f"  Holiday Name  : {holiday_name}")
    print(f"  Is Trading Day: {'YES (Market Open)' if trading_day else 'NO (Market Closed)'}")
    print(f"  Next Session  : {next_trade_date.strftime('%Y-%m-%d (%A)')}")
    print(f"========================================================\n")

    if args.notify:
        cfg = dotenv_values(args.env)
        webhook_url = cfg.get("DISCORD_WEBHOOK_URL", "").strip()
        if not webhook_url:
            print("Notice: No DISCORD_WEBHOOK_URL found in .env, skipping notification.")
            return

        notifier = DiscordNotifier(webhook_url=webhook_url)
        if holiday or not trading_day:
            event_name = holiday_name or ("Weekend" if weekend else "Market Closed")
            notifier.market_holiday(
                holiday_name=event_name,
                date_str=target_date_str,
                day_name=day_name,
                next_session=next_trade_date.strftime("%A, %d %b %Y"),
            )
            notifier.stop(timeout=5.0)
            print(f"✅ Market holiday alert dispatched to Discord for {target_date_str} ({event_name}).")
        else:
            print("Today is a regular trading day. Notification not dispatched (pass a holiday date to test).")


if __name__ == "__main__":
    main()
