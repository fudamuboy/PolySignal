from app.risk_manager import RiskManager
from app.config import ESTIMATED_FEE_BPS

rm = RiskManager()
signal = {"token_id": "TEST", "delta": 0.05, "score": 0.8}
market = {"spread": 0.04}
# EV expects: delta (0.05) - (spread (0.04) + 0.004 fees + 0.001 margin) = 0.05 - 0.045 = +0.005. So it should PASS
passed = rm.evaluate_ev(signal, market)
print(f"Spread 0.04 test (delta 0.05): {passed}")

signal2 = {"token_id": "TEST2", "delta": 0.05, "score": 0.8}
market2 = {"spread": 0.06}
# EV expects: delta (0.05) - (spread (0.06) + 0.005) = -0.015. So it should FAIL
passed2 = rm.evaluate_ev(signal2, market2)
print(f"Spread 0.06 test (delta 0.05): {passed2}")

