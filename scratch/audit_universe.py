import asyncio
import json
import os
import sys
import time
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

# Load env variables from .env
from dotenv import load_dotenv
load_dotenv()

CLOB_HOST = "https://clob.polymarket.com"
PK = os.getenv("PK")
CLOB_API_KEY = os.getenv("CLOB_API_KEY")
CLOB_API_SECRET = os.getenv("CLOB_API_SECRET")
CLOB_API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE")
CHAIN_ID = int(os.getenv("CHAIN_ID", 137))

def get_client():
    return ClobClient(
        CLOB_HOST,
        chain_id=CHAIN_ID,
        key=PK,
        creds=ApiCreds(
            api_key=CLOB_API_KEY,
            api_secret=CLOB_API_SECRET,
            api_passphrase=CLOB_API_PASSPHRASE
        )
    )

async def main():
    client = get_client()
    print("Fetching sampling markets...")
    try:
        sampling_markets = client.get_sampling_markets()
        print(f"Sampling markets count: {len(sampling_markets)}")
    except Exception as e:
        print(f"Error fetching sampling markets: {e}")
        sampling_markets = []

    print("Fetching active markets from local sampling JSON database for full universe...")
    # Since live pagination of all 1000+ markets via REST can take too long,
    # let's inspect storage/active_markets_sampling.json which has a local snapshot of 1000 markets.
    try:
        with open("storage/active_markets_sampling.json") as f:
            full_universe_data = json.load(f)
            full_markets = full_universe_data.get("data", [])
        print(f"Loaded {len(full_markets)} markets from active_markets_sampling.json")
    except Exception as e:
        print(f"Error loading local snapshot: {e}")
        full_markets = []

    # Let's perform a live fetch of top order books to enrich spread and depth for comparison
    # We will enrich all markets in the sampling list and the top markets in the full universe.
    # To keep it quick and avoid rate limiting, we select the top 60 markets by opportunity score.
    
    # Let's parse full markets
    active_full_markets = [m for m in full_markets if m.get("active") and not m.get("closed")]
    print(f"Active full universe markets count: {len(active_full_markets)}")

if __name__ == "__main__":
    asyncio.run(main())
