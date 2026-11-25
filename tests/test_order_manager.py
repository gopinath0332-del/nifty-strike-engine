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


def test_handle_exit_rejects_spot_as_option_exit_price():
    mock_kite = MagicMock()
    mock_notifier = MagicMock()
    engine = SignalEngine()
    engine.on_entry_filled("CE")

    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=mock_notifier,
        lot_size=65,
        live_order_placement=False,
    )
    order_mgr._open_symbol = "NIFTY26NOV25500CE"
    order_mgr._entry_price = 246.60

    # Kite LTP returns option last price 260.0
    mock_kite.ltp.return_value = {
        "NFO:NIFTY26NOV25500CE": {"last_price": 260.0}
    }

    # Pass 25765.90 as exit_price (accidentally spot index price)
    order_mgr.handle_exit(
        reason="EOD 15:00 Watchdog Exit",
        exit_price=25765.90,
        candle_time=datetime(2026, 11, 20, 15, 0, tzinfo=IST),
    )

    # Should have queried Kite for the NFO option LTP
    mock_kite.ltp.assert_called_with("NFO:NIFTY26NOV25500CE")
    # Notifier should receive the real option price (260.0), NOT 25765.90
    call_kwargs = mock_notifier.exit_signal.call_args[1]
    assert call_kwargs["exit_price"] == 260.0
    assert call_kwargs["entry_price"] == 246.60
    assert order_mgr.open_symbol is None


def test_record_sizing_weekly_and_monthly_strike_parsing():
    mock_kite = MagicMock()
    mock_sizer = MagicMock()
    mock_decision = MagicMock()
    mock_decision.action = "HOLD"
    mock_decision.total_trades = 1
    mock_decision.current_lots = 1
    mock_decision.recommended_lots = 1
    mock_decision.net_pnl = 0.0
    mock_decision.drawdown_pct = 0.0
    mock_decision.reason = "testing"
    mock_decision.headline = "HOLD"
    mock_sizer.evaluate.return_value = mock_decision

    engine = SignalEngine()

    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=MagicMock(),
        lot_size=65,
        position_sizer=mock_sizer,
    )

    now = datetime(2026, 11, 20, 15, 0, tzinfo=IST)
    # 1. Weekly symbol
    order_mgr._record_and_evaluate_sizing(
        trade_id="T1",
        symbol="NIFTY26N1725050CE",
        entry_time=now,
        exit_time=now,
        entry_price=245.0,
        exit_price=260.0,
        exit_reason="ST Flip Exit",
        pnl=975.0,
        spot_price=25200.0,
    )
    rec1 = mock_sizer.save_live_trade.call_args_list[0][0][0]
    assert rec1["strike"] == 25050
    assert rec1["spot_price"] == 25200.0
    assert rec1["exit_price"] == 260.0

    # 2. Monthly symbol
    order_mgr._record_and_evaluate_sizing(
        trade_id="T2",
        symbol="NIFTY26NOV25250CE",
        entry_time=now,
        exit_time=now,
        entry_price=250.0,
        exit_price=270.0,
        exit_reason="ST Flip Exit",
        pnl=1300.0,
        spot_price=25350.0,
    )
    rec2 = mock_sizer.save_live_trade.call_args_list[1][0][0]
    assert rec2["strike"] == 25250
    assert rec2["spot_price"] == 25350.0
    assert rec2["exit_price"] == 270.0


def test_live_orders_pass_market_protection():
    mock_kite = MagicMock()
    mock_kite.positions.return_value = {"net": []}
    mock_kite.margins.return_value = {"available": {"live_balance": 100000.0}}
    mock_kite.place_order.side_effect = ["BUY_1", "SELL_1"]
    mock_kite.orders.return_value = [
        {"order_id": "BUY_1", "average_price": 248.8},
        {"order_id": "SELL_1", "average_price": 265.0},
    ]

    engine = SignalEngine()
    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=MagicMock(),
        lot_size=65,
        live_order_placement=True,
        state_manager=MagicMock(),
    )

    now = datetime(2026, 11, 25, 11, 0, tzinfo=IST)
    order_mgr._buy(
        symbol="NIFTY26D0125900CE",
        option_type="CE",
        spot=25878.5,
        candle_time=now,
        indicators={"rsi": 77.6},
        estimated_ltp=248.8,
    )
    assert mock_kite.place_order.call_args_list[0].kwargs["market_protection"] == -1

    order_mgr.handle_exit(reason="ST Flip Exit", candle_time=now, spot=25900.0)
    assert mock_kite.place_order.call_args_list[1].kwargs["market_protection"] == -1


def test_live_buy_adopts_existing_manual_position_and_skips_duplicate():
    mock_kite = MagicMock()
    mock_kite.positions.return_value = {
        "net": [
            {
                "tradingsymbol": "NIFTY26D0125900CE",
                "quantity": 65,
                "average_price": 252.5,
                "product": "MIS",
            }
        ]
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

    now = datetime(2026, 11, 25, 11, 5, tzinfo=IST)
    order_mgr._buy(
        symbol="NIFTY26D0125950CE",
        option_type="CE",
        spot=25907.9,
        candle_time=now,
        indicators={"rsi": 79.6},
        estimated_ltp=245.5,
    )

    mock_kite.place_order.assert_not_called()
    assert order_mgr.open_symbol == "NIFTY26D0125900CE"
    assert order_mgr.entry_price == 252.5
    assert engine.position == "CE"
    assert engine.trade_today == 1
    mock_state_mgr.record_entry.assert_called_once()
    mock_notifier.info.assert_called_once()


def test_handle_exit_uses_adopted_nrml_product_and_retries_fill_price():
    mock_kite = MagicMock()
    mock_kite.positions.return_value = {
        "net": [
            {
                "tradingsymbol": "NIFTY26D0125900CE",
                "quantity": 65,
                "average_price": 260.75,
                "product": "NRML",
            }
        ]
    }
    mock_kite.place_order.return_value = "EXIT_NRML_1"
    # First orders() call has average_price=0 (not yet settled); second has 211.80
    mock_kite.orders.side_effect = [
        [{"order_id": "EXIT_NRML_1", "status": "OPEN", "average_price": 0}],
        [{"order_id": "EXIT_NRML_1", "status": "COMPLETE", "average_price": 211.80}],
    ]

    mock_notifier = MagicMock()
    engine = SignalEngine()
    engine.on_entry_filled("CE")

    order_mgr = OrderManager(
        kite=mock_kite,
        signal_engine=engine,
        notifier=mock_notifier,
        lot_size=65,
        live_order_placement=True,
        state_manager=MagicMock(),
    )
    order_mgr.restore_state(
        symbol="NIFTY26D0125900CE",
        entry_price=260.75,
        trade_id="ORPHAN_2026-11-25_CE",
        qty=65,
        product="NRML",
    )

    order_mgr.handle_exit(
        reason="ST Flip Exit",
        candle_time=datetime(2026, 11, 25, 12, 35, tzinfo=IST),
        spot=25811.30,
    )

    assert mock_kite.place_order.call_args.kwargs["product"] == "NRML"
    assert mock_kite.place_order.call_args.kwargs["market_protection"] == -1
    exit_kwargs = mock_notifier.exit_signal.call_args.kwargs
    assert exit_kwargs["exit_price"] == 211.80
    assert order_mgr.open_product == "MIS"  # Reset to default after exit




