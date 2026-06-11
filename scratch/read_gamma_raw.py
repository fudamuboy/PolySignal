import json
import os

path = "storage/gamma_universe_audit_data.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
    print("Type of data:", type(data))
    if isinstance(data, list):
        print("Length:", len(data))
        print("First 3 elements types:", [type(x) for x in data[:3]])
        for idx, x in enumerate(data[:3]):
            print(f"Index {idx}: {str(x)[:200]}")
    elif isinstance(data, dict):
        print("Keys:", list(data.keys()))
else:
    print("File not found")
