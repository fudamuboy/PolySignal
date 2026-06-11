import json

def main():
    try:
        with open("storage/active_markets_sampling.json", "r") as f:
            data = json.load(f)
        print(f"Type of active_markets_sampling: {type(data)}")
        if isinstance(data, list):
            print(f"Number of items: {len(data)}")
            if len(data) > 0:
                print("First item sample:")
                print(json.dumps(data[0], indent=2)[:500])
        elif isinstance(data, dict):
            print(f"Keys: {list(data.keys())}")
            # print sample
            first_key = list(data.keys())[0]
            print(f"Sample for key {first_key}:")
            print(json.dumps(data[first_key], indent=2)[:500])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
