import urllib.request
import json
import urllib.parse

def main():
    try:
        url = "https://gamma-api.polymarket.com/public-search?q=starship"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
        events = data.get("events", [])
        print(f"Number of events returned: {len(events)}")
        if events:
            print("First event structure:")
            first_event = events[0]
            print(json.dumps(first_event, indent=2)[:1500])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
