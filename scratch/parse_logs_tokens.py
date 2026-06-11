import os
import re

log_dir = "/Users/slim/Desktop/PolyMarket/storage/logs"
log_files = sorted([os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.startswith("bot.log")])

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

print("Scanning log files...")
for token in tokens:
    print(f"\n==================== TOKEN: {token} ====================")
    matches = 0
    for lf in log_files:
        try:
            with open(lf, "r", errors="ignore") as fh:
                for line in fh:
                    if token in line:
                        # Print if it contains interesting keywords or if we have printed fewer than 5 matches
                        if matches < 3 or any(kw in line.lower() for kw in ["market", "question", "slug", "clob", "active", "filter", "score", "strat"]):
                            print(f"{os.path.basename(lf)}: {line.strip()[:200]}")
                            matches += 1
                        if matches >= 15:
                            break
        except Exception as e:
            print(f"Error reading {lf}: {e}")
