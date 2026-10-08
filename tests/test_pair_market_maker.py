import uuid

import pytest

from app.config import PAIR_MM_STRATEGY, PMM_PAIR_SHARES
from app.execution_engine import ExecutionEngine
from app.paper_validation_tracker import PaperValidationTracker
from app.position_manager import PositionManager
from strategies.two_sided_mm_strategy import TwoSidedMMStrategy


def _market(bid_a, bid_b, tick=0.01):
    """Two-token market data map with one-tick spreads on both legs."""
    a, b = f"A-{uuid.uuid4().hex}", f"B-{uuid.uuid4().hex}"
    meta = {
        "conditionId": f"0x{uuid.uuid4().hex}",
        "tokens": [{"token_id": a}, {"token_id": b}],
        "endDate": "2099-01-01T00:00:00Z",
        "oneHourPriceChange": 0.0,
        "orderPriceMinTickSize": tick,
        "orderMinSize": 5,
    }

    def leg(token_id, bid):
        md = dict(meta, token_id=token_id, best_bid=bid, best_ask=round(bid + tick, 6), spread=0.02)
        md["orderbook"] = {"bids": [{"price": str(bid), "size": "500"}],
                           "asks": [{"price": str(round(bid + tick, 6)), "size": "500"}]}
        return md

    return a, b, {a: leg(a, bid_a), b: leg(b, bid_b)}


def _setup():
    ee = ExecutionEngine(paper_trading=True)
    tracker = PaperValidationTracker(total_capital=50.0)
    ee.set_tracker(tracker)
    pm = PositionManager()
    pm.clear_all_positions()  # temp test DB: start every scenario without leftover inventory
    return ee, pm, TwoSidedMMStrategy(ee, pm, tracker=tracker)


def _quotes(ee, token_id):
    return [(o["price"], o["size"]) for o in ee.pending_orders if o["token_id"] == token_id]


@pytest.mark.asyncio
async def test_opens_both_bids_when_pair_costs_less_than_one():
    ee, pm, mm = _setup()
    a, b, data = _market(0.40, 0.59)
    await mm.step(list(data.values()), data)
    assert _quotes(ee, a) == [(0.40, PMM_PAIR_SHARES)]
    assert _quotes(ee, b) == [(0.59, PMM_PAIR_SHARES)]


@pytest.mark.asyncio
async def test_no_quotes_without_edge():
    ee, pm, mm = _setup()
    a, b, data = _market(0.405, 0.594, tick=0.001)  # pair costs 0.999 < 1 - 0.01 is false
    await mm.step(list(data.values()), data)
    assert _quotes(ee, a) == [] and _quotes(ee, b) == []


@pytest.mark.asyncio
async def test_matched_legs_are_merged_for_the_locked_edge():
    ee, pm, mm = _setup()
    a, b, data = _market(0.40, 0.59)
    pm.update_position(a, 10, 0.40, "BUY", strategy=PAIR_MM_STRATEGY, fee=0.0)
    pm.update_position(b, 10, 0.59, "BUY", strategy=PAIR_MM_STRATEGY, fee=0.0)
    realized_before = pm.realized_pnl

    await mm.step(list(data.values()), data)
    assert a not in pm.positions and b not in pm.positions
    assert pm.realized_pnl - realized_before == pytest.approx(10 * (1 - 0.40 - 0.59))


@pytest.mark.asyncio
async def test_single_leg_only_bids_missing_leg_at_capped_price():
    ee, pm, mm = _setup()
    a, b, data = _market(0.40, 0.62)  # NO bid moved up after our YES filled
    pm.update_position(a, 10, 0.40, "BUY", strategy=PAIR_MM_STRATEGY, fee=0.0)

    await mm.step(list(data.values()), data)
    assert _quotes(ee, a) == []                         # never add to the held leg
    assert _quotes(ee, b) == [(0.59, 10)]               # 1 - 0.40 - min edge, not the 0.62 touch


@pytest.mark.asyncio
async def test_adverse_move_unwinds_single_leg():
    ee, pm, mm = _setup()
    a, b, data = _market(0.34, 0.65)  # YES bid fell 6 cents below our 0.40 cost
    pm.update_position(a, 10, 0.40, "BUY", strategy=PAIR_MM_STRATEGY, fee=0.0)

    await mm.step(list(data.values()), data)
    assert a not in pm.positions
    assert _quotes(ee, b) == []
    key = mm.token_market[a]
    assert mm.cooldown_until[key] > 0


def test_generic_exit_engine_ignores_pair_inventory():
    pm = PositionManager()
    pm.clear_all_positions()
    a, b, data = _market(0.20, 0.79)
    pm.update_position(a, 10, 0.40, "BUY", strategy=PAIR_MM_STRATEGY, fee=0.0)
    exits = pm.get_positions_for_exit(data)
    assert all(e["token_id"] != a for e in exits)


@pytest.mark.asyncio
async def test_prefers_markets_with_shorter_queues(monkeypatch):
    monkeypatch.setattr("strategies.two_sided_mm_strategy.PMM_MAX_MARKETS", 1)
    ee, pm, mm = _setup()
    a1, b1, crowded = _market(0.40, 0.59)
    a2, b2, quiet = _market(0.30, 0.69)
    for md in quiet.values():
        md["orderbook"]["bids"][0]["size"] = "50"
    data = {**crowded, **quiet}
    await mm.step(list(data.values()), data)
    assert _quotes(ee, a2) and _quotes(ee, b2)
    assert not _quotes(ee, a1) and not _quotes(ee, b1)
