import asyncio
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.data_fetcher import DataFetcher
from app.market_filter import MarketFilter
from app.signal_engine import SignalEngine
from strategies.two_sided_mm_strategy import TwoSidedMMStrategy

async def main():
    fetcher = DataFetcher()
    mfilter = MarketFilter()
    
    print("Fetching active markets...")
    markets = await fetcher.fetch_markets()
    
    candidates = mfilter.filter_markets(markets)
    print(f"Candidates: {len(candidates)}")
    
    enriched_tokens = []
    for m in candidates[:3]:
        tokens = m.get("tokens", [])
        for tok in tokens:
            t_id = tok.get("token_id")
            if not t_id: continue
            
            orderbook = await fetcher.get_orderbook(t_id)
            if not orderbook: continue
            
            raw_bids = orderbook.bids if hasattr(orderbook, "bids") else orderbook.get("bids", [])
            raw_asks = orderbook.asks if hasattr(orderbook, "asks") else orderbook.get("asks", [])
            if not raw_bids or not raw_asks: continue
            
            def get_price(level):
                if hasattr(level, "price"): return float(level.price)
                if isinstance(level, dict): return float(level.get("price") or level.get("p") or 0)
                return 0.0
                
            bids = sorted(raw_bids, key=get_price, reverse=True)
            asks = sorted(raw_asks, key=get_price)
            
            best_bid = get_price(bids[0])
            best_ask = get_price(asks[0])
            mid_price = (best_bid + best_ask) / 2
            spread = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0
            
            token_market = m.copy()
            token_market.update({
                "token_id": t_id,
                "orderbook": orderbook,
                "last_price": mid_price,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid_price": mid_price,
                "spread": spread
            })
            enriched_tokens.append(token_market)
            
    strategies = [TwoSidedMMStrategy()]
    engine = SignalEngine(strategies, confidence_threshold=70)
    signals, stats = await engine.generate_signals(enriched_tokens)
    
    print("\n--- GENERATED SIGNALS ---")
    for s in signals:
        print(f"Token: {s['token_id'][:15]}... | Side: {s['side']} | Price: {s['price']} | Delta: {s['delta']} | Strategy: {s['strategy']} | Score: {s['score']}")

if __name__ == "__main__":
    asyncio.run(main())
