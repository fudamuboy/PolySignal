import pytest
import asyncio
from strategies.momentum_strategy import MomentumStrategy
from app.risk_manager import RiskManager
from app.config import SOLO_MOMENTUM_MIN_SCORE

@pytest.mark.asyncio
async def test_momentum_spread_check():
    # Window size 2 for quick testing
    # Set min_liquidity to 0 to avoid rejection there
    strategy = MomentumStrategy(window_size=2, momentum_threshold=0.02, min_liquidity=0)
    
    token_id = "test_token"
    
    # Tick 1: Price 0.50
    await strategy.evaluate([{"token_id": token_id, "last_price": 0.50}])
    
    # Tick 2: Price 0.53 (Delta = 0.03)
    # Case A: Spread is 0.04 (Delta < Spread) -> Should skip
    market_data_a = {
        "token_id": token_id,
        "last_price": 0.53,
        "best_bid": 0.51,
        "best_ask": 0.55, # Spread 0.04
        "bid_depth": 1000,
        "ask_depth": 1000
    }
    signals_a = await strategy.evaluate([market_data_a])
    assert len(signals_a) == 0
    assert token_id not in strategy.pending_signals # Check it didn't even go pending
    
    # Tick 3: Price 0.56 again with small spread (0.01) -> Should evaluate signal
    market_data_b = {
        "token_id": token_id,
        "last_price": 0.56,
        "best_bid": 0.55,
        "best_ask": 0.56, # Spread 0.01
        "bid_depth": 1000,
        "ask_depth": 1000
    }
    signals_b = await strategy.evaluate([market_data_b])
    assert len(signals_b) == 1
    assert signals_b[0]["token_id"] == token_id
    assert signals_b[0]["side"] == "BUY"
    assert signals_b[0]["price"] == 0.56

@pytest.mark.asyncio
async def test_risk_manager_solo_momentum_score():
    # We need a mock position manager to avoid errors in validate_trade
    class MockPM:
        def __init__(self):
            self.positions = {}
        def get_open_positions_count(self): return 0
        def get_open_positions_summary(self): return "None"
        
    rm = RiskManager(position_manager=MockPM())
    
    # Test SOLO_MOMENTUM signal with low score
    low_score_signal = {
        "token_id": "test_token",
        "strategy": "SOLO_MOMENTUM",
        "score": 0.6,
        "side": "BUY",
        "price": 0.5,
        "delta": 0.07  # RR = 0.07/0.05 = 1.4 >= 1.2 -> clears RR filter, rejected by score
    }
    
    market_data = {
        "token_id": "test_token",
        "spread": 0.01,
        "bid_depth": 2000,
        "ask_depth": 2000,
        "last_price": 0.5,
        "best_bid": 0.495,
        "best_ask": 0.505
    }
    
    # Should be rejected (score 0.6 < SOLO_MOMENTUM_MIN_SCORE 0.75)
    assert rm.validate_trade(low_score_signal, market_data) is False
    
    # High score should be accepted (0.8 >= 0.75, delta clears RR filter)
    high_score_signal = low_score_signal.copy()
    high_score_signal["score"] = 0.8
    assert rm.validate_trade(high_score_signal, market_data) is True

@pytest.mark.asyncio
async def test_ev_calculation_robustness():
    rm = RiskManager()
    
    # Signal with 2% move on 0.50 price = 4% delta_pct
    # We set score to 90 (0.90) to force an aggressive order, which retains the spread penalty
    signal_aggressive = {
        "token_id": "test_token",
        "delta": 0.01, # 1 cent
        "price": 0.50, # 2% delta_pct
        "score": 90
    }
    
    # Case 1: Spread is 0.01 (1%) -> Required edge ~ 1% + fees (0.4%) + margin (0.1%) = 1.5%
    # Expected profit = 2% - 1.5% = 0.5% (Positive)
    market_data_1 = {
        "spread": 0.01,
        "last_price": 0.50
    }
    assert rm.evaluate_ev(signal_aggressive, market_data_1) is True
    
    # Case 2: Spread is 0.025 (2.5%) -> Required edge ~ 2.5% + 0.4% + 0.1% = 3.0%
    # Expected profit = 2% - 3.0% = -1.0% (Negative)
    market_data_2 = {
        "spread": 0.025,
        "last_price": 0.50
    }
    assert rm.evaluate_ev(signal_aggressive, market_data_2) is False

    # Case 3: Passive signal (score = 80) on Case 2 (spread = 2.5%) -> No spread penalty
    # Required edge = fees (0.4%) + margin (0.1%) = 0.5%
    # Expected profit = 2% - 0.5% = 1.5% (Positive)
    signal_passive = signal_aggressive.copy()
    signal_passive["score"] = 80
    assert rm.evaluate_ev(signal_passive, market_data_2) is True
