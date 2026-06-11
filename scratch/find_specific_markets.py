import json
import os

path = "storage/gamma_universe_audit_data.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
    markets = data.get("markets", [])
    print(f"Loaded {len(markets)} markets from gamma_universe_audit_data.json.")
    
    targets = ["pamela", "bannon", "becerra", "mcconnell", "reya"]
    found = {}
    
    for m in markets:
        if not isinstance(m, dict):
            continue
        title = m.get("question", "").lower() or m.get("title", "").lower()
        slug = m.get("slug", "").lower()
        for t in targets:
            if t in title or t in slug:
                found[t] = m
                break
                
    for t, m in found.items():
        print(f"\n=== Target: {t.upper()} ===")
        print("Question:", m.get("question") or m.get("title"))
        print("Slug:", m.get("slug"))
        print("Spread:", m.get("spread"))
        print("Liquidity:", m.get("liquidity"))
        print("Volume 24h:", m.get("volume_24h"))
else:
    print("gamma_universe_audit_data.json does not exist")
