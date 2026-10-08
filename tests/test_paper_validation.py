import pytest
import time
from app.paper_validation_tracker import PaperValidationTracker

def test_paper_tracker_touch_does_not_fill():
    tracker = PaperValidationTracker(total_capital=100.0)

    order_id = "paper_token_a_BUY_123"
    tracker.record_order_placement(order_id, "token_a", 0.50, 10.0, "BUY",
                                   {"best_bid": 0.49, "best_ask": 0.51, "bid_depth": 500.0})
    pending_orders = [{"order_id": order_id, "token_id": "token_a", "price": 0.50, "size": 10.0, "side": "BUY"}]

    # Mid / last price sitting on our limit is not evidence that we were filled
    market_data_touch = {
        "token_a": {"token_id": "token_a", "best_bid": 0.49, "best_ask": 0.51, "last_price": 0.50, "spread": 0.04}
    }
    for _ in range(20):
        assert tracker.process_resting_tick(pending_orders, market_data_touch) == []
    assert order_id in tracker.order_queue_depths

def test_paper_tracker_crossover_fills_at_limit():
    tracker = PaperValidationTracker(total_capital=100.0)

    order_id = "paper_token_b_BUY_456"
    tracker.record_order_placement(order_id, "token_b", 0.50, 10.0, "BUY")
    pending_orders = [{"order_id": order_id, "token_id": "token_b", "price": 0.50, "size": 10.0, "side": "BUY"}]

    # Ask trades through our bid: a maker is filled at its own limit, not at the new ask
    market_data_cross = {
        "token_b": {"token_id": "token_b", "best_bid": 0.47, "best_ask": 0.48, "last_price": 0.48, "spread": 0.04}
    }
    fills = tracker.process_resting_tick(pending_orders, market_data_cross)
    assert len(fills) == 1
    assert fills[0]["fill_price"] == 0.50
    assert fills[0]["fee"] == 0.0
    assert order_id not in tracker.order_queue_depths

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


class _Trades:
    """Minimal stand-in for WebsocketClient.get_trades_since."""
    def __init__(self):
        self.trades = {}

    def add(self, token_id, price, size):
        self.trades.setdefault(token_id, []).append({"ts": time.time() + 1, "price": price, "size": size, "side": "SELL"})

    def get_trades_since(self, token_id, since_ts):
        return [t for t in self.trades.get(token_id, []) if t["ts"] > since_ts]


def _book_md(bids, asks):
    return {
        "best_bid": bids[0][0], "best_ask": asks[0][0], "spread": 0.02,
        "orderbook": {"bids": [{"price": str(p), "size": str(s)} for p, s in bids],
                      "asks": [{"price": str(p), "size": str(s)} for p, s in asks]},
    }


def test_trades_at_our_level_consume_queue_then_fill():
    tracker = PaperValidationTracker(total_capital=100.0)
    trades = _Trades()
    tracker.trade_source = trades
    md = _book_md([(0.50, 100.0)], [(0.52, 100.0)])

    # Joining the 0.50 bid: 100 shares are ahead of us
    tracker.record_order_placement("o1", "tok", 0.50, 10.0, "BUY", md)
    order = {"order_id": "o1", "token_id": "tok", "price": 0.50, "size": 10.0, "side": "BUY"}

    trades.add("tok", 0.50, 95.0)
    assert tracker.process_resting_tick([order], {"tok": md}) == []
    assert tracker.order_queue_depths["o1"]["remaining"] == 5.0

    # Next 8 shares: 5 finish the queue, 3 fill us (partial)
    trades.trades["tok"] = []
    trades.add("tok", 0.50, 8.0)
    fills = tracker.process_resting_tick([order], {"tok": md})
    assert fills[0]["fill_size"] == 3.0 and fills[0]["fully_filled"] is False
    assert fills[0]["fill_price"] == 0.50


def test_trade_through_our_price_fills_fully():
    tracker = PaperValidationTracker(total_capital=100.0)
    trades = _Trades()
    tracker.trade_source = trades
    md = _book_md([(0.50, 1000.0)], [(0.52, 100.0)])
    tracker.record_order_placement("o2", "tok", 0.50, 10.0, "BUY", md)
    order = {"order_id": "o2", "token_id": "tok", "price": 0.50, "size": 10.0, "side": "BUY"}

    trades.add("tok", 0.49, 1.0)  # sellers went below our bid: our level was cleared
    fills = tracker.process_resting_tick([order], {"tok": md})
    assert fills[0]["fill_size"] == 10.0 and fills[0]["fully_filled"] is True
    assert "o2" not in tracker.order_queue_depths


def test_order_inside_spread_is_first_in_queue():
    tracker = PaperValidationTracker(total_capital=100.0)
    md = _book_md([(0.50, 1000.0)], [(0.53, 100.0)])
    tracker.record_order_placement("o3", "tok", 0.51, 10.0, "BUY", md)
    assert tracker.order_queue_depths["o3"]["remaining"] == 0.0
