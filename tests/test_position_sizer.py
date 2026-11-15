"""
tests/test_position_sizer.py
=============================
Unit tests for PositionSizer dynamic scaling and drawdown protection.
"""

import pandas as pd
import pytest

from src.position_sizer import PositionSizer, SizingDecision


def test_position_sizer_initial_state():
    sizer = PositionSizer(
        initial_capital=150000.0,
        base_lots=1,
        max_lots=5,
        profit_step_per_lot=40000.0,
        min_trades_per_scale=40,
        step_down_dd_pct=12.0,
    )
    decision = sizer.evaluate([])
    assert decision.recommended_lots == 1
    assert decision.action == "MAINTAIN"
    assert decision.total_trades == 0
    assert decision.current_equity == 150000.0


def test_position_sizer_scale_up_criteria():
    sizer = PositionSizer(
        initial_capital=150000.0,
        base_lots=1,
        max_lots=5,
        profit_step_per_lot=40000.0,
        min_trades_per_scale=40,
        step_down_dd_pct=12.0,
    )

    # 40 trades with ₹45,000 net profit and small drawdown
    trades = [{"pnl": 1125.0} for _ in range(40)]
    decision = sizer.evaluate(trades, current_lots=1)

    assert decision.action == "SCALE_UP"
    assert decision.recommended_lots == 2
    assert decision.total_trades == 40
    assert decision.net_pnl == 45000.0


def test_position_sizer_insufficient_trades_no_scale():
    sizer = PositionSizer(
        initial_capital=150000.0,
        base_lots=1,
        max_lots=5,
        profit_step_per_lot=40000.0,
        min_trades_per_scale=40,
        step_down_dd_pct=12.0,
    )

    # High profit but only 20 trades
    trades = [{"pnl": 3000.0} for _ in range(20)]
    decision = sizer.evaluate(trades, current_lots=1)

    assert decision.action == "MAINTAIN"
    assert decision.recommended_lots == 1
    assert decision.trades_needed == 20


def test_position_sizer_step_down_on_drawdown():
    sizer = PositionSizer(
        initial_capital=150000.0,
        base_lots=1,
        max_lots=5,
        profit_step_per_lot=40000.0,
        min_trades_per_scale=40,
        step_down_dd_pct=12.0,
    )

    # Account reached 200,000, then suffered a 28,000 loss (14% DD from peak)
    trades = [{"pnl": 50000.0}, {"pnl": -28000.0}]
    decision = sizer.evaluate(trades, current_lots=2)

    assert decision.action == "STEP_DOWN"
    assert decision.recommended_lots == 1
    assert decision.drawdown_pct >= 12.0


def test_position_sizer_with_sufficient_broker_cash():
    sizer = PositionSizer()
    trades = [{"pnl": 1125.0} for _ in range(40)]

    # Broker cash is ₹1,20,000 (ample for 2 lots)
    decision = sizer.evaluate(trades, current_lots=1, broker_capital=120000.0)
    assert decision.action == "SCALE_UP"
    assert decision.recommended_lots == 2
    assert decision.current_equity == 120000.0


def test_position_sizer_blocks_scale_up_if_broker_cash_low():
    sizer = PositionSizer()
    trades = [{"pnl": 1125.0} for _ in range(40)]

    # Strategy made ₹45,000 profit, BUT user withdrew money and Zerodha only has ₹35,000 cash
    decision = sizer.evaluate(trades, current_lots=1, broker_capital=35000.0)
    assert decision.action == "MAINTAIN"
    assert decision.recommended_lots == 1
    assert "below recommended margin buffer" in decision.reason


def test_fetch_broker_cash_mock():
    class MockKite:
        def margins(self, segment):
            return {
                "available": {
                    "cash": 88500.50,
                    "live_balance": 88500.50,
                }
            }

    cash = PositionSizer.fetch_broker_cash(MockKite())
    assert cash == 88500.50


