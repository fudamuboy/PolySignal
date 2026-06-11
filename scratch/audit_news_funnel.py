import re
import os
import json

ACTIVE_RULES = [
    {
        "category": "BITCOIN_1M",
        "news_keywords": ["BITCOIN", "TOUCHES 1 MILLION", "REACHES 1 MILLION", "HIT 1 MILLION", "BTC TO 1M", "BTC HITS 1 MILLION"],
        "market_keywords": ["BITCOIN", "1M", "1 MILLION", "GTA VI"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "CHINA_TAIWAN",
        "news_keywords": ["CHINA INVADES TAIWAN", "CHINESE FORCES ATTACK TAIWAN", "TAIWAN INVASION BEGUN"],
        "market_keywords": ["CHINA", "TAIWAN", "GTA VI"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "MEGAETH_AIRDROP",
        "news_keywords": ["MEGAETH AIRDROP ANNOUNCED", "MEGAETH AIRDROP CONFIRMED", "MEGAETH LAUNCHES AIRDROP"],
        "market_keywords": ["MEGAETH", "AIRDROP"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "WEINSTEIN_NO_PRISON",
        "news_keywords": ["HARVEY WEINSTEIN SENTENCED TO NO PRISON", "WEINSTEIN AVOIDS PRISON TIME", "WEINSTEIN SENTENCED TO ZERO YEARS"],
        "market_keywords": ["HARVEY WEINSTEIN", "NO PRISON"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "SPAIN_WORLD_CUP",
        "news_keywords": ["SPAIN WINS THE WORLD CUP", "SPAIN CROWNED WORLD CUP CHAMPIONS"],
        "market_keywords": ["SPAIN", "WORLD CUP", "FIFA"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "KNICKS_NBA",
        "news_keywords": ["NEW YORK KNICKS WIN NBA FINALS", "KNICKS CROWNED NBA CHAMPIONS"],
        "market_keywords": ["KNICKS", "NBA", "FINALS"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    {
        "category": "HURRICANES_NHL",
        "news_keywords": ["CAROLINA HURRICANES WIN STANLEY CUP", "HURRICANES CROWNED NHL CHAMPIONS"],
        "market_keywords": ["HURRICANES", "NHL", "STANLEY CUP"],
        "side": "BUY",
        "target_outcome": "Yes"
    }
]

def main():
    log_dir = "storage/logs"
    log_files = sorted(
        [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.startswith("bot.log")],
        key=lambda x: (len(x), x)
    )
    
    # We want to process chronologically. 
    # bot.log.5 -> bot.log.4 -> bot.log.3 -> bot.log.2 -> bot.log.1 -> bot.log
    log_files_ordered = []
    # Find all numbered logs and sort them in descending order
    numbered_logs = sorted(
        [f for f in log_files if re.search(r"\.log\.\d+$", f)],
        key=lambda x: int(x.split(".")[-1]),
        reverse=True
    )
    log_files_ordered.extend(numbered_logs)
    if "storage/logs/bot.log.1" in log_files:
        # if not in numbered_logs (sometimes it is depending on name, but let's be safe)
        if "storage/logs/bot.log.1" not in log_files_ordered:
            log_files_ordered.append("storage/logs/bot.log.1")
    if "storage/logs/bot.log" in log_files:
        log_files_ordered.append("storage/logs/bot.log")

    print(f"Log files to scan chronologically: {log_files_ordered}")
    
    start_run = False
    headlines_processed = []
    
    # Regex to match: 2026-06-03 00:40:50,433 - polymarket-bot - INFO -    LIVE NEWS PAPER-TRADING RUN LOOP STARTED
    # And: 2026-06-03 00:41:54,192 - polymarket-bot - INFO - NewsStrategy: Processing headline: 'Palo Alto Networks tops earnings as AI fuels cybersecurity urgency'
    
    headline_pat = re.compile(r"NewsStrategy: Processing headline: '(.*)'")
    start_pat = re.compile(r"LIVE NEWS PAPER-TRADING RUN LOOP STARTED")
    end_pat = re.compile(r"NewsStrategy background polling task stopped\.")
    
    for log_file in log_files_ordered:
        if not os.path.exists(log_file):
            continue
        print(f"Scanning {log_file}...")
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not start_run:
                    if start_pat.search(line) and "2026-06-03" in line:
                        start_run = True
                        print(f"Found START of 24h run: {line.strip()}")
                    continue
                
                # Check for end of run
                if end_pat.search(line) and "2026-06-04" in line:
                    print(f"Found END of 24h run: {line.strip()}")
                    # Don't break immediately, just in case there are multiple lines in the same tick, but we can stop parsing
                    start_run = False
                    
                match = headline_pat.search(line)
                if match:
                    headline = match.group(1)
                    headlines_processed.append(headline)
                    
    print(f"\nTotal headlines parsed during active run: {len(headlines_processed)}")
    
    # Deduplicate headlines (since RSS parsing might log duplicates across ticks if not cleaned properly)
    unique_headlines = []
    seen = set()
    for h in headlines_processed:
        if h not in seen:
            seen.add(h)
            unique_headlines.append(h)
            
    print(f"Unique headlines parsed: {len(unique_headlines)}")
    
    # Audit match funnel for each unique headline
    news_matched_count = 0
    matches = []
    
    for headline in unique_headlines:
        headline_upper = headline.upper()
        matched_rules = []
        for rule in ACTIVE_RULES:
            # Check keyword match
            match_kw = [kw for kw in rule["news_keywords"] if kw in headline_upper]
            if match_kw:
                matched_rules.append((rule["category"], match_kw))
                
        if matched_rules:
            news_matched_count += 1
            matches.append((headline, matched_rules))
            
    print(f"\n--- Funnel Stage 2 Audit: News Keyword Match ---")
    print(f"Total News Articles Ingested (Stage 1): {len(unique_headlines)}")
    print(f"Passed Stage 2 (News Keyword Match): {news_matched_count}")
    print(f"Rejected Stage 2 (No Keyword Match): {len(unique_headlines) - news_matched_count}")
    
    if matches:
        print("\nMatched Headlines:")
        for h, m_rules in matches:
            print(f"Headline: '{h}' matched rules: {m_rules}")
    else:
        print("\nNo headlines matched any rules.")
        
    # Analyze the most common words in headlines to see why they didn't match
    word_freq = {}
    for h in unique_headlines:
        words = re.findall(r"\w+", h.upper())
        for w in words:
            if len(w) > 3:
                word_freq[w] = word_freq.get(w, 0) + 1
                
    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    print("\nMost common words (len > 3) in headlines:")
    for word, freq in sorted_words[:20]:
        print(f"  {word}: {freq}")

if __name__ == "__main__":
    main()
