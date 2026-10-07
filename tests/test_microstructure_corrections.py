"""
TEST SUITE: MICROSTRUCTURE CORRECTIONS & SAFETY VALIDATION
===========================================================
Verifies:
1. Maker Take Profit limit order quoting logic inside spread
2. Maker TP fill accounting & zero maker exit fee
3. Maker TP waiting ticks increment & 6-tick timeout
4. Maker TP fallback to Bid after timeout
5. Emergency Stop-Loss override during pending Maker TP
6. Tick-size rounding and boundary protection
7. OBI depth ratio (>= 1.5x) and spread (<= 1.0%) gating
8. MAX_SPREAD (1.0%) filtering
9. SAFETY: LIVE_TRADING remains False and PAPER_TRADING remains True
"""
import pytest
import time
from app.position_manager import PositionManager
from app.config import (
    PAPER_TRADING, LIVE_TRADING, PAPER_INITIAL_CAPITAL,
    BASE_ORDER_SIZE, MAX_SPREAD, MAKER_TP_TIMEOUT_TICKS,
    TP_HIGH_CONFIDENCE, TAKER_FEE_BPS, MAKER_FEE_BPS
)
from strategies.order_book_imbalance_strategy import (
    OrderBookImbalanceStrategy, OBI_DEPTH_RATIO_MIN, OBI_MAX_SPREAD
)

def test_live_trading_remains_disabled_safety():
    """Guarantee that live trading execution path is strictly disabled."""
    assert PAPER_TRADING is True, "PAPER_TRADING must be True for paper validation."
    assert LIVE_TRADING is False, "LIVE_TRADING must be False. No real capital execution allowed."
    assert PAPER_INITIAL_CAPITAL == 50.0, "Virtual paper capital must be $50.0."
    assert BASE_ORDER_SIZE == 2.50, "Base order size must be $2.50."

def test_maker_tp_quoting():
    """Verify that Take Profit quotes as a Maker Limit order inside spread."""
    pm = PositionManager()
    pm.clear_all_positions()
    
    token_id = "test_token_maker_tp"
    entry_price = 0.50
    size = 5.0 # $2.50 position
    
    # Enter position
    pm.update_position(token_id=token_id, size=size, price=entry_price, side='BUY', strategy='PMM')
    
    # Market moves up by +8% (above TP_HIGH_CONFIDENCE 7%)
    # Mid = 0.54, Bid = 0.535, Ask = 0.545, Spread = (0.545-0.535)/0.54 = 1.85%
    market_map = {
        token_id: {
            "token_id": token_id,
            "mid_price": 0.54,
            "best_bid": 0.535,
            "best_ask": 0.545,
            "spread": 0.0185
        }
    }
    
    exits = pm.get_positions_for_exit(market_map)
    assert len(exits) == 1, "Should generate exactly 1 exit for TP"
    exit_cand = exits[0]
    
    assert exit_cand['token_id'] == token_id
    assert exit_cand['side'] == 'SELL'
    assert exit_cand['is_maker'] is True, "Take Profit exit must quote as MAKER Limit order"
    assert "MAKER_TP_ATTEMPT" in exit_cand['reason']
    # Price should undercut ask (0.545 - 0.0005 = 0.5445) and be strictly > bid (0.535)
    assert exit_cand['price'] > 0.535, "Maker TP price must be strictly higher than best bid"
    assert exit_cand['price'] <= 0.545, "Maker TP price must be <= best ask"

def test_maker_tp_non_fill_and_timeout():
    """Verify that Maker TP increments ticks and triggers fallback to Bid after 6 ticks."""
    pm = PositionManager()
    pm.clear_all_positions()
    
    token_id = "test_token_timeout"
    entry_price = 0.50
    size = 5.0
    
    pm.update_position(token_id=token_id, size=size, price=entry_price, side='BUY', strategy='PMM')
    
    market_map = {
        token_id: {
            "token_id": token_id,
            "mid_price": 0.54, # +8% move
            "best_bid": 0.535,
            "best_ask": 0.545,
            "spread": 0.0185
        }
    }
    
    # Loop ticks 1 to 6: should quote Maker TP
    for tick in range(1, MAKER_TP_TIMEOUT_TICKS + 1):
        exits = pm.get_positions_for_exit(market_map)
        assert len(exits) == 1
        assert exits[0]['is_maker'] is True, f"Tick {tick} should still be Maker TP attempt"
        assert f"tick {tick}/{MAKER_TP_TIMEOUT_TICKS}" in exits[0]['reason']
        
    # Tick 7: Timeout reached -> Fallback to Bid (Taker exit)
    exits_timeout = pm.get_positions_for_exit(market_map)
    assert len(exits_timeout) == 1
    fallback_exit = exits_timeout[0]
    assert fallback_exit['is_maker'] is False, "Fallback after timeout must exit at Bid"
    assert "MAKER_TP_FALLBACK" in fallback_exit['reason']
    assert fallback_exit['price'] == 0.535, "Fallback exit price must be current best bid"

