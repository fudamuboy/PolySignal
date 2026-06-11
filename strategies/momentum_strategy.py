import collections
from app.signal_engine import BaseStrategy
from app.logger import logger
from app.config import (
    TEST_MODE, MOMENTUM_CONSISTENCY_RATIO, MOMENTUM_MAGNITUDE_STRONG, 
    MOMENTUM_MAGNITUDE_MASSIVE, GLOBAL_MIN_PRICE, GLOBAL_MAX_PRICE,
    GLOBAL_MIN_LIQUIDITY, MIN_MOVE_THRESHOLD
)

class MomentumStrategy(BaseStrategy):
    def __init__(self, window_size=8, momentum_threshold=0.025, min_liquidity=5000):
        super().__init__("MomentumStrategy")
        self.window_size = window_size  # Increased from 3 to 8
        self.momentum_threshold = momentum_threshold
        self.min_liquidity = min_liquidity
        self.price_history = collections.defaultdict(lambda: collections.deque(maxlen=window_size))
        self.pending_signals = {}
        self.MIN_EDGE = 0.015

    async def evaluate(self, market_data):
        signals = []
        for market in market_data:
            token_id = market.get("token_id")
            current_price = market.get("last_price")
            
            if current_price is None or token_id is None:
                continue
                
            self.price_history[token_id].append(current_price)
            history = list(self.price_history[token_id])
            
            if len(history) < 2:
                continue
                
            # 1. Indicator Calculations
            # Velocity: short-term price change
            velocity_current = history[-1] - history[-2]
            
            acceleration = 0.0
            if len(history) >= 3:
                velocity_prev = history[-2] - history[-3]
                acceleration = velocity_current - velocity_prev
                
            # Breakout Detection: price moves beyond the previous window's range
            is_breakout_up = False
            if len(history) >= 4:
                prev_prices = history[:-1]
                window_max = max(prev_prices)
                if current_price > window_max:
                    is_breakout_up = True
                    
            # Trend Continuation: consecutive positive moves
            is_continuation_up = False
            if len(history) >= 4:
                moves = [history[i] - history[i-1] for i in range(1, len(history))]
                # Check if all last 3 moves are strictly positive
                if all(m > 0 for m in moves[-3:]):
                    is_continuation_up = True

            # Overall delta across window
            old_price = history[0]
            delta = current_price - old_price

            # 2. Extreme Price Zone Protection
            if (current_price < GLOBAL_MIN_PRICE or current_price > GLOBAL_MAX_PRICE) and not TEST_MODE:
                continue

            # 3. Liquidity Filtering
            total_liquidity = market.get("bid_depth", 0) + market.get("ask_depth", 0)
            if total_liquidity < self.min_liquidity and not TEST_MODE:
                continue

            # 4. Spread + MIN_EDGE Validation
            best_bid = market.get("best_bid", 0)
            best_ask = market.get("best_ask", 0)
            abs_spread = best_ask - best_bid if best_ask > 0 else 0.05
            
            # Allow validation in test mode with smaller moves
            min_move = self.MIN_EDGE if not TEST_MODE else 0.001
            if abs(delta) <= abs_spread + min_move:
                continue

            # Upward momentum ONLY generates BUY signals
            if delta <= 0:
                continue
                
            # 5. Advanced Signal Scoring (0-100)
            # Base score derived from raw momentum threshold
            score = 40 if delta >= self.momentum_threshold else 30
            
            # A. Breakout Boost (+15 pts)
            if is_breakout_up:
                score += 15
                
            # B. Acceleration Boost (+15 pts)
            if acceleration > 0:
                score += 15
                
            # C. Trend Continuation Boost (+15 pts)
            if is_continuation_up:
                score += 15
                
            # D. Liquidity / Spread Quality (up to 15 pts)
            if abs_spread <= 0.01:
                score += 15
            elif abs_spread <= 0.02:
                score += 8

            score = min(100, score)
            side = "BUY"
            
            logger.info(
                f"MomentumStrategy SIGNAL: {token_id[:20]} | Side: {side} | Delta: {delta:.4f} | "
                f"Accel: {acceleration:.4f} | Breakout: {is_breakout_up} | Cont: {is_continuation_up} | Score: {score}"
            )

            signals.append({
                "token_id": token_id,
                "price": current_price,
                "size": 10,
                "side": side,
                "delta": delta,
                "spread": abs_spread,
                "liquidity": total_liquidity,
                "score": score,
                "strategy": self.name,
                "reason": f"Momentum delta={delta:.4f} score={score} breakout={is_breakout_up}"
            })
                
        return signals
