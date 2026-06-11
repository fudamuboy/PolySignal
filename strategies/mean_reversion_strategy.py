import collections
from app.signal_engine import BaseStrategy
from app.logger import logger
from app.config import (
    TEST_MODE, GLOBAL_MIN_PRICE, GLOBAL_MAX_PRICE,
    GLOBAL_MIN_LIQUIDITY, REVERSION_DELTA_THRESHOLD, REVERSION_MIN_EDGE,
    MIN_REVERSION_FLOOR,
)

class MeanReversionStrategy(BaseStrategy):
    def __init__(self, window_size=5):
        super().__init__("MeanReversionStrategy")
        self.window_size = window_size
        self.price_history = collections.defaultdict(lambda: collections.deque(maxlen=window_size))

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

            # Delta Calculation
            old_price = history[0]
            delta = current_price - old_price

            # 1. Threshold Check
            # Hard floor: MIN_REVERSION_FLOOR (0.020) applies in ALL modes — no TEST_MODE bypass.
            # This prevents flat-market noise from flooding the pipeline.
            # Production additionally requires REVERSION_DELTA_THRESHOLD (0.06).
            if abs(delta) < MIN_REVERSION_FLOOR:
                continue

            if abs(delta) < REVERSION_DELTA_THRESHOLD and not TEST_MODE:
                continue

            # 2. Extreme Price Zone Protection
            if (current_price < GLOBAL_MIN_PRICE or current_price > GLOBAL_MAX_PRICE) and not TEST_MODE:
                continue

            # 3. Liquidity & Spread Check
            best_bid = market.get("best_bid", 0)
            best_ask = market.get("best_ask", 0)
            abs_spread = best_ask - best_bid if best_ask > 0 else 0.05
            
            total_liquidity = market.get("bid_depth", 0) + market.get("ask_depth", 0)
            
            # Mean reversion works best in tight, liquid markets
            if abs_spread > 0.015 and not TEST_MODE:
                continue
            if total_liquidity < GLOBAL_MIN_LIQUIDITY and not TEST_MODE:
                continue

            # 4. Scoring (0-100)
            score = 0
            
            # A. Reversion Strength (up to 40 pts)
            over_extension = abs(delta)
            if over_extension >= 0.10: score += 40
            elif over_extension >= 0.08: score += 30
            elif over_extension >= 0.06: score += 20
            else: score += 10

            # B. Spread Quality (up to 30 pts)
            if abs_spread <= 0.003: score += 30
            elif abs_spread <= 0.007: score += 20
            elif abs_spread <= 0.012: score += 10

            # C. Volume/Liquidity (up to 30 pts)
            if total_liquidity >= 30000: score += 30
            elif total_liquidity >= 15000: score += 20
            elif total_liquidity >= 5000: score += 10

            # Side: MeanReversion ONLY generates BUY signals
            # (price dropped → mean-revert up → BUY opportunity)
            # SELL is exclusively handled by position exit logic, never here.
            if delta >= 0:
                # Price went up — mean-reversion would suggest SELL, but we skip it
                # (no short selling; SELL only allowed on existing positions)
                continue
            side = "BUY"
            
            logger.info(f"MeanReversionStrategy SIGNAL candidate: {token_id} | Side: {side} | Delta: {delta:.4f} | Score: {score}")

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
                "spread": abs_spread,
                "reason": f"MeanReversion delta={delta:.4f} score={score}"
            })
                
        return signals
