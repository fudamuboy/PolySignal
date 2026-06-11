import asyncio
import time
import pytest
from unittest.mock import MagicMock
from app.market_filter import MarketFilter
from app.signal_engine import SignalEngine, BaseStrategy
from app.risk_manager import RiskManager
from app.position_manager import PositionManager

def test_market_ranking():
    print("\n--- Testing Market Ranking ---")
    mf = MarketFilter()
    markets = [
        {"condition_id": "M1", "volume_24h": "100", "24hr_change": "0.01", "liquidity": "100", "active": True, "tokens": [{"price": "0.5", "token_id": "M1_T"}]},
        {"condition_id": "M2", "volume_24h": "50000", "24hr_change": "0.15", "liquidity": "5000", "active": True, "tokens": [{"price": "0.5", "token_id": "M2_T"}]}, # Best
        {"condition_id": "M3", "volume_24h": "5000", "24hr_change": "0.05", "liquidity": "500", "active": True, "tokens": [{"price": "0.5", "token_id": "M3_T"}]}
    ]
    ranked = mf.filter_markets(markets)
    print(f"Top 1: {ranked[0]['condition_id']} (Score: {ranked[0]['opportunity_score']:.2f})")
    assert ranked[0]['condition_id'] == "M2"
    print("✅ Market ranking verified!")

@pytest.mark.asyncio
async def test_signal_confirmation():
    print("\n--- Testing Signal Confirmation ---")
    class MockStrat(BaseStrategy):
        def __init__(self, name, sid): super().__init__(name); self.sid = sid
        async def evaluate(self, market_data):
            return [{"token_id": "T1", "side": "BUY", "price": 0.5, "delta": 0.01, "score": 0.6, "strategy": self.name}]

    engine = SignalEngine([MockStrat("S1", 1), MockStrat("S2", 2)], confidence_threshold=0.5)
    signals, sig_stats = await engine.generate_signals([])
    
    print(f"Signal count: {len(signals)}")
    print(f"Signal score: {signals[0]['score']}")
    # Initial 0.6 + 0.15 boost = 0.75
    assert signals[0]['score'] >= 0.75
    print("✅ Signal confirmation verified!")

def test_risk_profitability():
    print("\n--- Testing Risk Profitability filters ---")
    rm = RiskManager()
    
    # 1. Price Range checks
    low_p  = {"token_id": "T_LOW",  "price": 0.1, "score": 0.8, "delta": 0.01}
    high_p = {"token_id": "T_HIGH", "price": 0.9, "score": 0.8, "delta": 0.01}
    # mid_p delta=0.07: RR = 0.07/0.05 = 1.4 >= 1.2 -> passes RR filter
    mid_p  = {"token_id": "T_MID",  "price": 0.5, "score": 0.8, "delta": 0.07}
    
    assert rm.validate_trade(low_p)  == False  # Rejected: price too low
    assert rm.validate_trade(high_p) == False  # Rejected: price too high
    assert rm.validate_trade(mid_p)  == True   # Accepted: price OK + RR OK
    
    # 1b. RR Filter: delta=0.02 -> RR=0.4 < 0.50 -> must be rejected
    low_rr_p = {"token_id": "T_LOWRR", "price": 0.5, "score": 0.8, "delta": 0.02}
    assert rm.validate_trade(low_rr_p) == False, "RR filter must reject delta=0.02 (RR=0.4)"
    print("  RR filter: delta=0.02 correctly rejected (RR=0.40 < 0.50)")
    
    # 2. Capital Scaling
    size_norm = rm.calculate_position_size(mid_p, {})
    high_score = {"token_id": "T_MID", "price": 0.5, "score": 0.95, "delta": 0.07}
    size_high = rm.calculate_position_size(high_score, {})
    
    print(f"  Norm Size (0.8): {size_norm} | High Size (0.95): {size_high}")
    assert size_high > size_norm
    
    # 3. Market Cooldown
    rm.update_after_trade(True, token_id="T_MID", pnl=0.1)
    assert rm.validate_trade(mid_p) == False  # Rejected due to cooldown
    print("✅ Risk profitability filters verified!")

if __name__ == "__main__":
    test_market_ranking()
    asyncio.run(test_signal_confirmation())
    test_risk_profitability()
