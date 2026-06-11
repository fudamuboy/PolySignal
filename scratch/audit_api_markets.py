import os
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from dotenv import load_dotenv

load_dotenv()

async def main():
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
    
    # 1. Fetch get_sampling_markets
    print("Fetching from get_sampling_markets()...")
    try:
        sampling_res = client.get_sampling_markets()
        # Handle dict or list format
        if isinstance(sampling_res, dict):
            sampling_markets = sampling_res.get("data", list(sampling_res.values()))
        else:
            sampling_markets = list(sampling_res)
        print(f"Success: get_sampling_markets() returned {len(sampling_markets)} markets.")
    except Exception as e:
        print(f"Error fetching sampling markets: {e}")
        sampling_markets = []

    # 2. Fetch get_markets (first page)
    print("\nFetching from get_markets() (first page)...")
    try:
        markets_res = client.get_markets()
        if isinstance(markets_res, dict):
            markets_keys = list(markets_res.keys())
            print(f"get_markets() response keys: {markets_keys}")
            if "data" in markets_res:
                print(f"get_markets() first page has {len(markets_res['data'])} markets.")
                if "next_cursor" in markets_res:
                    print(f"next_cursor = {markets_res['next_cursor']}")
        else:
            print(f"get_markets() returned type: {type(markets_res)}")
    except Exception as e:
        print(f"Error fetching markets: {e}")

if __name__ == "__main__":
    asyncio.run(main())