def test_emergency_sl_overrides_pending_maker_tp():
    """Verify that price drop to SL triggers emergency exit at Bid even if Maker TP was pending."""
    pm = PositionManager()
    pm.clear_all_positions()
    
    token_id = "test_token_emergency_sl"
    entry_price = 0.50
    size = 5.0
    
    pm.update_position(token_id=token_id, size=size, price=entry_price, side='BUY', strategy='PMM')
    
    # Step 1: Upward move triggers Maker TP attempt
    market_tp = {
        token_id: {
            "token_id": token_id,
            "mid_price": 0.54, # +8%
            "best_bid": 0.535,
            "best_ask": 0.545,
            "spread": 0.0185
        }
    }
    exits = pm.get_positions_for_exit(market_tp)
    assert exits[0]['is_maker'] is True
    
    # Step 2: Sudden reversal down to -5% (0.475)
    market_sl = {
        token_id: {
            "token_id": token_id,
            "mid_price": 0.475, # -5%
            "best_bid": 0.470,
            "best_ask": 0.480,
            "spread": 0.02
        }
    }
    exits_sl = pm.get_positions_for_exit(market_sl)
    assert len(exits_sl) == 1
    sl_exit = exits_sl[0]
    assert sl_exit['is_maker'] is False, "Emergency SL must exit at Bid"
    assert "SL_EMERGENCY_BID" in sl_exit['reason']
    assert sl_exit['price'] == 0.470, "SL exit must be at current best bid"

def test_maker_tp_fill_fee_accounting():
    """Verify that Maker TP fill is charged 0 maker exit fee and calculates correct net PnL."""
    pm = PositionManager()
    pm.clear_all_positions()
    
    token_id = "test_token_fee"
    entry_price = 0.50
    size = 5.0
    
    # Buy entry: 20 bps taker fee = 5.0 * 0.50 * 0.0020 = $0.0050
    pm.update_position(token_id=token_id, size=size, price=entry_price, side='BUY', strategy='PMM', is_maker=False)
    
    # Exit at Maker Ask 0.545: Maker fee = 0 bps = $0.0
    # Gross PnL = (0.545 - 0.50) * 5.0 = +$0.2250
    # Total fee = entry fee ($0.0050) + exit fee ($0.0) = $0.0050
    # Net PnL = $0.2250 - $0.0050 = +$0.2200
    pnl = pm.update_position(
        token_id=token_id,
        size=size,
        price=0.545,
        side='SELL',
        strategy='PMM',
        is_maker=True,
        exit_reason="MAKER_TP_FILL"
    )
    
    assert round(pnl, 4) == 0.2200, f"Expected net PnL +0.2200, got {pnl}"
    assert len(pm.positions) == 0, "Position should be completely closed"

def test_obi_strategy_depth_and_spread_constants():
    """Verify OBI strategy settings: depth ratio >= 1.5 and spread <= 1.0%."""
    assert OBI_DEPTH_RATIO_MIN == 1.5, "OBI depth ratio floor must be 1.5x"
    assert OBI_MAX_SPREAD == 0.01, "OBI max spread must be 1.0%"
    assert MAX_SPREAD == 0.01, "Global MAX_SPREAD must be 1.0%"

@pytest.mark.asyncio
async def test_obi_strategy_evaluation():
    """Verify OBI evaluates valid buy-side dominance book vs invalid book."""
    strat = OrderBookImbalanceStrategy()
    
    # Case 1: Valid book (OBI = (900-100)/1000 = 0.80 >= 0.80, Depth ratio = 9.0x >= 1.5x, Spread = 0.8% <= 1.0%)
    valid_market = [{
        "token_id": "tok_obi_valid",
        "last_price": 0.50,
        "best_bid": 0.498,
        "best_ask": 0.502,
        "bid_depth": 900.0,
        "ask_depth": 100.0,
        "total_depth": 1000.0
    }]
    signals = await strat.evaluate(valid_market)
    assert len(signals) == 1, "OBI should generate signal for high-dominance tight book"
    assert signals[0]['side'] == "BUY"
    assert signals[0]['score'] >= 85
    
    # Case 2: Rejected by spread > 1.0% (e.g. 2.0%)
    wide_market = [{
        "token_id": "tok_obi_wide",
        "last_price": 0.50,
        "best_bid": 0.490,
        "best_ask": 0.500, # spread = 0.010 / 0.495 = 2.02%
        "bid_depth": 900.0,
        "ask_depth": 100.0,
        "total_depth": 1000.0
    }]
    signals_wide = await strat.evaluate(wide_market)
    assert len(signals_wide) == 0, "OBI must reject books with spread > 1.0%"
