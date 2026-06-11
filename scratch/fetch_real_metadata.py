import urllib.request
import json
import ssl
import time

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

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

print("=== FETCHING REAL METADATA FROM POLYMARKET CLOB REST API ===")

mapped_results = {}

for idx, tok in enumerate(tokens):
    url = f"https://clob.polymarket.com/markets?token_id={tok}"
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    try:
        time.sleep(0.2)  # Avoid rate limits
        with urllib.request.urlopen(req, context=ctx) as response:
            res_dict = json.loads(response.read().decode())
            data_list = res_dict.get("data", [])
            if data_list:
                market = data_list[0]
                mapped_results[tok] = {
                    "question": market.get("question"),
                    "slug": market.get("market_slug") or market.get("slug"),
                    "category": market.get("category"),
                    "condition_id": market.get("condition_id"),
                    "market_id": market.get("clob_token_ids") or market.get("market_id"),
                    "description": market.get("description"),
                    "tokens": market.get("tokens")
                }
                print(f"Token {idx} ({tok[:10]}...) => Question: {market.get('question')} | Slug: {market.get('market_slug')} | Category: {market.get('category')}")
            else:
                print(f"Token {idx} ({tok[:10]}...) => No market data returned in list.")
    except Exception as e:
        print(f"Token {idx} ({tok[:10]}...) => Error fetching: {e}")

# Save results
with open("storage/paper_validation_real_mapped_tokens.json", "w") as fh:
    json.dump(mapped_results, fh, indent=2)

print("\nSuccessfully mapped all tokens and saved to storage/paper_validation_real_mapped_tokens.json!")
