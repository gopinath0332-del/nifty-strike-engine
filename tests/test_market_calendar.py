"""
tests/test_market_calendar.py
=============================
Unit tests for NSE trading holidays, market calendar, and holiday alerts.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from src.market_calendar import (
    get_holiday_name,
    get_next_trading_day,
    is_market_holiday,
    is_market_hours,
    is_trading_day,
    is_weekend,
)
from src.discord_notifier import DiscordNotifier
from src.session_manager import SessionManager
from config.settings import Settings

IST = timezone(timedelta(hours=5, minutes=30))


def _mock_settings() -> Settings:
    import os
    os.environ["KITE_API_KEY"] = "mock_key"
    os.environ["KITE_API_SECRET"] = "mock_secret"
    os.environ["KITE_ACCESS_TOKEN"] = "mock_token"
    os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/test"
    return Settings.load("config/settings.yml")


def test_is_weekend():
    # Saturday
    assert is_weekend(datetime(2026, 11, 7, 12, 0, tzinfo=IST)) is True
    # Sunday
    assert is_weekend(datetime(2026, 11, 8, 12, 0, tzinfo=IST)) is True
    # Friday
    assert is_weekend(datetime(2026, 11, 6, 12, 0, tzinfo=IST)) is False
    # Monday
    assert is_weekend(datetime(2026, 11, 9, 12, 0, tzinfo=IST)) is False


def test_is_market_holiday():
    # 2026-11-24 is Guru Nanak Jayanti
    guru_nanak = datetime(2026, 11, 24, 10, 0, tzinfo=IST)
    assert is_market_holiday(guru_nanak) is True
    assert "Guru Nanak" in (get_holiday_name(guru_nanak) or "")

    # 2026-01-26 is Republic Day
    republic_day = date(2026, 1, 26)
    assert is_market_holiday(republic_day) is True
    assert get_holiday_name(republic_day) == "Republic Day"

    # 2026-11-09 is a regular trading day
    normal_day = date(2026, 11, 9)
    assert is_market_holiday(normal_day) is False
    assert get_holiday_name(normal_day) is None


def test_custom_holidays_override():
    custom = [{"date": "2026-12-01", "name": "Custom Exchange Closure"}]
    target = date(2026, 12, 1)

    assert is_market_holiday(target) is False
    assert is_market_holiday(target, custom_holidays=custom) is True
    assert get_holiday_name(target, custom_holidays=custom) == "Custom Exchange Closure"

    # Dict format
    custom_dict = {"2026-12-02": "State Election Day"}
    assert is_market_holiday("2026-12-02", custom_holidays=custom_dict) is True
    assert get_holiday_name("2026-12-02", custom_holidays=custom_dict) == "State Election Day"


def test_is_trading_day():
    # Regular trading day
    assert is_trading_day(datetime(2026, 11, 9, 10, 0, tzinfo=IST)) is True

    # Weekend
    assert is_trading_day(datetime(2026, 11, 7, 10, 0, tzinfo=IST)) is False

    # Holiday (Tuesday 2026-11-24)
    assert is_trading_day(datetime(2026, 11, 24, 10, 0, tzinfo=IST)) is False


def test_get_next_trading_day():
    # Monday 2026-11-23 -> next trading day is Wednesday 2026-11-25 (since Tue 24 is holiday)
    mon = date(2026, 11, 23)
    next_day = get_next_trading_day(mon)
    assert next_day == date(2026, 11, 25)

    # Friday 2026-11-06 -> next trading day is Monday 2026-11-09
    fri = date(2026, 11, 6)
    assert get_next_trading_day(fri) == date(2026, 11, 9)

    # Friday 2026-01-23 -> Monday 2026-01-26 is Republic Day -> Tuesday 2026-01-27
    fri_rep = date(2026, 1, 23)
    assert get_next_trading_day(fri_rep) == date(2026, 1, 27)


def test_is_market_hours_with_holidays():
    # Regular day active trading hours (10:00 AM)
    reg_open = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    assert is_market_hours(reg_open) is True

    # Holiday at 10:00 AM (2026-11-24 Guru Nanak Jayanti) -> MUST be False
    hol_open = datetime(2026, 11, 24, 10, 0, tzinfo=IST)
    assert is_market_hours(hol_open) is False

    # Weekend at 10:00 AM -> False
    wknd_open = datetime(2026, 11, 8, 10, 0, tzinfo=IST)
    assert is_market_hours(wknd_open) is False

    # Pre-market on regular day (08:50 AM) -> False
    pre_mkt = datetime(2026, 11, 9, 8, 50, tzinfo=IST)
    assert is_market_hours(pre_mkt) is False

    # Post-market on regular day (15:35 PM) -> False
    post_mkt = datetime(2026, 11, 9, 15, 35, tzinfo=IST)
    assert is_market_hours(post_mkt) is False


def test_discord_notifier_market_holiday():
    notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test")
    notifier._send = MagicMock()

    notifier.market_holiday(
        holiday_name="Prakash Gurpurb Sri Guru Nanak Dev (Guru Nanak Jayanti)",
        date_str="2026-11-24",
        day_name="Tuesday",
        next_session="Wednesday, 25 Nov 2026",
    )

    notifier._send.assert_called_once()
    kwargs = notifier._send.call_args[1]
    assert "Market Holiday" in kwargs["title"]
    field_dict = {f[0]: f[1] for f in kwargs["fields"]}
    assert "Guru Nanak" in field_dict["Holiday / Occasion"]
    assert "2026-11-24" in field_dict["Date"]
    assert "Wednesday, 25 Nov 2026" in field_dict["Next Trading Session"]


def test_session_manager_run_on_holiday_triggers_alert(monkeypatch):
    mock_kite = MagicMock()
    mock_notifier = MagicMock()
    mock_state = MagicMock()
    mock_state.reconcile.return_value = {"status": "CLEAN"}

    sm = SessionManager(
        settings=_mock_settings(),
        kite_client=mock_kite,
        notifier=mock_notifier,
        state_manager=mock_state,
    )

    # Mock datetime to Tuesday 2026-11-24 09:15 AM (Guru Nanak Jayanti)
    holiday_now = datetime(2026, 11, 24, 9, 15, 0, tzinfo=IST)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return holiday_now

    monkeypatch.setattr("src.session_manager.datetime", MockDatetime)

    # Prevent _standby_loop from actually looping
    mock_standby = MagicMock()
    monkeypatch.setattr(sm, "_standby_loop", mock_standby)
    mock_active = MagicMock()
    monkeypatch.setattr(sm, "_start_active_session", mock_active)

    sm.run()

    # Verify market_holiday alert was sent to Discord
    mock_notifier.market_holiday.assert_called_once()
    call_kwargs = mock_notifier.market_holiday.call_args[1]
    assert "Guru Nanak" in call_kwargs["holiday_name"]
    assert call_kwargs["date_str"] == "2026-11-24"
    assert call_kwargs["day_name"] == "Tuesday"

    # Verify active session was NOT started, and standby loop was entered
    mock_active.assert_not_called()
    mock_standby.assert_called_once()


def test_sync_nse_holidays_success(tmp_path, monkeypatch):
    from src.market_calendar import sync_nse_holidays
    import src.market_calendar as mc

    cache_file = str(tmp_path / "test_cache.json")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "FO": [
            {"tradingDate": "15-Jan-2026", "description": "Municipal Corporation Election - Maharashtra"},
            {"tradingDate": "24-Nov-2026", "description": "Prakash Gurpurb Sri Guru Nanak Dev"},
        ],
        "CM": [],
    }

    monkeypatch.setattr("src.market_calendar.requests.get", lambda *args, **kwargs: mock_resp)
    # Reset in-memory cache
    mc._MEM_CACHE = None
    mc._LAST_MEM_SYNC = 0.0

    synced = sync_nse_holidays(cache_path=cache_file, force=True)

    assert "2026-01-15" in synced
    assert "2026-11-24" in synced
    assert synced["2026-01-15"] == "Municipal Corporation Election - Maharashtra"
    assert synced["2026-11-24"] == "Prakash Gurpurb Sri Guru Nanak Dev"
    import os
    assert os.path.exists(cache_file)


def test_sync_nse_holidays_network_failure_falls_back_to_cache(tmp_path, monkeypatch):
    from src.market_calendar import sync_nse_holidays
    import src.market_calendar as mc
    import json

    cache_file = str(tmp_path / "test_cache.json")
    # Pre-populate disk cache
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"last_synced": "2026-11-24T00:00:00", "holidays": {"2026-05-20": "General Election"}}, f)

    # Force network call to fail
    def mock_fail(*args, **kwargs):
        raise ConnectionError("Network unreachable")

    monkeypatch.setattr("src.market_calendar.requests.get", mock_fail)
    mc._MEM_CACHE = None
    mc._LAST_MEM_SYNC = 0.0

    synced = sync_nse_holidays(cache_path=cache_file, force=True)
    assert "2026-05-20" in synced
    assert synced["2026-05-20"] == "General Election"

