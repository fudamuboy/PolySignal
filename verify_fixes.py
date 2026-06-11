import asyncio
from app.risk_manager import RiskManager
from app.position_manager import PositionManager
from app.config import BASE_ORDER_SIZE, MAX_CAPITAL_PER_TRADE
from unittest.mock import MagicMock

def test_risk_manager_fixes():
    print("Testing RiskManager Fixes...")
    
    # Setup mock PositionManager
    pm = PositionManager()
    pm.db = MagicMock()
    pm.positions = {}
    
    rm = RiskManager(position_manager=pm)
    rm.max_capital_per_trade = 10.0
    
    rm.update_after_trade(success=True, token_id="T4")
    signal_cooldown = {"token_id": "T4", "side": "BUY", "price": 0.5, "score": 0.8, "delta": 0.20}
    allowed_cd = rm.validate_trade(signal_cooldown)
    print(f"Trade during Cooldown Allowed (Expect False): {allowed_cd}")
    assert not allowed_cd, "Trade allowed during active market cooldown!"

    print("ALL TESTS PASSED!")

if __name__ == "__main__":
    test_risk_manager_fixes()
