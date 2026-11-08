"""
tests/test_state_manager.py
===========================
Unit tests for StateManager, active position lifecycle (create on entry, remove on exit),
and crash recovery reconciliation.
"""

from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import MagicMock
import json
import os
import pytest

from src.state_manager import StateManager
from src.signal_engine import SignalEngine
from src.order_manager import OrderManager

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def temp_state_file(tmp_path):
    return str(tmp_path / "active_position.json")


@pytest.fixture
def state_mgr(temp_state_file):
    return StateManager(state_file=temp_state_file)


def test_file_not_present_by_default(state_mgr, temp_state_file):
    assert not os.path.exists(temp_state_file)
    assert state_mgr.load_active_position() is None


def test_create_file_on_entry_and_remove_on_exit(state_mgr, temp_state_file):
    now = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    
    # 1. Entry: File must be created
    state_mgr.record_entry(
        trade_id="TRADE_TEST_1",
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        entry_price=245.50,
        entry_time=now,
        qty=65,
        spot_price=24600.0,
    )

    assert os.path.exists(temp_state_file)
    loaded = state_mgr.load_active_position()
    assert loaded is not None
    assert loaded["symbol"] == "NIFTY12NOV2624600CE"
    assert loaded["entry_price"] == 245.50

    # 2. Exit: File must be completely removed from disk
    state_mgr.record_exit()
    assert not os.path.exists(temp_state_file)
    assert state_mgr.load_active_position() is None


def test_stale_file_removed_on_new_day(state_mgr, temp_state_file):
    # Write a stale date state (yesterday)
    yesterday_str = "2026-11-05"
    stale_state = {
        "date": yesterday_str,
        "symbol": "NIFTY_OLD_CE",
        "entry_price": 200.0,
    }
    state_mgr.save_state(stale_state)
    assert os.path.exists(temp_state_file)

    engine = SignalEngine()
    order_mgr = MagicMock()

    result = state_mgr.reconcile(
        kite=None,
        order_manager=order_mgr,
        signal_engine=engine,
        live_mode=False,
    )

    assert result["status"] == "fresh_day"
    assert not os.path.exists(temp_state_file)
    assert engine.trade_today == 0
    assert engine.position is None


def test_reconcile_simulation_open_position(state_mgr, temp_state_file):
    now = datetime.now(tz=IST)

    state_mgr.record_entry(
        trade_id="SIM_TRADE_1",
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        entry_price=250.0,
        entry_time=now,
        qty=65,
        spot_price=24600.0,
    )
    assert os.path.exists(temp_state_file)

    engine = SignalEngine(state_manager=state_mgr)
    order_mgr = OrderManager(
        kite=MagicMock(),
        signal_engine=engine,
        notifier=MagicMock(),
        live_order_placement=False,
        state_manager=state_mgr,
    )

    # Reconcile on restart
    result = state_mgr.reconcile(
        kite=None,
        order_manager=order_mgr,
        signal_engine=engine,
        live_mode=False,
    )

    assert result["status"] == "recovered_simulation_position"
    assert engine.position == "CE"
    assert engine.trade_today == 1
    assert order_mgr.open_symbol == "NIFTY12NOV2624600CE"
    assert order_mgr.entry_price == 250.0


def test_reconcile_live_open_position_on_broker(state_mgr, temp_state_file):
    mock_kite = MagicMock()
    mock_kite.positions.return_value = {
        "net": [
            {
                "tradingsymbol": "NIFTY12NOV2624600CE",
                "exchange": "NFO",
                "product": "MIS",
                "quantity": 65,
                "average_price": 248.50,
            }
        ]
    }

    # Pre-record entry
    state_mgr.record_entry(
        trade_id="LIVE_TRADE_1",
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        entry_price=248.50,
        entry_time=datetime.now(tz=IST),
        qty=65,
        spot_price=24600.0,
    )

    engine = SignalEngine(state_manager=state_mgr)
    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=MagicMock(),
        live_order_placement=True,
        state_manager=state_mgr,
    )

    result = state_mgr.reconcile(
        kite=mock_kite,
        order_manager=order_mgr,
        signal_engine=engine,
        live_mode=True,
        notifier=MagicMock(),
    )

    assert result["status"] == "recovered_live_position"
    assert engine.position == "CE"
    assert engine.trade_today == 1
    assert order_mgr.open_symbol == "NIFTY12NOV2624600CE"
    assert order_mgr.entry_price == 248.50


def test_reconcile_live_position_closed_on_broker_removes_file(state_mgr, temp_state_file):
    # State file exists, but broker returns quantity = 0
    state_mgr.record_entry(
        trade_id="TRADE_OFFLINE",
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        entry_price=248.50,
        entry_time=datetime.now(tz=IST),
        qty=65,
        spot_price=24600.0,
    )
    assert os.path.exists(temp_state_file)

    mock_kite = MagicMock()
    mock_kite.positions.return_value = {
        "net": [
            {
                "tradingsymbol": "NIFTY12NOV2624600CE",
                "exchange": "NFO",
                "product": "MIS",
                "quantity": 0,  # closed on Kite!
                "day_buy_quantity": 65,
                "average_price": 248.50,
            }
        ]
    }

    engine = SignalEngine(state_manager=state_mgr)
    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=MagicMock(),
        live_order_placement=True,
        state_manager=state_mgr,
    )

    result = state_mgr.reconcile(
        kite=mock_kite,
        order_manager=order_mgr,
        signal_engine=engine,
        live_mode=True,
        notifier=MagicMock(),
    )

    assert result["status"] == "closed_externally"
    # File must be deleted
    assert not os.path.exists(temp_state_file)
    assert engine.position is None
    assert engine.trade_today == 1
    assert order_mgr.open_symbol is None


def test_reconcile_live_restart_after_exit_no_file(state_mgr, temp_state_file):
    # No file on disk (already deleted on exit)
    assert not os.path.exists(temp_state_file)

    mock_kite = MagicMock()
    mock_kite.positions.return_value = {
        "net": [
            {
                "tradingsymbol": "NIFTY12NOV2624600CE",
                "exchange": "NFO",
                "product": "MIS",
                "quantity": 0,
                "day_buy_quantity": 65,  # trade was executed today
                "average_price": 248.50,
            }
        ]
    }

    engine = SignalEngine(state_manager=state_mgr)
    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=MagicMock(),
        live_order_placement=True,
        state_manager=state_mgr,
    )

    result = state_mgr.reconcile(
        kite=mock_kite,
        order_manager=order_mgr,
        signal_engine=engine,
        live_mode=True,
        notifier=MagicMock(),
    )

    assert result["status"] == "synced"
    assert result["trade_today"] == 1
    assert engine.trade_today == 1
    assert engine.position is None
    assert not os.path.exists(temp_state_file)
