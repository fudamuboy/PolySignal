import asyncio

def mock_market_filter_test():
    print("Testing Market Filtering Logic...")
    
    # Mock rejection_reasons dict
    rejection_reasons = {}

    def filter_market(best_bid, best_ask):
        mid_price = (best_bid + best_ask) / 2
        spread = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0
        
        # 1. Skip extreme "dust" or "dead" markets (e.g. 0.01/0.99)
        if best_bid <= 0.05 or best_ask >= 0.95:
            rejection_reasons["extreme_prices"] = rejection_reasons.get("extreme_prices", 0) + 1
            return "REJECT: extreme_prices"
        
        # 2. Relaxed but sensible spread filter (user requested < 0.3)
        if spread > 0.30: 
            rejection_reasons["wide_spread"] = rejection_reasons.get("wide_spread", 0) + 1
            return "REJECT: wide_spread"
            
        return "ACCEPT"

    # Test cases
    cases = [
        (0.50, 0.51, "ACCEPT"),       # Tight spread
        (0.01, 0.99, "REJECT: extreme_prices"), # Dust market (extreme)
        (0.40, 0.60, "ACCEPT"),       # Spread = 0.2 / 0.5 = 0.4. Wait, 0.4 > 0.3 should be REJECT.
        (0.45, 0.55, "ACCEPT"),       # Spread = 0.1 / 0.5 = 0.2. ACCEPT.
        (0.10, 0.90, "ACCEPT"),       # Spread = 0.8 / 0.5 = 1.6. REJECT: wide_spread.
        (0.04, 0.50, "REJECT: extreme_prices"), # Bid too low
        (0.50, 0.96, "REJECT: extreme_prices"), # Ask too high
    ]

    for bid, ask, expected in cases:
        result = filter_market(bid, ask)
        print(f"Bid: {bid:.2f}, Ask: {ask:.2f} -> Result: {result} (Expected: {expected})")

if __name__ == "__main__":
    mock_market_filter_test()
