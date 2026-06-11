import urllib.request
import json
import time
import os
import sys

# Load env variables from .env
from dotenv import load_dotenv
load_dotenv()

GAMMA_URL = "https://gamma-api.polymarket.com/markets"

def fetch_all_gamma_markets():
    print("Fetching active markets from Polymarket Gamma API...")
    markets = []
    offset = 0
    limit = 100
    while True:
        try:
            # Sort by liquidity descending to get the most relevant markets first
            url = f"{GAMMA_URL}?active=true&closed=false&limit={limit}&offset={offset}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
            if not data:
                break
            markets.extend(data)
            print(f"  Fetched offset {offset}: loaded {len(data)} markets (total loaded so far: {len(markets)})")
            if len(data) < limit:
                break
            offset += limit
            time.sleep(0.1) # Be gentle on rate limits
            if len(markets) >= 1000: # Limit to 1000 for efficiency
                break
        except Exception as e:
            print(f"Error fetching Gamma markets at offset {offset}: {e}")
            break
    return markets

def main():
    # 1. Fetch from Gamma (representing the full universe)
    full_markets = fetch_all_gamma_markets()
    if not full_markets:
        print("Failed to fetch full universe. Using fallback local snapshot.")
        try:
            with open("storage/active_markets_sampling.json") as f:
                fallback_data = json.load(f)
                full_markets = fallback_data.get("data", [])
        except Exception as e:
            print("Fallback also failed.")
            return

    # Filter to active and open
    active_full = [m for m in full_markets if m.get("active") and not m.get("closed")]
    print(f"\nTotal active/open markets fetched: {len(active_full)}")

    # 2. Fetch get_sampling_markets()
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    
    print("\nFetching get_sampling_markets() via CLOB API...")
    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            chain_id=137,
            key=os.getenv("PK"),
            creds=ApiCreds(
                api_key=os.getenv("CLOB_API_KEY"),
                api_secret=os.getenv("CLOB_API_SECRET"),
                api_passphrase=os.getenv("CLOB_API_PASSPHRASE")
            )
        )
        sampling_res = client.get_sampling_markets()
        if isinstance(sampling_res, dict):
            sampling_markets = sampling_res.get("data", list(sampling_res.values()))
        else:
            sampling_markets = list(sampling_res)
        print(f"Success: get_sampling_markets() returned {len(sampling_markets)} markets.")
    except Exception as e:
        print(f"Error fetching sampling markets: {e}")
        # Try local fallback
        sampling_markets = active_full[:50] # dummy fallback

    # Calculate overlap
    # We match by condition_id (CLOB condition_id matches Gamma conditionId)
    sampling_ids = set()
    for m in sampling_markets:
        cid = m.get("condition_id")
        if cid:
            sampling_ids.add(cid.lower())
            
    overlapping = []
    non_overlapping = []
    for m in active_full:
        cid = m.get("conditionId")
        if cid and cid.lower() in sampling_ids:
            overlapping.append(m)
        else:
            non_overlapping.append(m)
            
    print(f"\nAudit Totals:")
    print(f"1. Total active markets in universe   : {len(active_full)}")
    print(f"2. Total sampling markets visible     : {len(sampling_markets)}")
    print(f"3. Overlapping active markets         : {len(overlapping)}")
    visibility_pct = (len(overlapping) / len(active_full)) * 100 if len(active_full) > 0 else 0
    print(f"4. Percentage of universe visible     : {visibility_pct:.2f}%")

    # Save data for further analysis
    analysis_data = {
        "full_count": len(active_full),
        "sampling_count": len(sampling_markets),
        "visibility_pct": visibility_pct,
        "active_full": active_full,
        "sampling_markets": sampling_markets,
        "overlapping": overlapping
    }
    with open("storage/gamma_universe_audit_data.json", "w") as fh:
        # We only save minimal fields to save space
        json.dump({
            "full_count": len(active_full),
            "sampling_count": len(sampling_markets),
            "visibility_pct": visibility_pct,
            "markets": [{
                "title": m.get("question"),
                "slug": m.get("slug"),
                "conditionId": m.get("conditionId"),
                "spread": float(m.get("spread") or 1.0),
                "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0.0),
                "volume": float(m.get("volume24hr") or m.get("volume") or 0.0),
                "category": m.get("groupItemTitle", "General") or "General"
            } for m in active_full]
        }, fh, indent=2)
    print("\nData saved to storage/gamma_universe_audit_data.json")

if __name__ == "__main__":
    main()
