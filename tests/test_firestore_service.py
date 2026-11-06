"""
tests/test_firestore_service.py
===============================
Unit tests for FirestoreService (mocking the underlying firestore client).
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.firestore_service import FirestoreService


def test_firestore_service_disabled_when_key_missing() -> None:
    fs = FirestoreService(credentials_path="non_existent_key.json")
    assert fs.is_connected is False
    assert fs.record_entry("t1", "strat", "sym", "CE", 24000, 24000, 65, datetime.datetime.now(), 100, {}, False) is False
    assert fs.record_exit("t1", datetime.datetime.now(), 120, "ST Exit", 1300) is False


def test_firestore_record_entry_and_exit_with_mock() -> None:
    fs = FirestoreService(credentials_path="dummy.json")
    # Attach mock db client
    mock_db = MagicMock()
    fs._db = mock_db

    now = datetime.datetime(2025, 5, 20, 10, 0, 0)
    res_entry = fs.record_entry(
        trade_id="TRADE_001",
        strategy_name="NIFTY Options Buying Strategy v9",
        symbol="NIFTY22MAY2524000CE",
        option_type="CE",
        strike=24000,
        spot_price=23800.0,
        lot_size=65,
        entry_time=now,
        entry_price=150.0,
        indicators={"rsi": 75.0, "r1": 23750.0},
        live_trade=False,
    )
    assert res_entry is True
    mock_db.collection.assert_called_with("nifty")
    mock_db.collection().document.assert_called_with("TRADE_001")
    mock_db.collection().document().set.assert_called_once()
    saved_data = mock_db.collection().document().set.call_args[0][0]
    assert saved_data["strategy_name"] == "NIFTY Options Buying Strategy v9"
    assert saved_data["status"] == "OPEN"
    assert saved_data["strike"] == 24000

    # Test exit
    res_exit = fs.record_exit(
        trade_id="TRADE_001",
        exit_time=now,
        exit_price=180.0,
        exit_reason="ST Flip Exit",
        pnl=1950.0,
    )
    assert res_exit is True
    mock_db.collection().document().update.assert_called_once()
    update_data = mock_db.collection().document().update.call_args[0][0]
    assert update_data["status"] == "CLOSED"
    assert update_data["pnl"] == 1950.0

