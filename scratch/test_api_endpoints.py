import urllib.request
import json
import ssl

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

for tok in tokens[:2]:
    print(f"\n--- TESTING TOKEN: {tok} ---")
    urls = [
        f"https://clob.polymarket.com/markets/{tok}",
        f"https://clob.polymarket.com/markets?condition_id={tok}",
        f"https://clob.polymarket.com/markets?token_id={tok}",
        f"https://clob.polymarket.com/prices?token_id={tok}",
        f"https://clob.polymarket.com/book?token_id={tok}"
    ]
    for url in urls:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        try:
            with urllib.request.urlopen(req, context=ctx) as response:
                data = json.loads(response.read().decode())
                print(f"SUCCESS on {url}: {str(data)[:200]}")
        except Exception as e:
            print(f"FAILED on {url}: {e}")
