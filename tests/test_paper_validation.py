import pytest
import time
from app.paper_validation_tracker import PaperValidationTracker

def test_paper_tracker_order_queue_depletion():
    print("Testing paper tracker order queue depletion...")
    tracker = PaperValidationTracker(total_capital=100.0)
    
    # Setup mock order placement
    order_id = "paper_token_a_BUY_123"
    token_id = "token_a"
    price = 0.50
    size = 10.0
    side = "BUY"
    
    market_data = {
        "best_bid": 0.49,
        "best_ask": 0.51,
        "bid_depth": 500.0  # initial depth in USD -> 500 / 0.50 = 1000 shares
    }
    
    tracker.record_order_placement(order_id, token_id, price, size, side, market_data)
    
    # Verify recorded initial depth
    assert order_id in tracker.order_queue_depths
    assert tracker.order_queue_depths[order_id]["initial"] == 1000.0
    assert tracker.order_queue_depths[order_id]["remaining"] == 1000.0
    
    # Simulating tick: market touches price level -> queue depletes
    pending_orders = [{"order_id": order_id, "token_id": token_id, "price": price, "size": size, "side": side}]
    
    # Mock tick where last_price = price (touches)
    market_data_touch = {
        "token_a": {
            "token_id": "token_a",
            "best_bid": 0.49,
            "best_ask": 0.51,
            "last_price": 0.50, # touches limit
            "spread": 0.04
        }
    }
    
    # Run a resting tick to trigger depletion
    # We monkeypatch random to return a fixed depletion of 100.0
    import random
    original_uniform = random.uniform
    random.uniform = lambda a, b: 100.0
    
    fills_1 = tracker.process_resting_tick(pending_orders, market_data_touch)
    assert len(fills_1) == 0  # Not filled yet, queue only depleted
    assert tracker.order_queue_depths[order_id]["remaining"] == 900.0
    
    # Deplete rest of queue to 0
    tracker.order_queue_depths[order_id]["remaining"] = 10.0
    fills_2 = tracker.process_resting_tick(pending_orders, market_data_touch)
    assert len(fills_2) == 1
    assert fills_2[0]["order_id"] == order_id
    assert fills_2[0]["fill_price"] == price
    
    # Restore original random
    random.uniform = original_uniform
    print("✅ Queue depletion fill verified!")

def test_paper_tracker_crossover_instant_fill():
    print("Testing paper tracker crossover instant fill...")
    tracker = PaperValidationTracker(total_capital=100.0)
    
    order_id = "paper_token_b_BUY_456"
    token_id = "token_b"
    price = 0.50
    size = 10.0
    side = "BUY"
    
    tracker.record_order_placement(order_id, token_id, price, size, side)
    
    pending_orders = [{"order_id": order_id, "token_id": token_id, "price": price, "size": size, "side": side}]
    
    # Mock tick where price crosses past limit (best_ask drops below P_limit)
    market_data_cross = {
        "token_b": {
            "token_id": "token_b",
            "best_bid": 0.47,
            "best_ask": 0.48,  # best ask drops strictly below our buy limit of 0.50
            "last_price": 0.48,
            "spread": 0.04
        }
    }
    
    fills = tracker.process_resting_tick(pending_orders, market_data_cross)
    assert len(fills) == 1
    assert fills[0]["order_id"] == order_id
    assert fills[0]["fill_price"] == 0.48  # Instant fill gets executed at best ask price
    assert order_id not in tracker.order_queue_depths
    
    print("✅ Strict crossover instant fill verified!")

def test_paper_tracker_missed_opportunities():
    print("Testing paper tracker missed opportunities rate...")
    tracker = PaperValidationTracker(total_capital=100.0)
    
    # Setup mock rejected signals
    signal_buy = {"token_id": "token_c", "side": "BUY", "price": 0.50, "score": 0.65}
    signal_sell = {"token_id": "token_d", "side": "SELL", "price": 0.60, "score": 0.62}
    
    tracker.record_rejected_signal(signal_buy, mid_price=0.50)
    tracker.record_rejected_signal(signal_sell, mid_price=0.60)
    
    assert "token_c" in tracker.active_rejections
    assert "token_d" in tracker.active_rejections
    
    # 1. Simulating ticks where token_c moves in profitable direction (BUY touches TP target)
    # TP Target for BUY 0.50 = 0.50 + 0.07 = 0.57
    market_data_c_hits = {
        "token_c": {"token_id": "token_c", "last_price": 0.57}, # Touched TP target
        "token_d": {"token_id": "token_d", "last_price": 0.60}  # Flat
    }
    tracker.process_rejections_tick(market_data_c_hits)
    
    assert tracker.resolved_rejections == 1
    assert tracker.missed_profitable_opportunities == 1
    
    # 2. Simulating ticks where token_d hits SL target (Loss avoided!)
    # SL Target for SELL 0.60 = 0.60 + 0.05 = 0.65 (Loss if we sold, price goes up)
    market_data_d_hits = {
        "token_d": {"token_id": "token_d", "last_price": 0.65} # Touched SL target
    }
    tracker.process_rejections_tick(market_data_d_hits)
    
    assert tracker.resolved_rejections == 2
    assert tracker.missed_profitable_opportunities == 1 # Stays 1, token_d was a saved loss!
    
    # 3. Check rate: 1 / 2 = 50.0%
    # Fetch report summary to verify
    avg_inv = tracker.exposure_sum / tracker.ticks_count if tracker.ticks_count > 0 else 0.0
    fill_rate = (tracker.total_fills / tracker.total_placed * 100) if tracker.total_placed > 0 else 0.0
    missed_opp_rate = (tracker.missed_profitable_opportunities / tracker.resolved_rejections * 100) if tracker.resolved_rejections > 0 else 0.0
    
    assert missed_opp_rate == 50.0
    print("✅ Missed opportunities outcome and rate calculations verified!")
