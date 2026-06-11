import pytest
import time
from app.position_manager import PositionManager

@pytest.mark.asyncio
async def test_exit_logic():
    print("\n--- Testing Exit Logic ---")
    pm = PositionManager()
    pm.clear_all_positions()
    
    # 1. Setup a position to hit Take Profit (entry 0.50, current 0.65 -> +30%)
    token_tp = "0x_TP_TOKEN"
    pm.update_position(token_tp, 100, 0.50, "BUY", "MomentumStrategy")
    
    # 2. Setup a position to hit Stop Loss (entry 0.50, current 0.35 -> -30%)
    token_sl = "0x_SL_TOKEN"
    pm.update_position(token_sl, 100, 0.50, "BUY", "MomentumStrategy")
    
    # 3. Setup a position for Time Stop (Mock entry_time to 25h ago)
    token_time = "0x_TIME_TOKEN"
    pm.update_position(token_time, 100, 0.50, "BUY", "MomentumStrategy")
    pm.positions[token_time]['entry_time'] = time.time() - 90000 # ~25h ago
    
    # Current prices for evaluation (mocking the market_map structure)
    market_map = {
        token_tp: {
            "token_id": token_tp,
            "best_bid": 0.65,
            "last_price": 0.65,
            "spread": 0.02
        },
        token_sl: {
            "token_id": token_sl,
            "best_bid": 0.35,
            "last_price": 0.35,
            "spread": 0.02
        },
        token_time: {
            "token_id": token_time,
            "best_bid": 0.505,
            "last_price": 0.505,
            "spread": 0.02
        }
    }
    
    exits = pm.get_positions_for_exit(market_map)
    
    print(f"Detected {len(exits)} exit candidates.")
    for e in exits:
        print(f"Token: {e['token_id']} | Reason: {e['reason']}")
        
    assert any(e['token_id'] == token_tp and "TP" in e['reason'] for e in exits), f"TP not detected for {token_tp}"
    assert any(e['token_id'] == token_sl and "SL" in e['reason'] for e in exits), f"SL not detected for {token_sl}"
    
    # Time-stop intentionally removed in Positive RR Mode.
    # Positions now exit ONLY via TP or SL to prevent uncontrolled exits.
    # A 25h-old position at +1% should NOT be force-closed if TP/SL not hit.
    assert all(e['token_id'] != token_time for e in exits), \
        "Time-stop should be DISABLED — position at +1% must NOT be force-closed by time"
    
    print(f"✅ Exit logic verification passed! TP={len([e for e in exits if 'TP' in e['reason']])} SL={len([e for e in exits if 'SL' in e['reason']])} Time=0 (disabled)")
