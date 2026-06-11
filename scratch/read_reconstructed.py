import json
import os

path = "storage/paper_validation_reconstructed_trades.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
    print("Keys in reconstructed trades:", list(data.keys()) if isinstance(data, dict) else "Not a dict")
    if isinstance(data, list):
        print("Total items:", len(data))
        print("First item sample:", data[0])
    elif isinstance(data, dict):
        print("Sample data:", str(data)[:500])
else:
    print("reconstructed_trades.json does not exist")
