import urllib.request
import json
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.market_filter import MarketFilter

def main():
    try:
        url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            markets = json.loads(response.read().decode())
            
        normalized_markets = []
        for m in markets:
            if not isinstance(m, dict): continue
            nm = m.copy()
            nm["volume_24h"] = float(nm.get("volume24hr") or nm.get("volumeNum") or nm.get("volume_24h") or 0)
            nm["liquidity"] = float(nm.get("liquidityNum") or nm.get("liquidity") or nm.get("liquidityClob") or 0)
            nm["spread"] = float(nm.get("spread") or 1.0)
            nm["end_date_iso"] = nm.get("endDateIso") or nm.get("endDate")
            nm["active"] = nm.get("active", True)
            nm["closed"] = nm.get("closed", False)
            normalized_markets.append(nm)
            
        filterer = MarketFilter()
        
        # Log all markets with their scores
        scored_markets = []
        for m in normalized_markets:
            if filterer.is_tradeable(m):
                m['opportunity_score'] = filterer.calculate_opportunity_score(m)
                scored_markets.append(m)
                
        scored_markets.sort(key=lambda x: x.get('opportunity_score', 0), reverse=True)
        
        print("Top 50 scored markets:")
        for i, m in enumerate(scored_markets[:50]):
            q = m.get("question")
            score = m.get("opportunity_score")
            vol = m.get("volume_24h")
            liq = m.get("liquidity")
            print(f"  {i+1:2d}. Score: {score:5.2f} | Vol: {vol:10.2f} | Liq: {liq:10.2f} | '{q}'")
            
        # Search for bitcoin or gta vi in all scored markets
        print("\nBitcoin/GTA VI/Taiwan markets in the sorted list:")
        found = False
        for i, m in enumerate(scored_markets):
            q = m.get("question", "").upper()
            if "BITCOIN" in q or "GTA VI" in q or "TAIWAN" in q:
                found = True
                score = m.get("opportunity_score")
                vol = m.get("volume_24h")
                liq = m.get("liquidity")
                print(f"  Rank: {i+1} | Score: {score:5.2f} | Vol: {vol:10.2f} | Liq: {liq:10.2f} | '{m.get('question')}'")
        if not found:
            print("  None found in scored markets!")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
