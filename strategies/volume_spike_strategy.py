import collections
from app.signal_engine import BaseStrategy
from app.logger import logger
from app.config import (
    TEST_MODE, GLOBAL_MIN_PRICE, GLOBAL_MAX_PRICE,
    GLOBAL_MIN_LIQUIDITY, GLOBAL_MAX_SPREAD
)

class VolumeSpikeStrategy(BaseStrategy):
    def __init__(self, window_size=8, spike_threshold=3.0):
        super().__init__("VolumeSpikeStrategy")
        self.window_size = window_size
        self.spike_threshold = spike_threshold
        self.volume_history = collections.defaultdict(lambda: collections.deque(maxlen=window_size))
        self.change_history = collections.defaultdict(lambda: collections.deque(maxlen=window_size))
        self.price_history = collections.defaultdict(lambda: collections.deque(maxlen=3))

    async def evaluate(self, market_data):
        signals = []
        for market in market_data:
            token_id = market.get("token_id")
            current_volume = market.get("volume_24h")
            current_price = market.get("last_price")
            
            if token_id is None or current_volume is None or current_price is None:
                continue
                
            # Maintain histories
            self.price_history[token_id].append(current_price)
            
            history_vol = self.volume_history[token_id]
            prev_vol = history_vol[-1] if history_vol else None
            history_vol.append(current_volume)
            
            if prev_vol is None:
                continue
                
            # Calculate volume change rate
            vol_change = max(0.0, current_volume - prev_vol)
            if vol_change <= 0:
                continue
                
            history_changes = list(self.change_history[token_id])
            self.change_history[token_id].append(vol_change)
            
            if len(history_changes) < 3:
                continue
                
            # Calculate average historical volume change rate
            avg_change = sum(history_changes) / len(history_changes)
            if avg_change <= 0:
                continue
                
            # Compute relative volume spike
            rel_spike = vol_change / avg_change
            
            # Check spike condition (in test mode, lower threshold to 1.5x)
            threshold = self.spike_threshold if not TEST_MODE else 1.2
            if rel_spike < threshold:
                continue
                
            # Filter by basic pricing boundary
            if (current_price < GLOBAL_MIN_PRICE or current_price > GLOBAL_MAX_PRICE) and not TEST_MODE:
                continue
                
            # Determine direction from recent price movement
            price_history_list = list(self.price_history[token_id])
            if len(price_history_list) < 2:
                continue
                
            price_delta = price_history_list[-1] - price_history_list[0]
            if abs(price_delta) < 1e-8:
                continue
                
            # Positive price delta + volume spike -> BUY.
            # Negative price delta + volume spike -> shorting is not supported by CLOB BUY, but we can trade the opposite outcome NO token if outcome is YES,
            # or if the token outcome matches our side. To keep it robust, we only buy when price movement is in the same direction:
            # Let's check outcome:
            outcome = market.get("outcome", "Yes")
            
            # Determine side & token targeting
            side = "BUY"
            
            # Skip if flat
            if price_delta > 0:
                direction_reason = "Spike Up"
            else:
                # Downward volume breakout (we don't place short sells, so we skip downward breakouts to protect capital)
                continue

            best_bid = market.get("best_bid", 0)
            best_ask = market.get("best_ask", 0)
            abs_spread = best_ask - best_bid if best_ask > 0 else 0.05
            
            # Score based on spike intensity
            score = min(100, 50 + int(rel_spike * 12))
            
            logger.info(
                f"VolumeSpikeStrategy SIGNAL: {token_id[:20]} | RelSpike: {rel_spike:.2f}x | "
                f"VolChange: {vol_change:.1f} | AvgChange: {avg_change:.1f} | PriceDelta: {price_delta:+.4f} | Score: {score}"
            )
            
            signals.append({
                "token_id": token_id,
                "price": current_price,
                "size": 10,
                "side": side,
                "delta": price_delta,
                "spread": abs_spread,
                "liquidity": market.get("bid_depth", 0) + market.get("ask_depth", 0),
                "score": score,
                "strategy": self.name,
                "reason": f"Volume spike {rel_spike:.2f}x with price delta {price_delta:+.4f}"
            })
            
        return signals
