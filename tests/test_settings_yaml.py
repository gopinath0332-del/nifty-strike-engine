"""
tests/test_settings_yaml.py
===========================
Tests for settings.yaml parsing and validation.
"""

from __future__ import annotations

import os
from config.settings import _load_yaml_settings, Settings


def test_yaml_settings_parsing() -> None:
    data = _load_yaml_settings("config/settings.yml")
    assert data["lot_size"] == 65
    assert data["max_trades_per_day"] == 1
    assert data["strategy_name"] == "NIFTY_V9"
    assert data["firestore_collection"] == "nifty"
    assert data["log_level"] == "INFO"
    assert data["st_atr_period"] == 10
    assert data["st_factor"] == 3.0
    assert data["rsi_length"] == 14
    assert data["bb_length"] == 20
    assert data["bb_mult"] == 2.0
    assert data["candle_interval_minutes"] == 3
    assert data["entry_cutoff_time"] == "14:45"
    assert data["eod_exit_time"] == "15:00"


def test_settings_load_with_mock_env(monkeypatch) -> None:
    monkeypatch.setenv("KITE_API_KEY", "real_key_123")
    monkeypatch.setenv("KITE_API_SECRET", "real_secret_456")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "real_token_789")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test")
    monkeypatch.setenv("LIVE_ORDER_PLACEMENT", "false")

    s = Settings.load("config/settings.yaml")
    assert s.kite_api_key == "real_key_123"
    assert s.lot_size == 65
    assert s.st_atr_period == 10
    assert s.entry_cutoff_hhmm == (14, 45)
    assert s.eod_exit_hhmm == (15, 0)

