import json
import os

path = "storage/gamma_universe_audit_data.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
    markets = data.get("markets", [])
    
    # Search evette or reya
    for m in markets:
        title = (m.get("question", "") or m.get("title", "")).lower()
        if "evette" in title or "reya" in title or "pamela" in title:
            print(f"Match: {m.get('question')} | Spread: {m.get('spread')} | Liquidity: {m.get('liquidity')} | Volume: {m.get('volume_24h')}")
else:
    print("File not found")
