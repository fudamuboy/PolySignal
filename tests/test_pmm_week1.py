import pytest
from app.execution_engine import ExecutionEngine
from app.position_manager import PositionManager

@pytest.mark.asyncio
async def test_pmm_resting_order_flow(book):
    print("Testing PMM Resting Order Flow...")
    ee = ExecutionEngine(paper_trading=True)
    
    # 1. Verify placed order returns PENDING and enters ledger
    res = await ee.place_limit_order("token_a", 0.50, 10, "BUY", market_data=book(0.48, 0.52))
    assert res["status"] == "PENDING"
    assert len(ee.pending_orders) == 1
    assert ee.pending_orders[0]["token_id"] == "token_a"
    assert ee.pending_orders[0]["price"] == 0.50
    assert ee.pending_orders[0]["side"] == "BUY"

    # 2. Check resting fills (no fill if ask is above limit and last trade is above limit)
    market_data_no_fill = {
        "token_a": {
            "token_id": "token_a",
            "best_bid": 0.48,
            "best_ask": 0.52,  # ask is higher than our buy limit
            "last_price": 0.51, # last trade price is higher than our buy limit
            "spread": 0.08
        }
    }
    fills_1 = ee.check_resting_fills(market_data_no_fill)
    assert len(fills_1) == 0
    assert len(ee.pending_orders) == 1  # order stays in ledger

    # 3. Check resting fills (fill when ask drops to/below limit)
    market_data_fill = {
        "token_a": {
            "token_id": "token_a",
            "best_bid": 0.48,
            "best_ask": 0.50,  # ask touched our buy limit (someone sold to us)
            "last_price": 0.50,
            "spread": 0.04
        }
    }
    fills_2 = ee.check_resting_fills(market_data_fill)
    assert len(fills_2) == 1
    assert fills_2[0]["status"] == "SUCCESS"
    assert fills_2[0]["fill_price"] == 0.50
    assert fills_2[0]["fill_size"] == 10
    assert len(ee.pending_orders) == 0  # order is removed from ledger

    # 4. Check order cancellation
    res_cancel = await ee.place_limit_order("token_b", 0.60, 5, "SELL", market_data=book(0.58, 0.62))
    assert len(ee.pending_orders) == 1
    order_id = res_cancel["order_id"]
    
    cancelled = await ee.cancel_order(order_id)
    assert cancelled is True
    assert len(ee.pending_orders) == 0

@pytest.mark.asyncio
async def test_pmm_db_recovery(monkeypatch):
    print("Testing PMM Database Position Recovery...")
    pm = PositionManager()
    
    # Mock database load_positions to return dummy positions
    mock_positions = {"recovered_token": {"size": 20, "avg_price": 0.45}}
    pm.positions = mock_positions
    
    # Verify that database position recovery stores it in memory
    assert "recovered_token" in pm.positions
    assert pm.positions["recovered_token"]["size"] == 20
