import json

ACTIVE_KEYWORDS = ["BITCOIN", "1M", "1 MILLION", "GTA VI", "TAIWAN", "CHINA", "MEGAETH", "AIRDROP", "WEINSTEIN", "PRISON", "SPAIN", "WORLD CUP", "KNICKS", "NBA", "HURRICANES", "NHL", "STANLEY CUP"]

def main():
    try:
        with open("storage/active_markets_sampling.json", "r") as f:
            data = json.load(f)
            
        markets = data.get("data", [])
        print(f"Loaded {len(markets)} active markets from active_markets_sampling.json")
        
        matches = []
        for m in markets:
            q = m.get("question", "").upper()
            matched_kws = [kw for kw in ACTIVE_KEYWORDS if kw in q]
            if matched_kws:
                matches.append((m.get("question"), matched_kws))
                
        print(f"\nFound {len(matches)} markets containing rule keywords in their questions:")
        for q, kws in matches:
            print(f"  - '{q}' matches: {kws}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
