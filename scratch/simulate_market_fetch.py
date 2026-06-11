import urllib.request
import json

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
            
        print(f"Fetched {len(markets)} active markets from Gamma API.")
        
        matches = []
        for rule in ACTIVE_RULES:
            for m in markets:
                q = str(m.get("question", "")).upper()
                match_kws = [kw for kw in rule["market_keywords"] if kw in q]
                if match_kws:
                    matches.append((rule["category"], m.get("question"), match_kws))
                    
        print(f"\nFound {len(matches)} market question matches in current top 100 active markets:")
        for cat, q, kws in matches:
            print(f"  - Category: {cat} | Question: '{q}' | Match: {kws}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
