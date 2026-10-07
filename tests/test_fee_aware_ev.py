from app.risk_manager import RiskManager, is_aggressive_signal
from app.config import DB_PATH, STORAGE_DIR


def test_tests_use_isolated_database():
    assert STORAGE_DIR not in DB_PATH.parents


def test_ev_rejects_taker_trade_eaten_by_market_fees():
    rm = RiskManager()
    signal = {"token_id": "t", "delta": 0.01, "price": 0.50, "score": 90}  # 2% expected move
    # Without fee info the legacy flat fee leaves a positive edge...
    assert rm.evaluate_ev(signal, {"spread": 0.01}) is True
    # ...but a crypto market charges 0.07 * p(1-p) = 1.75% per taker leg
    crypto = {"spread": 0.01, "feeSchedule": {"rate": 0.07, "exponent": 1, "takerOnly": True}}
    assert rm.evaluate_ev(signal, crypto) is False


def test_passive_orders_pay_no_taker_fee():
    rm = RiskManager()
    signal = {"token_id": "t", "delta": 0.01, "price": 0.50, "score": 75}
    crypto = {"spread": 0.03, "feeSchedule": {"rate": 0.07, "exponent": 1, "takerOnly": True}}
    assert rm.evaluate_ev(signal, crypto) is True


def test_aggressiveness_matches_execution_rules():
    assert is_aggressive_signal({"score": 90, "strategy": "MomentumStrategy"})
    assert not is_aggressive_signal({"score": 84, "strategy": "MomentumStrategy"})
    assert is_aggressive_signal({"score": 82, "strategy": "NewsStrategy"})
    assert is_aggressive_signal({"score": 0.9, "strategy": "MomentumStrategy"})
    assert not is_aggressive_signal({"score": 95, "strategy": "TwoSidedMMStrategy"})
