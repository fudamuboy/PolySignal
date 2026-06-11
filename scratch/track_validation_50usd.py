import sys
import os
import sqlite3
import datetime
from collections import defaultdict

# Add parent directory to path to allow importing app configs
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB_PATH = "storage/database.db"
LOG_DIR = "/Users/slim/.gemini/antigravity-ide/brain/9ff69cdf-b57b-4ced-a70b-08b0dbe6e173/.system_generated/tasks/"

def get_latest_bot_log():
    if not os.path.exists(LOG_DIR):
        return None, None
    log_files = []
    for f in os.listdir(LOG_DIR):
        if f.endswith(".log") and "task-" in f:
            filepath = os.path.join(LOG_DIR, f)
            # Verify this is a bot log and not a timer/other task
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as file:
                    head = file.read(1000)
                    if "polymarket-bot" not in head:
                        continue
            except Exception:
                continue
            log_files.append((filepath, os.path.getmtime(filepath)))
    if not log_files:
        return None, None
    # Sort by mtime descending
    log_files.sort(key=lambda x: x[1], reverse=True)
    return log_files[0][0], log_files[0][1]

def get_bot_start_time(log_filepath):
    try:
        with open(log_filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                # Find the first line with a timestamp, e.g. "2026-06-05 21:59:51,093"
                if len(line) > 19 and line[0:4].isdigit() and line[4] == '-' and line[7] == '-':
                    ts_str = line[0:19] # "2026-06-05 21:59:51"
                    # Log timestamps are local time (UTC+3). DB timestamps are UTC.
                    local_ts = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    # Convert local time (UTC+3) to UTC time
                    utc_ts = local_ts - datetime.timedelta(hours=3)
                    return utc_ts, local_ts
    except Exception as e:
        print(f"Error reading start time: {e}")
    return None, None

def compile_metrics_50usd():
    log_file, _ = get_latest_bot_log()
    if not log_file:
        print("No bot log files found. Ensure the bot is running.")
        return
    
    start_utc, start_local = get_bot_start_time(log_file)
    if not start_utc:
        print(f"Could not determine start time from log: {log_file}")
        return
        
    print(f"Detected active bot log: {os.path.basename(log_file)}")
    print(f"Bot start time (Local):  {start_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Bot start time (UTC/DB): {start_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Query trades from DB since start time
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp ASC",
        (start_utc.strftime("%Y-%m-%d %H:%M:%S"),)
    )
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # Group trades by token to pair BUY and SELL
    token_trades = defaultdict(list)
    for t in trades:
        token_trades[t["token_id"]].append(t)
        
    # Pair trades
    completed_trades = []
    for token_id, list_t in token_trades.items():
        buys = [x for x in list_t if x["side"] == "BUY"]
        sells = [x for x in list_t if x["side"] == "SELL"]
        
        # Pair chronologically
        for b in buys:
            matching_sell = None
            b_time = datetime.datetime.strptime(b["timestamp"], "%Y-%m-%d %H:%M:%S")
            for s in sells:
                s_time = datetime.datetime.strptime(s["timestamp"], "%Y-%m-%d %H:%M:%S")
                if s_time >= b_time:
                    matching_sell = s
                    sells.remove(s)
                    break
            
            if matching_sell:
                s_time = datetime.datetime.strptime(matching_sell["timestamp"], "%Y-%m-%d %H:%M:%S")
                hold_time = (s_time - b_time).total_seconds()
                
                completed_trades.append({
                    "token_id": token_id,
                    "strategy": b["strategy"],
                    "buy_price": b["price"],
                    "sell_price": matching_sell["price"],
                    "size": b["size"],
                    "pnl": matching_sell["realized_pnl"],
                    "hold_time": hold_time,
                    "buy_value": b["size"] * b["price"],
                    "timestamp": b["timestamp"]
                })
                
    completed_trades.sort(key=lambda x: x["timestamp"])
    
    total_trades = len(completed_trades)
    wins = [x for x in completed_trades if x["pnl"] > 0]
    losses = [x for x in completed_trades if x["pnl"] < 0]
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
    
    gross_profits = sum(x["pnl"] for x in wins)
    gross_losses = sum(x["pnl"] for x in losses)
    profit_factor = (gross_profits / abs(gross_losses)) if gross_losses != 0 else (gross_profits if gross_profits > 0 else 1.0)
    
    net_pnl = sum(x["pnl"] for x in completed_trades)
    ev = (net_pnl / total_trades) if total_trades > 0 else 0.0
    
    # Maximum drawdown on cumulative PnL
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in completed_trades:
        cum_pnl += t["pnl"]
        if cum_pnl > peak:
            peak = cum_pnl
        dd = peak - cum_pnl
        if dd > max_dd:
            max_dd = dd
            
    total_capital_pool = 50.0
    total_buy_value = sum(x["buy_value"] for x in completed_trades)
    capital_turnover = total_buy_value / total_capital_pool
    
    print("==================================================")
    print("50 USD VALIDATION RUN — GLOBAL PORTFOLIO METRICS")
    print("==================================================")
    print(f"* Total completed trades: {total_trades}")
    print(f"* Win rate:               {win_rate:.2f}%")
    print(f"* Profit factor:          {profit_factor:.2f}")
    print(f"* EV per trade:           ${ev:.4f}")
    print(f"* Realized PnL:          ${net_pnl:+.4f} USD")
    print(f"* Maximum drawdown:      ${max_dd:.4f} USD")
    print(f"* Capital turnover:       {capital_turnover:.2f}x (Total Buy Vol: ${total_buy_value:.2f})")
    print()
    
    strat_trades = defaultdict(list)
    for t in completed_trades:
        strat_trades[t["strategy"]].append(t)
        
    print("==================================================")
    print("50 USD VALIDATION RUN — ENGINE BREAKDOWN")
    print("==================================================")
    
    all_strategies = ["TwoSidedMMStrategy", "NewsStrategy", "MomentumStrategy", "VolumeSpikeStrategy", "OrderBookImbalanceStrategy", "HEDGE"]
    for strat in all_strategies:
        s_list = strat_trades[strat]
        s_total = len(s_list)
        s_wins = [x for x in s_list if x["pnl"] > 0]
        s_win_rate = (len(s_wins) / s_total * 100) if s_total > 0 else 0.0
        s_pnl = sum(x["pnl"] for x in s_list)
        s_ev = (s_pnl / s_total) if s_total > 0 else 0.0
        
        s_cum = 0.0
        s_peak = 0.0
        s_dd = 0.0
        for x in s_list:
            s_cum += x["pnl"]
            if s_cum > s_peak:
                s_peak = s_cum
            dd = s_peak - s_cum
            if dd > s_dd:
                s_dd = dd
                
        avg_hold = sum(x["hold_time"] for x in s_list) / s_total if s_total > 0 else 0.0
        
        print(f"Engine: {strat}")
        print(f"  * Trades executed:      {s_total}")
        print(f"  * Win rate:             {s_win_rate:.2f}%")
        print(f"  * EV per trade:         ${s_ev:+.4f}")
        print(f"  * Realized PnL:         ${s_pnl:+.4f} USD")
        print(f"  * Drawdown:             ${s_dd:.4f} USD")
        print(f"  * Avg holding time:     {avg_hold:.1f} seconds")
        print("-" * 30)
    print()
    
    # Parse cooldown bypasses for this specific log file
    total_bypasses, bypass_breakdown = count_cooldown_bypasses(log_file)
    print("==================================================")
    print("50 USD VALIDATION RUN — COOLDOWN BYPASS ANALYSIS")
    print("==================================================")
    print(f"* Total bypasses triggered: {total_bypasses}")
    for b_strat, count in bypass_breakdown.items():
        print(f"  - {b_strat:28} : {count} times")
    print()

def count_cooldown_bypasses(log_filepath):
    total_bypasses = 0
    strategy_counts = defaultdict(int)
    try:
        with open(log_filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "COOLDOWN BYPASS:" in line:
                    total_bypasses += 1
                    if "NewsStrategy" in line:
                        strategy_counts["NewsStrategy"] += 1
                    elif "OrderBookImbalanceStrategy" in line:
                        strategy_counts["OrderBookImbalanceStrategy"] += 1
                    elif "MomentumStrategy" in line:
                        strategy_counts["MomentumStrategy"] += 1
                    else:
                        strategy_counts["Unknown"] += 1
    except Exception:
        pass
    return total_bypasses, strategy_counts

if __name__ == "__main__":
    compile_metrics_50usd()
