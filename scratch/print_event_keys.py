import urllib.request
import json

def main():
    try:
        url = "https://gamma-api.polymarket.com/public-search?q=starship"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
        events = data.get("events", [])
        if events:
            first_event = events[0]
            print(f"Event Keys: {list(first_event.keys())}")
            if "markets" in first_event:
                print(f"Type of markets in event: {type(first_event['markets'])}")
                print(f"Number of markets in event: {len(first_event['markets'])}")
                if first_event['markets']:
                    print("Sample market keys:", list(first_event['markets'][0].keys()))
            else:
                print("No 'markets' key found in event!")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
