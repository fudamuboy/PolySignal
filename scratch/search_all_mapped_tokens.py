import json

json_path = "storage/active_markets_sampling.json"

try:
    with open(json_path, "r") as fh:
        raw_data = json.load(fh)
        
    # The JSON might be a list directly, or a dictionary with a "data" key
    if isinstance(raw_data, dict):
        markets = raw_data.get("data", [])
    else:
        markets = raw_data
        
    print(f"Loaded {len(markets)} markets from {json_path}.")
    
    tokens = [
        "113585128461808554139088332956774914584404743535283179667078778495379772593123",
        "29308269585917040698756405269369277965835278101044026988987379832371144259087",
        "62419753549060557163164233939381694805760527051999619924042222919057447816",
        "50346565575310273995396997144874891836871065259829083228393044602519086496922",
        "60977129292396881848833910361112107174416489010090570432106122538120602786646",
        "42544248033910169115455586624331983477679665735561815306855702312176773945155",
        "79397895660526576528066965797785113521241099187839967306891454236823723226031",
        "101956855572379773739057381598800735026280011489835740928793113207560616123923",
        "6176387069967725418539368518279260352303182827227966329832868478861860932409"
    ]
    
    found_count = 0
    mapped = {}
    
    for tok in tokens:
        matched = False
        for m in markets:
            if not isinstance(m, dict):
                continue
            # Look inside m["tokens"]
            tokens_list = m.get("tokens", [])
            for t in tokens_list:
                if t.get("token_id") == tok:
                    matched = True
                    found_count += 1
                    mapped[tok] = {
                        "question": m.get("question"),
                        "slug": m.get("market_slug"),
                        "category": m.get("tags")[0] if m.get("tags") else m.get("category"),
                        "outcome": t.get("outcome"),
                        "condition_id": m.get("condition_id"),
                        "description": m.get("description")
                    }
                    print(f"MATCH: {tok} => Question: '{m.get('question')}' | Side: '{t.get('outcome')}' | Category: '{mapped[tok]['category']}'")
                    break
            if matched:
                break
                
        if not matched:
            print(f"MISS : {tok}")
            
    print(f"\nTotal successfully mapped: {found_count}/{len(tokens)}")
    
    with open("storage/paper_validation_real_mapped_tokens.json", "w") as fh:
        json.dump(mapped, fh, indent=2)
        
except Exception as e:
    print(f"Error: {e}")
