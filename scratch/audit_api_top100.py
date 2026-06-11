import urllib.request
import json

def main():
    try:
        url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            markets = json.loads(response.read().decode())
            
        print(f"Fetched {len(markets)} active markets from Gamma API.")
        for i, m in enumerate(markets):
            print(f"  {i+1:2d}. '{m.get('question')}' (slug: {m.get('slug')})")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
