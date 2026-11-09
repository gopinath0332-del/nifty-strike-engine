"""
tests/test_discord_notifier.py
==============================
Unit tests for asynchronous DiscordNotifier worker and queue.
"""

from unittest.mock import MagicMock, patch
import time
from datetime import datetime, timezone, timedelta
import pytest

from src.discord_notifier import DiscordNotifier

IST = timezone(timedelta(hours=5, minutes=30))


def test_notifier_empty_url_is_noop():
    notifier = DiscordNotifier(webhook_url="")
    # Should not start worker thread
    assert notifier._worker_thread is None
    # Method calls should be immediate no-ops
    notifier.info("Test message")
    notifier.flush()
    notifier.stop()


def test_notifier_delivers_asynchronously():
    mock_resp = MagicMock()
    mock_resp.status_code = 204

    with patch("requests.Session.post", return_value=mock_resp) as mock_post:
        notifier = DiscordNotifier(
            webhook_url="https://discord.com/api/webhooks/mock/test",
            live_order_placement=False,
        )

        t_start = time.perf_counter()
        notifier.info("Hello async world!")
        elapsed = time.perf_counter() - t_start

        # Enqueuing must return immediately (< 50ms)
        assert elapsed < 0.05

        # Flush to wait for background thread to post
        notifier.flush(timeout=2.0)
        notifier.stop(timeout=1.0)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert "embeds" in call_kwargs["json"]
        assert call_kwargs["json"]["embeds"][0]["title"] == "ℹ️ System Notification"


def test_notifier_handles_http_error_gracefully():
    # If requests.post raises an exception, the caller is unaffected
    with patch("requests.Session.post", side_effect=RuntimeError("Connection reset by peer")):
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/mock/test")

        notifier.error("Something went wrong")
        notifier.flush(timeout=2.0)
        notifier.stop(timeout=1.0)
        # Background worker caught exception, caller thread does not crash


def test_notifier_rate_limit_backoff():
    rate_limit_resp = MagicMock()
    rate_limit_resp.status_code = 429
    rate_limit_resp.json.return_value = {"retry_after": 0.05}

    success_resp = MagicMock()
    success_resp.status_code = 200

    with patch("requests.Session.post", side_effect=[rate_limit_resp, success_resp]) as mock_post:
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/mock/test")

        notifier.info("Rate limit test")
        notifier.flush(timeout=2.0)
        notifier.stop(timeout=1.0)

        # Worker retried after backoff
        assert mock_post.call_count == 2


def test_morning_briefing_embed_format():
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.Session.post", return_value=mock_resp) as mock_post:
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/mock/test")

        notifier.morning_briefing(
            user_id="tql221",
            mode="SIMULATION",
            lot_size=65,
            timeframe=5,
            entry_cutoff="14:45",
            eod_exit="15:00",
            available_cash=125000.50,
            prev_close=24500.0,
            pivot=24520.0,
            r1=24600.0,
            s1=24450.0,
        )

        notifier.flush(timeout=2.0)
        notifier.stop(timeout=1.0)

        mock_post.assert_called_once()
        embed = mock_post.call_args[1]["json"]["embeds"][0]
        assert embed["title"] == "🛡️ Nifty Strike Engine — System Armed & Ready"
        field_dict = {f["name"]: f["value"] for f in embed["fields"]}
        assert "tql221" in field_dict["Broker Auth"]
        assert "125,000.50" in field_dict["Cash Available"]
        assert "24,520.00" in field_dict["Daily Pivots"]
        assert "5m Candle Builder Armed" in field_dict["Execution Parameters"]


def test_eod_summary_embed_format():
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.Session.post", return_value=mock_resp) as mock_post:
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/mock/test")

        notifier.eod_summary(
            date_str="2026-11-09",
            signals_evaluated=75,
            trades_taken_str="0 (No breakout met all 4 filter criteria)",
            day_range_str="₹24,520.00 – ₹24,780.00",
            next_session_str="tomorrow 08:50 AM IST",
        )

        notifier.flush(timeout=2.0)
        notifier.stop(timeout=1.0)

        mock_post.assert_called_once()
        embed = mock_post.call_args[1]["json"]["embeds"][0]
        assert embed["title"] == "📊 Nifty Strike Engine — EOD Session Summary"
        field_dict = {f["name"]: f["value"] for f in embed["fields"]}
        assert field_dict["Date"] == "2026-11-09"
        assert field_dict["Market Status"] == "Closed (15:30 IST)"
        assert field_dict["Signals Evaluated"] == "75 candles"
        assert "No breakout met all 4 filter criteria" in field_dict["Trades Taken"]
        assert "₹24,520.00 – ₹24,780.00" in field_dict["NIFTY Day Range"]
        assert "tomorrow 08:50 AM IST" in field_dict["Bot Status"]


def test_auth_success_embed_format():
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.Session.post", return_value=mock_resp) as mock_post:
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/mock/test")

        notifier.auth_success(
            user_id="TQL221",
            message="Daily access token generated and saved to .env",
            service_status="Restarting nifty-strike-engine service",
        )

        notifier.flush(timeout=2.0)
        notifier.stop(timeout=1.0)

        mock_post.assert_called_once()
        embed = mock_post.call_args[1]["json"]["embeds"][0]
        assert embed["title"] == "🔑 Zerodha Morning Authentication Successful"
        field_dict = {f["name"]: f["value"] for f in embed["fields"]}
        assert "TQL221" in field_dict["Broker User ID"]
        assert "Success ✅" in field_dict["Auth Status"]
        assert "Daily access token generated and saved to .env" in field_dict["Token Storage"]
        assert "Restarting nifty-strike-engine service" in field_dict["Engine Action"]


def test_auth_failure_embed_format():
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.Session.post", return_value=mock_resp) as mock_post:
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/mock/test")

        notifier.auth_failure(
            user_id="TQL221",
            error="Step 1 (Credentials) rejected by Kite: Invalid password",
        )

        notifier.flush(timeout=2.0)
        notifier.stop(timeout=1.0)

        mock_post.assert_called_once()
        embed = mock_post.call_args[1]["json"]["embeds"][0]
        assert embed["title"] == "❌ Zerodha Morning Authentication Failed"
        field_dict = {f["name"]: f["value"] for f in embed["fields"]}
        assert "TQL221" in field_dict["Broker User ID"]
        assert "Failed ❌" in field_dict["Auth Status"]
        assert "Invalid password" in field_dict["Error Details"]


