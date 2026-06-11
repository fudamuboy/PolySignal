import pytest
from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.config import GAMMA_BID, GAMMA_ASK, PMM_TOTAL_CAPITAL

def test_asymmetric_inventory_skew_math():
    print("Testing Asymmetric Inventory Skew Math...")
    pm = PositionManager()
    rm = RiskManager(position_manager=pm)
    
    # Ensure memory is clean
    pm.positions = {}
    
    token_id = "test_token_skew"
    mid_price = 0.50
    
    # 1. No position: skew should be exactly 0.0
    skew_buy_none = rm.calculate_inventory_skew(token_id, mid_price, "BUY")
    skew_sell_none = rm.calculate_inventory_skew(token_id, mid_price, "SELL")
    assert skew_buy_none == 0.0
    assert skew_sell_none == 0.0
    
    # 2. Setup position: 100 units (exposure = 100 * 0.50 = 50.0 USD)
    # Total Capital = 100.0 USD -> Inventory Ratio I = 50.0 / 100.0 = 50.0%
    pm.positions = {
        token_id: {"size": 100.0, "avg_price": 0.50, "strategy": "PMM"}
    }
    
    # Expected skews:
    # BUY (Entry): I * GAMMA_BID = 0.5 * 0.08 = 0.04
    # SELL (Exit): I * GAMMA_ASK = 0.5 * 0.02 = 0.01
    skew_buy = rm.calculate_inventory_skew(token_id, mid_price, "BUY", total_capital=100.0)
    skew_sell = rm.calculate_inventory_skew(token_id, mid_price, "SELL", total_capital=100.0)
    
    assert abs(skew_buy - 0.04) < 1e-6
    assert abs(skew_sell - 0.01) < 1e-6
    
    # 3. Setup position: 200 units (exposure = 200 * 0.40 = 80.0 USD)
    # Total Capital = 100.0 USD -> Inventory Ratio I = 80.0 / 100.0 = 80.0%
    pm.positions = {
        token_id: {"size": 200.0, "avg_price": 0.40, "strategy": "PMM"}
    }
    
    mid_price_new = 0.40
    # Expected skews:
    # BUY (Entry): I * GAMMA_BID = 0.8 * 0.08 = 0.064
    # SELL (Exit): I * GAMMA_ASK = 0.8 * 0.02 = 0.016
    skew_buy_large = rm.calculate_inventory_skew(token_id, mid_price_new, "BUY", total_capital=100.0)
    skew_sell_large = rm.calculate_inventory_skew(token_id, mid_price_new, "SELL", total_capital=100.0)
    
    assert abs(skew_buy_large - 0.064) < 1e-6
    assert abs(skew_sell_large - 0.016) < 1e-6
    
    print("✅ Asymmetric skew math successfully verified!")

def test_asymmetric_skew_parameters_custom():
    print("Testing Asymmetric Custom Parameters...")
    pm = PositionManager()
    rm = RiskManager(position_manager=pm)
    
    token_id = "test_custom_skew"
    mid_price = 0.60
    
    # Position: 50 units (exposure = 50 * 0.60 = 30.0 USD)
    pm.positions = {
        token_id: {"size": 50.0, "avg_price": 0.60, "strategy": "PMM"}
    }
    
    # Capital = 50.0 -> I = 30.0 / 50.0 = 0.60 (60% imbalance)
    # Custom Gamma: Bid = 0.10, Ask = 0.05
    # Expected:
    # BUY: 0.60 * 0.10 = 0.06
    # SELL: 0.60 * 0.05 = 0.03
    skew_buy = rm.calculate_inventory_skew(
        token_id=token_id,
        mid_price=mid_price,
        side="BUY",
        total_capital=50.0,
        gamma_bid=0.10,
        gamma_ask=0.05
    )
    
    skew_sell = rm.calculate_inventory_skew(
        token_id=token_id,
        mid_price=mid_price,
        side="SELL",
        total_capital=50.0,
        gamma_bid=0.10,
        gamma_ask=0.05
    )
    
    assert abs(skew_buy - 0.06) < 1e-6
    assert abs(skew_sell - 0.03) < 1e-6
    
    print("✅ Custom skew parameters verified successfully!")
