import asyncio
import sqlite3
import re
import os
import time
import json
import datetime
from app.data_fetcher import DataFetcher

async def audit():
    print("=== STARTING STRICT EXIT AUDIT ===")
    
    # 1. Load open positions from DB
    conn = sqlite3.connect('storage/database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT token_id, size, avg_price, updated_at FROM positions')
    rows = cursor.fetchall()
    
    positions = []
    for r in rows:
        positions.append({
            'token_id': r[0],
            'size': r[1],
            'avg_price': r[2],
            'db_updated_at': r[3]
        })
    print(f"Loaded {len(positions)} open positions from DB.")

    # Initialize data fetcher
    df = DataFetcher()
    
    # We will search the log files for each token to find:
    # - exact entry time and side
    # - exit orders placed, their price, placement time, status, queue depth
    # We scan bot.log* files in storage/logs
    log_dir = 'storage/logs'
    log_files = sorted([f for f in os.listdir(log_dir) if f.startswith('bot.log')], 
                       key=lambda x: (len(x), x))
    
    # Helper to parse logs chronologically
    all_lines = []
    for f in log_files:
        path = os.path.join(log_dir, f)
        with open(path, 'r', errors='ignore') as fh:
            for line in fh:
                all_lines.append((f, line))
                
    # Sort or parse
    # Note: bot.log is latest, bot.log.1, bot.log.2 are older. Let's read them in correct order.
    # The logs are rotated, so bot.log is current, bot.log.1 is previous, bot.log.2 is older, etc.
    # Let's read them in chronological order: bot.log.5, bot.log.4, bot.log.3, bot.log.2, bot.log.1, bot.log
    chronological_files = []
    for i in range(5, 0, -1):
        f = f"bot.log.{i}"
        if f in log_files:
            chronological_files.append(f)
    if "bot.log" in log_files:
        chronological_files.append("bot.log")
        
    print(f"Log file order for chronological parsing: {chronological_files}")
    
    chron_lines = []
    for f in chronological_files:
        path = os.path.join(log_dir, f)
        with open(path, 'r', errors='ignore') as fh:
            chron_lines.extend(fh.readlines())
            
    print(f"Loaded {len(chron_lines)} chronological log lines.")

    # 2. Perform audit for each position
    results = []
    now_ts = time.time()
    
    for pos in positions:
        tid = pos['token_id']
        print(f"\nAuditing token: {tid}")
        
        # A. Fetch current market data
        best_bid = 0.0
        best_ask = 0.0
        bid_depth = 0.0
        ask_depth = 0.0
        
        try:
            ob = await df.get_orderbook(tid)
            if ob:
                raw_bids = ob.get("bids", []) if isinstance(ob, dict) else getattr(ob, "bids", [])
                raw_asks = ob.get("asks", []) if isinstance(ob, dict) else getattr(ob, "asks", [])
                
                def gp(lvl):
                    if hasattr(lvl, "price"): return float(lvl.price)
                    if isinstance(lvl, dict): return float(lvl.get("price") or lvl.get("p") or 0)
                    return 0.0
                
                def gs(lvl):
                    if hasattr(lvl, "size"): return float(lvl.size)
                    if isinstance(lvl, dict): return float(lvl.get("size") or 0)
                    return 0.0
                
                bids = sorted(raw_bids, key=gp, reverse=True)
                asks = sorted(raw_asks, key=gp)
                
                if bids:
                    best_bid = gp(bids[0])
                    bid_depth = sum(gs(b) for b in bids[:2]) * best_bid
                if asks:
                    best_ask = gp(asks[0])
                    ask_depth = sum(gs(a) for a in asks[:2]) * best_ask
        except Exception as e:
            print(f"Error fetching orderbook for {tid}: {e}")
            
        print(f"  Live Best Bid: {best_bid}, Best Ask: {best_ask}")
        
        # B. Scan logs for this token
        entry_log_time = None
        entry_strategy = None
        exit_order_id = None
        exit_order_price = None
        exit_order_status = "NO_ORDER"
        exit_order_placed_time = None
        queue_depth_ahead = None
        
        # We search chronologically
        for line in chron_lines:
            if tid in line:
                # Look for FILLED BUY or position update
                # e.g. "Updated position for 202571... | Side: BUY"
                if "Updated position" in line and "Side: BUY" in line:
                    match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})", line)
                    if match:
                        entry_log_time = match.group(1)
                    match_strat = re.search(r"strategy=(\w+)", line)
                    if match_strat:
                        entry_strategy = match_strat.group(1)
                        
                # Look for PLACED passive limit order for exit (SELL side)
                # e.g. "PLACED PASSIVE LIMIT ORDER [PENDING]: paper_17708..._SELL_... | SELL 27.97 @ 0.6430"
                if "PLACED PASSIVE LIMIT ORDER" in line and "SELL" in line:
                    match_order = re.search(r"paper_[0-9]+_SELL_[0-9\.]+", line)
                    if match_order:
                        exit_order_id = match_order.group(0)
                        exit_order_status = "PENDING"
                    match_price = re.search(r"@ ([\d\.]+)", line)
                    if match_price:
                        exit_order_price = float(match_price.group(1))
                    match_time = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})", line)
                    if match_time:
                        exit_order_placed_time = match_time.group(1)
                        
                # Look for PAPER_PLACE estimated queue depth
                # e.g. "PAPER_PLACE | paper_17708... | side=SELL | ... | estimated_queue_depth=... shares"
                if "PAPER_PLACE" in line and "SELL" in line:
                    match_q = re.search(r"estimated_queue_depth=([\d\.]+)", line)
                    if match_q:
                        queue_depth_ahead = float(match_q.group(1))
                        
                # Look for QUEUE_DIAGNOSTIC or cancellations or fills
                if "QUEUE_DIAGNOSTIC" in line and exit_order_id and exit_order_id in line:
                    # extract remaining depth
                    match_rem = re.search(r"remaining=([\d\.]+)", line)
                    if match_rem:
                        queue_depth_ahead = float(match_rem.group(1))
                        
                if "RESTING ORDER FILLED" in line and exit_order_id and exit_order_id in line:
                    exit_order_status = "FILLED"
                    
                if "Cancel" in line and exit_order_id and exit_order_id in line:
                    exit_order_status = "CANCELLED"
                    
        # Parse entry time to datetime object to find age
        time_since_entry = "UNKNOWN"
        if entry_log_time:
            try:
                # "2026-06-01 20:05:42,839"
                dt = datetime.datetime.strptime(entry_log_time.split(",")[0], "%Y-%m-%d %H:%M:%S")
                # Local time: the ADDITIONS_METADATA says current local time is 2026-06-02 15:47:53
                # The log is in the same local time timezone
                now_dt = datetime.datetime(2026, 6, 2, 15, 47, 53)
                delta = now_dt - dt
                time_since_entry = delta.total_seconds() # age in seconds
            except Exception as ex:
                print(f"Error parsing entry time {entry_log_time}: {ex}")
                
        # Calculate unrealized PnL: (best_bid - entry_price) * size
        unrealized_pnl = (best_bid - pos['avg_price']) * pos['size']
        
        # Planned Exit Price
        # In PMM TwoSidedMMStrategy, exit price is entry_price, or calculated? Let's check
        # Wait, if entry_price is 0.3575, and side was BUY for YES token, the opposing NO token was also bought.
        # Wait! For Two-Sided MM, the positions were:
        # Token 1 (YES): 27.97 @ 0.3575
        # Token 2 (NO): 27.97 @ 0.3575 (Wait, no, the second position is 12636... which is also size 27.97 @ 0.3575!)
        # Token 3 (YES): 27.97 @ 0.6430
        # Token 4 (NO): 27.97 @ 0.6430
        # Wait! Let's check why YES and NO both have same entry prices!
        # If YES is 0.3575 and NO is 0.3575, the sum is 0.715!
        # Wait, YES is 0.3575 and opposing is YES/NO?
        # Let's check what the tokens actually are.
        # Let's add all this to our audit record
        audit_record = {
            'token_id': tid,
            'entry_price': pos['avg_price'],
            'size': pos['size'],
            'best_bid': best_bid,
            'best_ask': best_ask,
            'unrealized_pnl': unrealized_pnl,
            'entry_time_str': entry_log_time,
            'age_seconds': time_since_entry,
            'exit_order_id': exit_order_id,
            'planned_exit_price': exit_order_price,
            'exit_order_status': exit_order_status,
            'queue_depth': queue_depth_ahead
        }
        results.append(audit_record)
        
    print("\n=== AUDIT RESULTS ===")
    print(json.dumps(results, indent=2))
    
    # Save results to a json file
    with open('storage/exit_audit_results.json', 'w') as fh:
        json.dump(results, fh, indent=2)

if __name__ == "__main__":
    asyncio.run(audit())
