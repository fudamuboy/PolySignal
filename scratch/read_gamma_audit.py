import json
import os

path = "storage/gamma_universe_audit_results.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
    print("Type of data:", type(data))
    if isinstance(data, list):
        print("Total items:", len(data))
        for item in data[:5]:
            print(item)
    elif isinstance(data, dict):
        print("Keys:", list(data.keys()))
        for key in list(data.keys())[:3]:
            print(f"Sample under key {key}: {str(data[key])[:400]}")
else:
    print("gamma_universe_audit_results.json does not exist")
