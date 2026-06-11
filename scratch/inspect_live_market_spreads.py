import asyncio
import sys
import os
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.data_fetcher import DataFetcher
from app.market_filter import MarketFilter

async def main():
    fetcher = DataFetcher()
    mfilter = MarketFilter()
    
    print("Fetching active markets...")
    markets = await fetcher.fetch_markets()
    
    if isinstance(markets, dict):
        m_list = markets.get("data", list(markets.values()))
    else:
        m_list = list(markets)
        
    print(f"Total active markets: {len(m_list)}")
    
    candidates = mfilter.filter_markets(markets)
    print(f"Total candidates after basic filtering: {len(candidates)}")
    
    tradeable_count = 0
    rejections = {}
    
    for m in candidates[:30]:
        tokens = m.get("tokens", [])
        if not tokens:
            continue
            
        print(f"\nMarket: {m.get('question')} | ID: {m.get('id')}")
        for tok in tokens:
            t_id = tok.get("token_id")
            price = float(tok.get("price") or 0.5)
            
            # Fetch orderbook to get real spread
            ob = await fetcher.get_orderbook(t_id)
            if not ob:
                print(f"  Token {t_id[:10]}...: No orderbook")
                continue
                
            raw_bids = ob.get("bids", []) if isinstance(ob, dict) else getattr(ob, "bids", [])
            raw_asks = ob.get("asks", []) if isinstance(ob, dict) else getattr(ob, "asks", [])
            if not raw_bids or not raw_asks:
                print(f"  Token {t_id[:10]}...: Empty orderbook bids/asks")
                continue
                
            def get_price(level):
                if hasattr(level, "price"): return float(level.price)
                if isinstance(level, dict): return float(level.get("price") or level.get("p") or 0)
                return 0.0

            bids = sorted(raw_bids, key=lambda x: get_price(x), reverse=True)
            asks = sorted(raw_asks, key=lambda x: get_price(x))
            
            best_bid = get_price(bids[0])
            best_ask = get_price(asks[0])
            mid = (best_bid + best_ask) / 2
            spread = (best_ask - best_bid) / mid if mid > 0 else 0
            
            print(f"  Token {t_id[:10]}... | Price: {price:.4f} | Bid: {best_bid:.4f} | Ask: {best_ask:.4f} | Mid: {mid:.4f} | Spread: {spread*100:.3f}%")
            
            # Check rejections
            reasons = []
            if price > 0.80:
                reasons.append("ZOMBIE_PRICE_CAP")
            if best_bid < 0.05:
                reasons.append("ZOMBIE_BID_FLOOR")
            if spread > 0.20:
                reasons.append("ZOMBIE_MAX_SPREAD")
            
            # EV Check
            delta = spread * mid * 0.5
            delta_pct = delta / mid
            required_edge = 0.001 # EV_SAFETY_MARGIN
            if delta_pct < required_edge:
                reasons.append(f"NEGATIVE_EV (DeltaPct={delta_pct*100:.3f}% < Req={required_edge*100:.3f}%)")
                
            if reasons:
                print(f"    -> REJECTED: {', '.join(reasons)}")
            else:
                print(f"    -> TRADEABLE!")
                tradeable_count += 1
                
    print(f"\nSummary: {tradeable_count} tradeable tokens found in top 30 candidate markets.")

if __name__ == "__main__":
    asyncio.run(main())
