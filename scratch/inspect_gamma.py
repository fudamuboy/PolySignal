import urllib.request
import json

url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=2"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=5) as response:
    data = json.loads(response.read().decode())

print("Number of markets:", len(data))
if data:
    m = data[0]
    for key, val in m.items():
        print(f"Key: {key} | Type: {type(val)} | Sample: {str(val)[:200]}")
