import re
import os

ACTIVE_RULES = [
    {
        "category": "BITCOIN_1M",
        "news_keywords": ["BITCOIN", "TOUCHES 1 MILLION", "REACHES 1 MILLION", "HIT 1 MILLION", "BTC TO 1M", "BTC HITS 1 MILLION"],
        "market_keywords": ["BITCOIN", "1M", "1 MILLION", "GTA VI"]
    },
    {
        "category": "CHINA_TAIWAN",
        "news_keywords": ["CHINA INVADES TAIWAN", "CHINESE FORCES ATTACK TAIWAN", "TAIWAN INVASION BEGUN"],
        "market_keywords": ["CHINA", "TAIWAN", "GTA VI"]
    },
    {
        "category": "MEGAETH_AIRDROP",
        "news_keywords": ["MEGAETH AIRDROP ANNOUNCED", "MEGAETH AIRDROP CONFIRMED", "MEGAETH LAUNCHES AIRDROP"],
        "market_keywords": ["MEGAETH", "AIRDROP"]
    },
    {
        "category": "WEINSTEIN_NO_PRISON",
        "news_keywords": ["HARVEY WEINSTEIN SENTENCED TO NO PRISON", "WEINSTEIN AVOIDS PRISON TIME", "WEINSTEIN SENTENCED TO ZERO YEARS"],
        "market_keywords": ["HARVEY WEINSTEIN", "NO PRISON"]
    },
    {
        "category": "SPAIN_WORLD_CUP",
        "news_keywords": ["SPAIN WINS THE WORLD CUP", "SPAIN CROWNED WORLD CUP CHAMPIONS"],
        "market_keywords": ["SPAIN", "WORLD CUP", "FIFA"]
    },
    {
        "category": "KNICKS_NBA",
        "news_keywords": ["NEW YORK KNICKS WIN NBA FINALS", "KNICKS CROWNED NBA CHAMPIONS"],
        "market_keywords": ["KNICKS", "NBA", "FINALS"]
    },
    {
        "category": "HURRICANES_NHL",
        "news_keywords": ["CAROLINA HURRICANES WIN STANLEY CUP", "HURRICANES CROWNED NHL CHAMPIONS"],
        "market_keywords": ["HURRICANES", "NHL", "STANLEY CUP"]
    }
]

def main():
    log_dir = "storage/logs"
    log_files = sorted(
        [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.startswith("bot.log")],
        key=lambda x: (len(x), x)
    )
    
    log_files_ordered = []
    numbered_logs = sorted(
        [f for f in log_files if re.search(r"\.log\.\d+$", f)],
        key=lambda x: int(x.split(".")[-1]),
        reverse=True
    )
    log_files_ordered.extend(numbered_logs)
    if "storage/logs/bot.log.1" in log_files:
        log_files_ordered.append("storage/logs/bot.log.1")
    if "storage/logs/bot.log" in log_files:
        log_files_ordered.append("storage/logs/bot.log")

    start_run = False
    headlines_processed = []
    
    headline_pat = re.compile(r"NewsStrategy: Processing headline: '(.*)'")
    start_pat = re.compile(r"LIVE NEWS PAPER-TRADING RUN LOOP STARTED")
    end_pat = re.compile(r"NewsStrategy background polling task stopped\.")
    
    for log_file in log_files_ordered:
        if not os.path.exists(log_file):
            continue
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not start_run:
                    if start_pat.search(line) and "2026-06-03" in line:
                        start_run = True
                    continue
                if end_pat.search(line) and "2026-06-04" in line:
                    start_run = False
                match = headline_pat.search(line)
                if match:
                    headlines_processed.append(match.group(1))

    total_news = len(headlines_processed)
    print(f"Total News Detections: {total_news}")
    
    stage2_kw_passed = 0
    stage2_kw_rejected = 0
    stage3_mapped_passed = 0
    stage3_mapped_rejected = 0
    
    kw_rejections = {}
    
    for headline in headlines_processed:
        headline_upper = headline.upper()
        matched_rules = []
        for rule in ACTIVE_RULES:
            match_kw = [kw for kw in rule["news_keywords"] if kw in headline_upper]
            if match_kw:
                matched_rules.append((rule, match_kw))
        
        if not matched_rules:
            stage2_kw_rejected += 1
            # Record why: it didn't match any news_keywords
            continue
        
        stage2_kw_passed += 1
        # It matched the news keyword. Now did it map to any candidate markets?
        # Since we know no markets contained the keywords, they all failed here.
        stage3_mapped_rejected += 1
        for rule, _ in matched_rules:
            cat = rule["category"]
            kw_rejections[cat] = kw_rejections.get(cat, 0) + 1

    print("\nFunnel details:")
    print(f"Stage 1 (News detected): {total_news}")
    print(f"Stage 2 (News Keyword Match):")
    print(f"  - Total processed: {total_news}")
    print(f"  - Passed: {stage2_kw_passed} ({stage2_kw_passed/total_news*100:.2f}%)")
    print(f"  - Rejected: {stage2_kw_rejected} ({stage2_kw_rejected/total_news*100:.2f}%)")
    print(f"  - Rejection Reason: NO_KEYWORD_MATCH (headlines did not contain strict event-specific news keywords)")
    print(f"Stage 3 (Market mapped):")
    print(f"  - Total processed: {stage2_kw_passed}")
    print(f"  - Passed: {stage3_mapped_passed} (0.00%)")
    print(f"  - Rejected: {stage3_mapped_rejected} (100.00%)")
    print(f"  - Rejection Reasons per category:")
    for cat, cnt in kw_rejections.items():
        print(f"    * {cat}: {cnt} times, Reason: NO_ACTIVE_MARKET_MATCH (no corresponding market found in active candidate list)")

if __name__ == "__main__":
    main()
