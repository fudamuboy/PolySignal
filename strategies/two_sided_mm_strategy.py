from app.signal_engine import BaseStrategy

class TwoSidedMMStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("TwoSidedMMStrategy")

    async def evaluate(self, market_data):
        """
        Generate simultaneous BUY signals for both YES and NO tokens
        across all active enriched markets.
        """
        signals = []
        for market in market_data:
            token_id = market.get("token_id")
            if not token_id:
                continue
                
            spread = market.get("spread", 0.02)
            mid_price = market.get("mid_price") or market.get("last_price") or 0.50
            
            # Delta is modeled as half the spread (the theoretical edge to mid)
            delta = spread * mid_price * 0.5
            
            signals.append({
                "token_id": str(token_id),
                "price": mid_price,
                "size": 10.0,
                "side": "BUY",
                "score": 75.0,  # Maker baseline score (score < 85 triggers passive EV logic)
                "strategy": self.name,
                "delta": delta,
                "spread": spread
            })
        return signals
