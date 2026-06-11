from app.config import TEST_MODE
from app.signal_engine import BaseStrategy
from app.logger import logger
import collections

class ConsistencyStrategy(BaseStrategy):
    def __init__(self, jump_threshold=0.01, mean_reversion_threshold=0.02):
        super().__init__("ConsistencyStrategy")
        self.jump_threshold = jump_threshold if not TEST_MODE else 0.001
        self.mean_reversion_threshold = mean_reversion_threshold if not TEST_MODE else 0.001
        self.price_history = collections.defaultdict(lambda: collections.deque(maxlen=60)) 

    async def evaluate(self, market_data):
        signals = []
        for market in market_data:
            token_id = market.get("token_id")
            current_price = market.get("last_price")
            if current_price is None: continue

            history = self.price_history[token_id]
            if not history:
                history.append(current_price)
                continue

            avg_price = sum(history) / len(history)
            last_price = history[-1]
            price_change = abs(current_price - last_price)
            
            # 1. Detect abnormal jumps
            if price_change >= self.jump_threshold:
                signals.append({
                    "token_id": token_id,
                    "price": current_price,
                    "size": 10,
                    "side": "SELL" if current_price > last_price else "BUY",
                    "delta": price_change,
                    "score": 0.8,
                    "strategy": self.name,
                    "metadata": {"type": "jump", "change": price_change}
                })

            # 2. Detect overreaction (Mean Reversion)
            deviation = current_price - avg_price
            if abs(deviation) >= self.mean_reversion_threshold:
                signals.append({
                    "token_id": token_id,
                    "price": avg_price, # Aim for the mean
                    "size": 10,
                    "side": "SELL" if deviation > 0 else "BUY",
                    "delta": abs(deviation),
                    "score": min(0.9, 0.5 + abs(deviation)),
                    "strategy": self.name,
                    "metadata": {"type": "mean_reversion", "deviation": deviation}
                })

            history.append(current_price)
        return signals
