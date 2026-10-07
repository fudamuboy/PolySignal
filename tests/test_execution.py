
import pytest
from app.execution_engine import ExecutionEngine
from app.paper_fill_model import taker_fee_rate, round_to_tick

@pytest.mark.asyncio
async def test_pmm_passive_order_lifecycle(book):
    ee = ExecutionEngine(paper_trading=True)

    # 1. Non-marketable limit order -> PENDING state
    res = await ee.place_limit_order("test_token", 0.50, 10, "BUY", market_data=book(0.49, 0.51))
    assert res["status"] == "PENDING"
    assert len(ee.pending_orders) == 1

    # 2. Check cancellation
    cancelled = await ee.cancel_order(res["order_id"])
    assert cancelled is True
    assert len(ee.pending_orders) == 0

@pytest.mark.asyncio
async def test_emergency_fill_walks_the_book(book):
    ee = ExecutionEngine(paper_trading=True)
    md = book(bid_levels=[(0.49, 6.0), (0.48, 100.0)], ask_levels=[(0.51, 100.0)])

    # Emergency sell sweeps the bids: 6 @ 0.49 then 4 @ 0.48, never at the requested price
    res = await ee.place_limit_order("test_token", 0.01, 10, "SELL", market_data=md, is_emergency=True)
    assert res["status"] == "SUCCESS"
    assert res["fill_size"] == pytest.approx(10)
    assert res["fill_price"] == pytest.approx((6 * 0.49 + 4 * 0.48) / 10)
    assert res["slippage"] == pytest.approx(0.49 - res["fill_price"])
    assert len(ee.pending_orders) == 0

@pytest.mark.asyncio
async def test_aggressive_order_respects_limit_and_depth(book):
    ee = ExecutionEngine(paper_trading=True)
    md = book(ask_levels=[(0.51, 4.0), (0.55, 100.0)])

    # Limit 0.52 only reaches the first level -> partial FAK fill, remainder dropped
    res = await ee.place_limit_order("t", 0.52, 10, "BUY", market_data=md, is_aggressive=True)
    assert res["status"] == "SUCCESS"
    assert res["fill_size"] == pytest.approx(4.0)
    assert res["partial"] is True
    assert ee.pending_orders == []

    # Limit below the ask -> nothing to take
    res = await ee.place_limit_order("t", 0.50, 10, "BUY", market_data=md, is_aggressive=True)
    assert res["status"] == "FAILED"

@pytest.mark.asyncio
async def test_paper_order_rejections(book):
    ee = ExecutionEngine(paper_trading=True)
    # No book -> cannot simulate
    res = await ee.place_limit_order("t", 0.50, 10, "BUY")
    assert res["status"] == "FAILED"
    # Below the market minimum order size
    res = await ee.place_limit_order("t", 0.50, 3, "BUY", market_data=book())
    assert res["status"] == "FAILED"

@pytest.mark.asyncio
async def test_taker_fee_uses_market_fee_schedule(book):
    ee = ExecutionEngine(paper_trading=True)
    schedule = {"rate": 0.07, "exponent": 1, "takerOnly": True}
    md = book(0.49, 0.50, feeSchedule=schedule)
    res = await ee.place_limit_order("t", 0.50, 100, "BUY", market_data=md, is_aggressive=True)
    # fee = shares * p * rate * (p * (1 - p)) ** exponent = 100 * 0.5 * 0.07 * 0.25
    assert res["fee"] == pytest.approx(0.875)
    assert taker_fee_rate(0.5, {"feesEnabled": False}) == 0.0

def test_prices_snap_to_tick():
    assert round_to_tick(0.5049, 0.01, "BUY") == 0.50
    assert round_to_tick(0.5049, 0.01, "SELL") == 0.51
    assert round_to_tick(0.5001, 0.01, "BUY", aggressive=True) == 0.51
    assert round_to_tick(0.57, 0.01, "BUY") == 0.57
    assert round_to_tick(0.1234, 0.001, "SELL") == 0.124
