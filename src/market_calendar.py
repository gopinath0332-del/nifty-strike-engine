"""
src/market_calendar.py
======================
NSE Trading Holidays Calendar & Market Hours Utilities.

Provides:
  - Official National Stock Exchange (NSE) trading holiday schedules.
  - Dynamic synchronization with live NSE API (https://www.nseindia.com/api/holiday-master?type=trading)
    to automatically catch intermittent and newly announced exchange holidays.
  - Robust offline fallback (local disk cache + built-in holiday calendar).
  - Custom holiday overrides via config/settings.yml.
  - Weekend detection, next trading session calculation, and market hours checks.
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from datetime import date, datetime, time, timedelta
from typing import Any

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

import requests

logger = logging.getLogger(__name__)

NSE_HOLIDAYS_API_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
NSE_CACHE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "nse_holidays_cache.json")
)

# In-memory fast cache to avoid disk reads during high-frequency checks
_MEM_CACHE: dict[str, str] | None = None
_LAST_MEM_SYNC: float = 0.0
_MEM_CACHE_TTL_SEC: float = 3600.0  # 1 hour in-memory TTL

# ==============================================================================
# Baseline Built-in NSE Trading Holidays (Equities & F&O Segments)
# Used as offline fallback when live NSE API and local cache are unavailable.
# ==============================================================================
NSE_OFFICIAL_HOLIDAYS: dict[str, str] = {
    # 2024
    "2024-01-22": "Special Holiday (Ayodhya Pran Pratishtha)",
    "2024-01-26": "Republic Day",
    "2024-03-08": "Mahashivratri",
    "2024-03-25": "Holi",
    "2024-03-29": "Good Friday",
    "2024-04-11": "Id-Ul-Fitr (Ramadan Eid)",
    "2024-04-17": "Shri Ram Navami",
    "2024-05-01": "Maharashtra Day",
    "2024-05-20": "General Parliamentary Elections (Mumbai)",
    "2024-06-17": "Bakri Id",
    "2024-07-17": "Muharram",
    "2024-08-15": "Independence Day / Parsi New Year",
    "2024-10-02": "Mahatma Gandhi Jayanti",
    "2024-11-01": "Diwali Laxmi Pujan (Muhurat Trading Day)",
    "2024-11-15": "Prakash Gurpurb Sri Guru Nanak Dev (Guru Nanak Jayanti)",
    "2024-11-20": "Maharashtra Assembly Elections",
    "2024-12-25": "Christmas",

    # 2025
    "2025-02-26": "Mahashivratri",
    "2025-03-14": "Holi",
    "2025-03-31": "Id-Ul-Fitr (Ramadan Eid)",
    "2025-04-10": "Shri Mahavir Jayanti",
    "2025-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2025-04-18": "Good Friday",
    "2025-05-01": "Maharashtra Day",
    "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi",
    "2025-10-02": "Mahatma Gandhi Jayanti / Dussehra",
    "2025-10-21": "Diwali Laxmi Pujan (Muhurat Trading Day)",
    "2025-10-22": "Diwali-Balipratipada",
    "2025-11-05": "Prakash Gurpurb Sri Guru Nanak Dev (Guru Nanak Jayanti)",
    "2025-12-25": "Christmas",

    # 2026
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat Trading Day)",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev (Guru Nanak Jayanti)",
    "2026-12-25": "Christmas",

    # 2027 (Gazetted / standard calendar)
    "2027-01-26": "Republic Day",
    "2027-03-22": "Holi",
    "2027-03-26": "Good Friday",
    "2027-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2027-05-01": "Maharashtra Day",
    "2027-08-15": "Independence Day",
    "2027-10-02": "Mahatma Gandhi Jayanti",
    "2027-11-14": "Prakash Gurpurb Sri Guru Nanak Dev (Guru Nanak Jayanti)",
    "2027-12-25": "Christmas",
}


def _normalize_date_str(d: datetime | date | str) -> str:
    """Converts a datetime, date, or date string into YYYY-MM-DD string."""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, str):
        return d.strip()
    raise TypeError(f"Unsupported date type: {type(d)}")


def _parse_custom_holidays(custom_holidays: Any) -> dict[str, str]:
    """
    Parses custom holidays provided in various forms:
      - dict: {"2026-11-24": "Guru Nanak Jayanti"}
      - list of dicts: [{"date": "2026-11-24", "name": "Guru Nanak Jayanti"}]
      - list of strings: ["2026-11-24"]
    """
    out: dict[str, str] = {}
    if not custom_holidays:
        return out

    if isinstance(custom_holidays, dict):
        for k, v in custom_holidays.items():
            out[_normalize_date_str(k)] = str(v)
    elif isinstance(custom_holidays, list):
        for item in custom_holidays:
            if isinstance(item, dict):
                d = item.get("date")
                n = item.get("name") or item.get("description") or "Exchange Holiday"
                if d:
                    out[_normalize_date_str(d)] = str(n)
            elif isinstance(item, (str, date, datetime)):
                out[_normalize_date_str(item)] = "Exchange Holiday"
    return out


# ==============================================================================
# Live NSE API Synchronization & Local Caching
# ==============================================================================

def sync_nse_holidays(
    cache_path: str = NSE_CACHE_FILE,
    max_age_hours: float = 24.0,
    force: bool = False,
) -> dict[str, str]:
    """
    Synchronizes trading holidays directly with the official NSE API.
    Caches results locally to avoid unnecessary API requests.

    Parameters
    ----------
    cache_path : str
        Path to local JSON cache file.
    max_age_hours : float
        Maximum age of cache before attempting refresh (default: 24h).
    force : bool
        If True, ignores cache and forces a live network fetch.

    Returns
    -------
    dict[str, str]
        Mapping of 'YYYY-MM-DD': 'Holiday Description'.
    """
    global _MEM_CACHE, _LAST_MEM_SYNC
    now_epoch = _time.time()

    # Check in-memory cache first if not forced
    if not force and _MEM_CACHE is not None and (now_epoch - _LAST_MEM_SYNC) < _MEM_CACHE_TTL_SEC:
        return _MEM_CACHE

    # Check local disk cache validity
    cache_data: dict[str, Any] = {}
    is_cache_fresh = False
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            file_mtime = os.path.getmtime(cache_path)
            if (now_epoch - file_mtime) < (max_age_hours * 3600.0):
                is_cache_fresh = True
        except Exception as exc:
            logger.debug("Could not read NSE holiday cache at %s: %s", cache_path, exc)

    if is_cache_fresh and not force and "holidays" in cache_data:
        holidays_map = cache_data["holidays"]
        _MEM_CACHE = holidays_map
        _LAST_MEM_SYNC = now_epoch
        return holidays_map

    # Attempt live fetch from NSE official API
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        logger.info("Syncing trading holidays directly from official NSE API...")
        resp = requests.get(NSE_HOLIDAYS_API_URL, headers=headers, timeout=6.0)
        if resp.status_code == 200:
            data = resp.json()
            synced: dict[str, str] = {}
            # Inspect both F&O and Capital Market (Equities) segments
            for segment in ("FO", "CM"):
                for item in data.get(segment, []):
                    raw_date = item.get("tradingDate")
                    desc = item.get("description", "NSE Trading Holiday").strip()
                    if raw_date:
                        try:
                            iso_date = datetime.strptime(raw_date.strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
                            synced[iso_date] = desc
                        except Exception:
                            pass

            if synced:
                # Save to disk cache
                try:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    payload = {
                        "last_synced": datetime.now(tz=IST).isoformat(),
                        "holidays": synced,
                    }
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=2)
                except Exception as save_err:
                    logger.debug("Failed to write holiday cache to %s: %s", cache_path, save_err)

                _MEM_CACHE = synced
                _LAST_MEM_SYNC = now_epoch
                logger.info("Successfully synced %d holidays from NSE live API.", len(synced))
                return synced

    except Exception as exc:
        logger.warning(
            "Could not reach live NSE holiday API (%s). Falling back to cached / built-in schedule.",
            exc,
        )

    # Fallback 1: Disk cache (even if older than max_age_hours)
    if "holidays" in cache_data and isinstance(cache_data["holidays"], dict):
        logger.info("Using existing local holiday cache (%d entries).", len(cache_data["holidays"]))
        _MEM_CACHE = cache_data["holidays"]
        _LAST_MEM_SYNC = now_epoch
        return cache_data["holidays"]

    # Fallback 2: Empty dict (get_all_holidays will fall back to NSE_OFFICIAL_HOLIDAYS)
    return {}


def get_all_holidays(
    custom_holidays: Any = None,
    auto_sync: bool = True,
) -> dict[str, str]:
    """
    Returns the unified dictionary of all market holidays:
      1. Baseline: Built-in NSE_OFFICIAL_HOLIDAYS (offline resilience).
      2. Live Sync: Synced from official NSE API or local disk cache.
      3. Custom: User overrides from config/settings.yml (highest precedence).
    """
    # 1. Baseline
    combined = dict(NSE_OFFICIAL_HOLIDAYS)

    # 2. Live dynamic sync from NSE (or cache)
    if auto_sync:
        try:
            synced = sync_nse_holidays()
            combined.update(synced)
        except Exception as exc:
            logger.debug("Dynamic NSE sync skipped: %s", exc)

    # 3. User custom holidays override
    if custom_holidays:
        custom_map = _parse_custom_holidays(custom_holidays)
        combined.update(custom_map)

    return combined


# ==============================================================================
# Public Calendar & Market Hours Functions
# ==============================================================================

def is_weekend(dt: datetime | date) -> bool:
    """Returns True if dt (in IST) falls on Saturday (5) or Sunday (6)."""
    return dt.weekday() >= 5


def is_market_holiday(
    dt: datetime | date | str,
    custom_holidays: Any = None,
    auto_sync: bool = True,
) -> bool:
    """
    Returns True if dt is an NSE trading holiday (synced live from NSE,
    built-in, or custom defined).
    """
    date_str = _normalize_date_str(dt)
    holidays_map = get_all_holidays(custom_holidays=custom_holidays, auto_sync=auto_sync)
    return date_str in holidays_map


def get_holiday_name(
    dt: datetime | date | str,
    custom_holidays: Any = None,
    auto_sync: bool = True,
) -> str | None:
    """
    Returns the holiday description for the given date, or None if not a holiday.
    """
    date_str = _normalize_date_str(dt)
    holidays_map = get_all_holidays(custom_holidays=custom_holidays, auto_sync=auto_sync)
    return holidays_map.get(date_str)


def is_trading_day(
    dt: datetime | date,
    custom_holidays: Any = None,
    auto_sync: bool = True,
) -> bool:
    """
    Returns True if dt is a valid NSE trading day (weekday and not a market holiday).
    """
    return not is_weekend(dt) and not is_market_holiday(dt, custom_holidays=custom_holidays, auto_sync=auto_sync)


def get_next_trading_day(
    dt: datetime | date,
    custom_holidays: Any = None,
    auto_sync: bool = True,
) -> date:
    """
    Returns the date of the next valid trading day following dt.
    """
    cur_date = dt.date() if isinstance(dt, datetime) else dt
    next_d = cur_date + timedelta(days=1)
    while not is_trading_day(next_d, custom_holidays=custom_holidays, auto_sync=auto_sync):
        next_d += timedelta(days=1)
    return next_d


def is_market_hours(
    dt: datetime,
    custom_holidays: Any = None,
    auto_sync: bool = True,
) -> bool:
    """
    Returns True if dt (in IST) is within active NSE trading hours.
    NSE regular market hours: 09:15 to 15:30 IST on trading days (not weekends, not holidays).
    We allow tick stream connection starting at 09:14 IST.
    """
    if is_weekend(dt) or is_market_holiday(dt, custom_holidays=custom_holidays, auto_sync=auto_sync):
        return False
    t = dt.time()
    return time(9, 14) <= t <= time(15, 30)
