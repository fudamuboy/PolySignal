import pytest
from app.execution_engine import ExecutionEngine

@pytest.mark.asyncio
async def test_pmm_passive_order_lifecycle():
    print("Testing PMM Passive Order Lifecycle...")
    ee = ExecutionEngine(paper_trading=True)
    
    # 1. Place limit order -> PENDING state
    res = await ee.place_limit_order("test_token", 0.50, 10, "BUY")
    assert res["status"] == "PENDING"
    assert len(ee.pending_orders) == 1
    
    # 2. Check cancellation
    cancelled = await ee.cancel_order(res["order_id"])
    assert cancelled is True
    assert len(ee.pending_orders) == 0

@pytest.mark.asyncio
async def test_emergency_instant_force_fill():
    print("Testing Emergency Instant Force Fill...")
    ee = ExecutionEngine(paper_trading=True)
    
    # Emergency order must execute immediately (SUCCESS) and not go PENDING
    res = await ee.place_limit_order("test_token", 0.50, 10, "SELL", is_emergency=True)
    assert res["status"] == "SUCCESS"
    assert res["fill_price"] == 0.50
    assert res["fill_size"] == 10
    assert len(ee.pending_orders) == 0
