"""
tests/test_auto_login.py
========================
Tests for auto_login module:
  - TOTP code generation format and length
  - Simulation of login step failures with informative error reporting
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
    with patch("requests.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        # Return failure from step 1
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "error", "message": "Invalid password"}
        mock_session.post.return_value = mock_resp

        with pytest.raises(RuntimeError, match="Step 1 .* failed: Invalid password"):
            perform_automated_login(
                api_key="key",
                api_secret="secret",
                user_id="U12345",
                password="wrong_password",
                totp_secret=pyotp.random_base32(),
            )

