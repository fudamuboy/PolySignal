import asyncio
from app.data_fetcher import DataFetcher

async def main():
    df = DataFetcher()
    markets = await df.fetch_markets()
    if isinstance(markets, dict):
        mlist = markets.get("data", [])
    else:
        mlist = list(markets)
    print(f"Found {len(mlist)} active markets.")
    for m in mlist[:100]:
        print(f"- Question: {m.get('question')} | Tokens: {[t.get('symbol') for t in m.get('tokens', [])]}")

if __name__ == "__main__":
    asyncio.run(main())
