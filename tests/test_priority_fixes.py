import pytest
import time
from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.paper_validation_tracker import PaperValidationTracker
from app.execution_engine import ExecutionEngine
from app.config import PMM_TOTAL_CAPITAL

def test_hard_capital_limits_risk_manager():
    print("\n--- Testing Hard Capital Limits in RiskManager ---")
    pm = PositionManager()
    rm = RiskManager(position_manager=pm)
    
    # 1. Clean positions
    pm.positions = {}
    
    # 2. Add an active position to consume $80 of our $100 capital base
    token_a = "token_a_consuming_capital"
    pm.positions[token_a] = {
        "size": 160.0,
        "avg_price": 0.50,
        "strategy": "PMM"
    }
    
    # 3. Try to validate a BUY order that costs $30 (Total = $110 > $100 limit)
    signal_large = {
        "token_id": "token_b_new_order",
        "side": "BUY",
        "price": 0.50,
        "size": 60.0,
        "score": 0.8,
        "delta": 0.06,
        "strategy": "PMM"
    }
    
    # We pass a mock market_data to ensure calculate_position_size is called and liquidity filter passes
    market_data = {
        "best_bid": 0.49,
        "best_ask": 0.51,
        "spread": 0.04,
        "last_price": 0.50,
        "bid_depth": 300.0,
        "ask_depth": 300.0
    }
    
    # Since dynamic size calculation scales by score, let's mock calculate_position_size to return 60.0
    import unittest.mock as mock
    with mock.patch.object(rm, 'calculate_position_size', return_value=60.0):
        # Validate large order -> must be rejected
        is_valid = rm.validate_trade(signal_large, market_data)
        assert is_valid is False, "Risk manager must reject order that exceeds total capital base"
        print("✅ Rejecting oversized order successfully blocked virtual leverage!")

    # 4. Try to validate a BUY order that costs $15 (Total = $95 <= $100 limit)
    with mock.patch.object(rm, 'calculate_position_size', return_value=30.0):
        # Validate small order -> must be accepted (if it passes other filters)
        # We override standard filters by setting is_fallback=True, but let's also pass valid market depth
        is_valid_small = rm.validate_trade(signal_large, market_data, is_fallback=True)
        assert is_valid_small is True, "Risk manager should accept order that fits within capital base"
        print("✅ Accepting small order within capital limits verified!")

def test_tracker_cash_safety():
    print("\n--- Testing Telemetry Tracker Cash Safety ---")
    # Start tracker with $100 virtual cash
    tracker = PaperValidationTracker(total_capital=100.0)
    
    # 1. Placing a BUY order costing $150 (300 units @ 0.50) must raise ValueError immediately
    with pytest.raises(ValueError):
        tracker.record_order_placement(
            order_id="paper_overspend",
            token_id="token_safety",
            price=0.50,
            size=300.0,
            side="BUY"
        )
    print("✅ record_order_placement blocked placement exceeding available cash!")

    # 2. Test force-scale BUY fill inside _update_virtual_position
    # Set cash to $10.0
    tracker.virtual_cash = 10.0
    
    # Execute a BUY fill requesting size 40.0 @ 0.50 = cost $20.0 (exceeds cash $10.0)
    tracker._update_virtual_position(
        token_id="token_scale_test",
        size=40.0,
        price=0.50,
        side="BUY",
        spread=0.02
    )
    
    # Cash must be exactly 0.0, and the position size must be scaled down to 20.0 (10 / 0.50)
    assert tracker.virtual_cash == 0.0
    assert tracker.virtual_positions["token_scale_test"]["size"] == 20.0
    print("✅ Force-scaling fill successfully capped cost and kept cash non-negative!")

def test_real_exit_engine_cycle():
    print("\n--- Testing Real Exit Engine Trade Cycle & Realized PnL ---")
    pm = PositionManager()
    pm.clear_all_positions()
    
    # 1. Setup a position (100 shares @ 0.50, BUY, cost $50)
    token_id = "token_for_exit_cycle"
    pm.update_position(token_id, 100.0, 0.50, "BUY", "MomentumStrategy")
    assert pm.positions[token_id]["size"] == 100.0
    assert pm.realized_pnl == 0.0
    
    # 2. Trigger Take Profit Exit (exit at 0.60 -> realized PnL = +$10)
    tx_pnl = pm.update_position(
        token_id=token_id,
        size=100.0,
        price=0.60,
        side="SELL",
        strategy="EXIT_TP_DYNAMIC"
    )
    
    assert abs(tx_pnl - 10.0) < 1e-6
    assert abs(pm.realized_pnl - 10.0) < 1e-6
    assert token_id not in pm.positions, "Position must be completely closed upon full exit size SELL"
    print("✅ Exit realized profit successfully generated!")

    # 3. Trigger Stop Loss Exit (100 shares @ 0.50, BUY, then SELL exit at 0.40 -> realized PnL = -$10)
    pm.update_position(token_id, 100.0, 0.50, "BUY", "MomentumStrategy")
    tx_pnl_loss = pm.update_position(
        token_id=token_id,
        size=100.0,
        price=0.40,
        side="SELL",
        strategy="EXIT_SL_DYNAMIC"
    )
    assert abs(tx_pnl_loss - (-10.0)) < 1e-6
    assert abs(pm.realized_pnl - 0.0) < 1e-6 # +10 realized earlier -10 realized now = 0.0 total
    assert token_id not in pm.positions, "Position must be completely closed"
    print("✅ Exit realized loss successfully generated!")
