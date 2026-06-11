import urllib.request
import json
import urllib.parse

def test_query(query):
    try:
        url = f"https://gamma-api.polymarket.com/public-search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
        print(f"\nQuery '{query}' returned data type: {type(data)}")
        if isinstance(data, dict):
            print(f"Keys: {list(data.keys())}")
            for key in data.keys():
                val = data[key]
                print(f"  Key '{key}' value type: {type(val)} | size: {len(val) if isinstance(val, (list, dict)) else 'N/A'}")
                if key == "markets" and isinstance(val, list) and len(val) > 0:
                    print("  Sample market:")
                    print(json.dumps(val[0], indent=2)[:500])
    except Exception as e:
        print(f"Error for query '{query}': {e}")

def main():
    test_query("FED")
    test_query("STARSHIP")
    test_query("BIDEN")

if __name__ == "__main__":
    main()
