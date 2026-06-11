import json
import os

path = "storage/gamma_universe_audit_results.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
    print("BEST STATS:")
    print(json.dumps(data["best_stats"], indent=2))
    print("\nSAMPLED STATS:")
    print(json.dumps(data["sampled_stats"], indent=2))
