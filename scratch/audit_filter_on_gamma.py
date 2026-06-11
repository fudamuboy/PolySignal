import urllib.request
import json
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.market_filter import MarketFilter

ACTIVE_RULES = [
    {
        "category": "BITCOIN_1M",
        "news_keywords": ["BITCOIN", "TOUCHES 1 MILLION", "REACHES 1 MILLION", "HIT 1 MILLION", "BTC TO 1M", "BTC HITS 1 MILLION"],
        "market_keywords": ["BITCOIN", "1M", "1 MILLION", "GTA VI"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "CHINA_TAIWAN",
        "news_keywords": ["CHINA INVADES TAIWAN", "CHINESE FORCES ATTACK TAIWAN", "TAIWAN INVASION BEGUN"],
        "market_keywords": ["CHINA", "TAIWAN", "GTA VI"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "MEGAETH_AIRDROP",
        "news_keywords": ["MEGAETH AIRDROP ANNOUNCED", "MEGAETH AIRDROP CONFIRMED", "MEGAETH LAUNCHES AIRDROP"],
        "market_keywords": ["MEGAETH", "AIRDROP"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "WEINSTEIN_NO_PRISON",
        "news_keywords": ["HARVEY WEINSTEIN SENTENCED TO NO PRISON", "WEINSTEIN AVOIDS PRISON TIME", "WEINSTEIN SENTENCED TO ZERO YEARS"],
        "market_keywords": ["HARVEY WEINSTEIN", "NO PRISON"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "SPAIN_WORLD_CUP",
        "news_keywords": ["SPAIN WINS THE WORLD CUP", "SPAIN CROWNED WORLD CUP CHAMPIONS"],
        "market_keywords": ["SPAIN", "WORLD CUP", "FIFA"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "KNICKS_NBA",
        "news_keywords": ["NEW YORK KNICKS WIN NBA FINALS", "KNICKS CROWNED NBA CHAMPIONS"],
        "market_keywords": ["KNICKS", "NBA", "FINALS"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "HURRICANES_NHL",
        "news_keywords": ["CAROLINA HURRICANES WIN STANLEY CUP", "HURRICANES CROWNED NHL CHAMPIONS"],
        "market_keywords": ["HURRICANES", "NHL", "STANLEY CUP"],
        "side": "BUY",
        "target_outcome": "Yes"
    }
]

def main():
    try:
        url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            markets = json.loads(response.read().decode())
            
        # Standard clob mapping
        normalized_markets = []
        for m in markets:
            if not isinstance(m, dict): continue
            nm = m.copy()
            if "tokens" not in nm and "clobTokenIds" in nm:
                clob_ids = nm.get("clobTokenIds") or []
                if isinstance(clob_ids, str): clob_ids = json.loads(clob_ids)
                outcomes = nm.get("outcomes") or []
                if isinstance(outcomes, str): outcomes = json.loads(outcomes)
                prices = nm.get("outcomePrices") or []
                if isinstance(prices, str): prices = json.loads(prices)
                tokens = []
                for i, tid in enumerate(clob_ids):
                    outcome = outcomes[i] if i < len(outcomes) else ("Yes" if i == 0 else "No")
                    price = prices[i] if i < len(prices) else "0.5"
                    tokens.append({"token_id": str(tid), "outcome": outcome, "price": price})
                nm["tokens"] = tokens
            nm["volume_24h"] = float(nm.get("volume24hr") or nm.get("volumeNum") or nm.get("volume_24h") or 0)
            nm["liquidity"] = float(nm.get("liquidityNum") or nm.get("liquidity") or nm.get("liquidityClob") or 0)
            nm["spread"] = float(nm.get("spread") or 1.0)
            nm["end_date_iso"] = nm.get("endDateIso") or nm.get("endDate")
            nm["active"] = nm.get("active", True)
            nm["closed"] = nm.get("closed", False)
            normalized_markets.append(nm)
            
        filterer = MarketFilter()
        # Ensure we run in the environment's mode (TEST_MODE=True)
        print(f"Filterer running. TEST_MODE value: {os.getenv('TEST_MODE')}")
        
        filtered = filterer.filter_markets(normalized_markets)
        print(f"Filtered to {len(filtered)} markets.")
        
        # Check how many of the matched ones survived
        matches = []
        for m in filtered:
            q = str(m.get("question", "")).upper()
            for rule in ACTIVE_RULES:
                match_kws = [kw for kw in rule["market_keywords"] if kw in q]
                if match_kws:
                    matches.append((rule["category"], m.get("question"), match_kws))
                    
        print(f"\n{len(matches)} matched markets survived filter_markets:")
        for cat, q, kws in matches:
            print(f"  - Category: {cat} | Question: '{q}' | Match: {kws}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
