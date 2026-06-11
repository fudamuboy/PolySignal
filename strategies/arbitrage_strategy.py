from app.config import TEST_MODE
from app.signal_engine import BaseStrategy
from app.logger import logger

class ArbitrageStrategy(BaseStrategy):
    def __init__(self, target_profit_margin=0.001, est_fees=0.001):
        super().__init__("ArbitrageStrategy")
        self.target_profit_margin = target_profit_margin if not TEST_MODE else 0.0001
        self.est_fees = est_fees if not TEST_MODE else 0

    async def evaluate(self, market_data):
        """
        Detect when YES + NO != 1 (Pricing discrepancies).
        We expect market_data to be grouped by event or have pair info.
        For simplicity, we assume 'market' objects have 'group_id' and 'side' (YES/NO).
        """
        signals = []
        event_pairs = {}
        
        # Group tokens by event
        for market in market_data:
            event_id = market.get("question") # Example event identifier
            if not event_id:
                continue
            
            if event_id not in event_pairs:
                event_pairs[event_id] = {"YES": None, "NO": None}
            
            # Polymarket tokens usually have different condition_ids for YES/NO
            # This logic needs refinement based on real API market structure
            outcome = market.get("outcome") # 'Yes' or 'No' (case sensitive check)
            if outcome in ["Yes", "No"]:
                event_pairs[event_id][outcome.upper()] = market

        for event_id, pair in event_pairs.items():
            yes_market = pair["YES"]
            no_market = pair["NO"]
            
            if not yes_market or not no_market:
                continue
            
            yes_price = yes_market.get("last_price")
            no_price = no_market.get("last_price")
            
            if yes_price is None or no_price is None:
                continue

            total_cost = yes_price + no_price
            potential_arbing = 1.0 - total_cost
            
            logger.info(f"ArbitrageStrategy Detail [{event_id}]: YES={yes_price:.4f}, NO={no_price:.4f}, Sum={total_cost:.4f}, Potential={potential_arbing:.4f}")
            
            # account for fees and slippage
            net_profit = potential_arbing - self.est_fees
            
            if net_profit >= self.target_profit_margin:
                logger.info(f"Arbitrage opportunity detected for {event_id}: YES={yes_price}, NO={no_price}, Profit={net_profit:.4f}")
                
                # Create signals for both sides
                signals.append({
                    "token_id": yes_market["token_id"],
                    "price": yes_price,
                    "size": 10,
                    "side": "BUY",
                    "score": min(0.95, 0.7 + net_profit * 5),
                    "strategy": self.name
                })
                signals.append({
                    "token_id": no_market["token_id"],
                    "price": no_price,
                    "size": 10,
                    "side": "BUY",
                    "score": min(0.95, 0.7 + net_profit * 5),
                    "strategy": self.name
                })
                
        return signals
