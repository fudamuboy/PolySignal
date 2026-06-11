from app.config import TEST_MODE
from app.signal_engine import BaseStrategy
from app.logger import logger

class SpreadStrategy(BaseStrategy):
    def __init__(self, spread_threshold=0.01, min_depth_usd=500):
        super().__init__("SpreadStrategy")
        self.spread_threshold = spread_threshold
        self.min_depth_usd = min_depth_usd

    async def evaluate(self, market_data):
        """
        Detect attractive spreads using orderbook depth.
        """
        signals = []
        for market in market_data:
            orderbook = market.get("orderbook")
            if not orderbook:
                continue
            
            bids = getattr(orderbook, "bids", [])
            asks = getattr(orderbook, "asks", [])
            
            if not bids or not asks:
                # Try dictionary access if it's not an object
                if isinstance(orderbook, dict):
                    bids = orderbook.get("bids", [])
                    asks = orderbook.get("asks", [])
                
            if not bids or not asks:
                continue

            def get_price(level):
                if hasattr(level, "price"): return float(level.price)
                if isinstance(level, dict): return float(level.get("price") or level.get("p") or 0)
                if isinstance(level, (list, tuple)): return float(level[0])
                return 0.0

            best_bid = get_price(bids[0])
            best_ask = get_price(asks[0])
            mid_price = (best_bid + best_ask) / 2
            spread = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0

            logger.info(f"SpreadStrategy Detail [{market.get('token_id')}]: Bid={best_bid:.4f}, Ask={best_ask:.4f}, Mid={mid_price:.4f}, Spread={spread:.4f}")

            # 1. Check spread threshold
            eff_spread_thresh = self.spread_threshold if not TEST_MODE else 0.5
            if spread > eff_spread_thresh:
                continue

            # 2. Detect strong liquidity zones (depth)
            # Calculate depth within 1% of best price
            bid_depth = sum(
                float(b.size if hasattr(b, "size") else b.get("size")) * 
                float(b.price if hasattr(b, "price") else b.get("price")) 
                for b in bids if float(b.price if hasattr(b, "price") else b.get("price")) >= best_bid * 0.99
            )
            ask_depth = sum(
                float(a.size if hasattr(a, "size") else a.get("size")) * 
                float(a.price if hasattr(a, "price") else a.get("price")) 
                for a in asks if float(a.price if hasattr(a, "price") else a.get("price")) <= best_ask * 1.01
            )

            # Avoid thin markets
            eff_min_depth = self.min_depth_usd if not TEST_MODE else 0
            if bid_depth < eff_min_depth or ask_depth < eff_min_depth:
                logger.debug(f"Skipping market {market.get('condition_id')} due to thin liquidity: {bid_depth} / {ask_depth}")
                continue

            # 3. Calculate score based on spread tightness and depth ratio
            # Score favors tight spreads and balanced liquidity
            spread_score = 1.0 - (spread / self.spread_threshold)
            depth_balance = 1.0 - abs(bid_depth - ask_depth) / (bid_depth + ask_depth)
            final_score = (spread_score * 0.6) + (depth_balance * 0.4)

            # Detect "fake" or thin spread by checking if best bid/ask size is tiny
            first_bid_size = float(bids[0].size if hasattr(bids[0], "size") else bids[0].get("size"))
            first_ask_size = float(asks[0].size if hasattr(asks[0], "size") else asks[0].get("size"))
            
            if first_bid_size * best_bid < 10 or first_ask_size * best_ask < 10:
                final_score *= 0.5 # Penalize small top-of-book size
            
            if final_score > 0.6:
                signals.append({
                    "token_id": market.get("token_id"),
                    "price": best_ask if final_score > 0.8 else mid_price, 
                    "size": 10, 
                    "side": "BUY", 
                    "score": final_score,
                    "strategy": self.name
                })
        
        return signals
