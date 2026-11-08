"""
tests/test_order_manager.py
===========================
Unit tests for OrderManager, including margin pre-check and order placement safety.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest

from src.order_manager import OrderManager
from src.signal_engine import SignalEngine

IST = timezone(timedelta(hours=5, minutes=30))


def test_margin_sufficient_places_live_order():
    mock_kite = MagicMock()
    mock_kite.margins.return_value = {
        "available": {
            "live_balance": 50000.0,
            "cash": 50000.0,
        }
    }
    mock_kite.place_order.return_value = "ORDER_12345"
    mock_kite.orders.return_value = [{"order_id": "ORDER_12345", "average_price": 245.0}]

    mock_notifier = MagicMock()
    mock_state_mgr = MagicMock()
    engine = SignalEngine()

    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=mock_notifier,
        lot_size=65,
        live_order_placement=True,
        state_manager=mock_state_mgr,
    )

    now = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    indicators = {"rsi": 75.0, "upper_bb": 24500.0, "r1": 24520.0}

    # Estimated premium 245 * 65 = ~15,925 required (50,000 available)
    order_mgr._buy(
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        spot=24550.0,
        candle_time=now,
        indicators=indicators,
        estimated_ltp=245.0,
    )

    mock_kite.place_order.assert_called_once()
    mock_notifier.margin_insufficient.assert_not_called()
    assert order_mgr.open_symbol == "NIFTY12NOV2624600CE"
    assert engine.position == "CE"
    mock_state_mgr.record_entry.assert_called_once()


def test_margin_insufficient_aborts_live_order():
    mock_kite = MagicMock()
    mock_kite.margins.return_value = {
        "available": {
            "live_balance": 5000.0,  # Only 5,000 available!
            "cash": 5000.0,
        }
    }

    mock_notifier = MagicMock()
    mock_state_mgr = MagicMock()
    engine = SignalEngine()

    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=mock_notifier,
        lot_size=65,
        live_order_placement=True,
        state_manager=mock_state_mgr,
    )

    now = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    indicators = {"rsi": 75.0, "upper_bb": 24500.0, "r1": 24520.0}

    # Estimated premium 245 * 65 = ~15,925 required (only 5,000 available)
    order_mgr._buy(
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        spot=24550.0,
        candle_time=now,
        indicators=indicators,
        estimated_ltp=245.0,
    )

    # Must NOT call place_order
    mock_kite.place_order.assert_not_called()
    # Must dispatch margin alert
    mock_notifier.margin_insufficient.assert_called_once_with(
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        required_margin=pytest.approx(15925.0),
        available_balance=5000.0,
        qty=65,
    )
    # State remains clean
    assert order_mgr.open_symbol is None
    assert engine.position is None
    mock_state_mgr.record_entry.assert_not_called()


def test_margin_api_error_failsafe():
    mock_kite = MagicMock()
    # Kite margins API throws exception
    mock_kite.margins.side_effect = RuntimeError("Kite API margin endpoint timeout")
    mock_kite.place_order.return_value = "ORDER_9999"
    mock_kite.orders.return_value = [{"order_id": "ORDER_9999", "average_price": 240.0}]

    mock_notifier = MagicMock()
    engine = SignalEngine()

    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=mock_notifier,
        lot_size=65,
        live_order_placement=True,
    )

    now = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    order_mgr._buy(
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        spot=24550.0,
        candle_time=now,
        indicators={},
        estimated_ltp=240.0,
    )

    # Order still executes (failsafe on margin API timeout)
    mock_kite.place_order.assert_called_once()
    assert order_mgr.open_symbol == "NIFTY12NOV2624600CE"


def test_simulation_mode_does_not_abort_on_low_margin():
    mock_kite = MagicMock()
    mock_kite.margins.return_value = {
        "available": {
            "live_balance": 0.0,
        }
    }

    mock_notifier = MagicMock()
    engine = SignalEngine()

    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=mock_notifier,
        lot_size=65,
        live_order_placement=False,  # Simulation
    )

    now = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    order_mgr._buy(
        symbol="NIFTY12NOV2624600CE",
        option_type="CE",
        spot=24550.0,
        candle_time=now,
        indicators={},
        estimated_ltp=245.0,
    )

    # In simulation, fill succeeds
    assert order_mgr.open_symbol == "NIFTY12NOV2624600CE"
    assert order_mgr.entry_price == 245.0
    assert engine.position == "CE"
    mock_notifier.margin_insufficient.assert_not_called()

