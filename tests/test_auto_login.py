"""
tests/test_auto_login.py
========================
Tests for auto_login module:
  - TOTP code generation format and length
  - Playwright automated login flow and error handling
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyotp
import pytest

from scripts.auto_login import perform_automated_login


def test_totp_generation_with_pyotp() -> None:
    # Standard base32 secret
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    assert len(code) == 6
    assert code.isdigit()
    assert totp.verify(code) is True


def test_auto_login_step1_failure_raises() -> None:
    with patch("scripts.auto_login.sync_playwright") as mock_pw:
        mock_p = MagicMock()
        mock_pw.return_value.__enter__.return_value = mock_p

        mock_browser = MagicMock()
        mock_p.chromium.launch.return_value = mock_browser

        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_error_loc = MagicMock()
        mock_error_loc.is_visible.return_value = True
        mock_error_loc.inner_text.return_value = "Invalid password or user ID"

        mock_twofa_loc = MagicMock()
        mock_twofa_loc.is_visible.return_value = False

        def mock_locator(sel: str, **kwargs):
            if "error" in sel:
                return mock_error_loc
            return mock_twofa_loc

        mock_page.locator.side_effect = mock_locator

        with pytest.raises(RuntimeError, match="Step 1 .* rejected by Kite: Invalid password or user ID"):
            perform_automated_login(
                api_key="key",
                api_secret="secret",
                user_id="U12345",
                password="wrong_password",
                totp_secret=pyotp.random_base32(),
                headless=True,
            )


def test_auto_login_skips_on_holiday(monkeypatch) -> None:
    from datetime import datetime, timezone, timedelta
    from scripts.auto_login import main

    IST = timezone(timedelta(hours=5, minutes=30))
    holiday_now = datetime(2026, 11, 24, 8, 50, tzinfo=IST)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return holiday_now

    monkeypatch.setattr("scripts.auto_login.datetime", MockDatetime)
    monkeypatch.setattr("sys.argv", ["auto_login.py"])

    # Mock perform_automated_login so if it gets called, it would fail
    mock_perform = MagicMock()
    monkeypatch.setattr("scripts.auto_login.perform_automated_login", mock_perform)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    mock_perform.assert_not_called()
