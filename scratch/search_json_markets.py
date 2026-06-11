import json

json_path = "storage/active_markets_sampling.json"

try:
    with open(json_path, "r") as fh:
        markets = json.load(fh)
    print(f"Successfully loaded {json_path}. It has {len(markets)} markets.")
    
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
    for tok in tokens:
        matched = False
        for m in markets:
            # Gather all clob token IDs in this market
            clob_toks = m.get("clob_token_ids") or []
            tokens_list = m.get("tokens") or []
            m_tokens = []
            for t in tokens_list:
                m_tokens.append(t.get("token_id"))
            m_tokens.extend(clob_toks)
            
            if tok in m_tokens:
                print(f"MATCH: {tok[:15]}... => {m.get('question')} | Slug: {m.get('market_slug')} | Cat: {m.get('category')}")
                matched = True
                found_count += 1
                break
        if not matched:
            print(f"MISS : {tok[:15]}...")
            
    print(f"Total matched: {found_count}/{len(tokens)}")

except Exception as e:
    print(f"Error: {e}")
