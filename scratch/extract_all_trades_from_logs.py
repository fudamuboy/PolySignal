import os
import re
import json
from datetime import datetime

log_dir = "/Users/slim/Desktop/PolyMarket/storage/logs"
log_files = sorted([os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.startswith("bot.log")])

# We look for lines containing "Updated position for" since that is printed for every successful trade update.
# Example: 2026-05-31 22:55:24,668 - polymarket-bot - INFO - Updated position for <token> | Side: BUY | PnL: +0.0000 | Total Realized: +0.0000
trade_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - .* - INFO - Updated position for (\w+) \| Side: (\w+) \| PnL: ([\+\-\d\.]+) \| Total Realized: ([\+\-\d\.]+)")

# We also want to find if there are passive limit order details like price, size, spread, etc.
# In paper_validation_tracker.py, placed orders are logged as:
# PAPER_PLACE | paper_token_BUY_timestamp | side=BUY | size=size @ price
# When filled, it updates positions. Let's see if we can match them.
place_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - .* - INFO - PAPER_PLACE \| (paper_(\w+)_(\w+)_([\d\.]+)) \| side=(\w+) \| size=([\d\.]+) @ ([\d\.]+) \| estimated_queue_depth=([\d\.]+) shares")

placements = []
trades = []

for lf in log_files:
    try:
        with open(lf, "r", errors="ignore") as fh:
            for line in fh:
                m_p = place_pattern.search(line)
                if m_p:
                    placements.append({
                        "timestamp_str": m_p.group(1),
                        "order_id": m_p.group(2),
                        "token_id": m_p.group(3),
                        "side": m_p.group(4),
                        "epoch": float(m_p.group(5)),
                        "size": float(m_p.group(7)),
                        "price": float(m_p.group(8))
                    })
                    
                m_t = trade_pattern.search(line)
                if m_t:
                    dt = datetime.strptime(m_t.group(1), "%Y-%m-%d %H:%M:%S,%f")
                    trades.append({
                        "timestamp_str": m_t.group(1),
                        "timestamp": dt,
                        "token_id": m_t.group(2),
                        "side": m_t.group(3),
                        "pnl": float(m_t.group(4)),
                        "realized": float(m_t.group(5))
                    })
    except Exception as e:
        print(f"Error reading {lf}: {e}")

print(f"Total placements found in logs: {len(placements)}")
print(f"Total trades found in logs: {len(trades)}")

# Let's match each trade fill with its placement to find the exact price, size, and estimate holding time or cost
reconstructed_trades = []
for t in trades:
    # Find matching placement (closest placement before the trade timestamp for the same token and side)
    matching_p = None
    min_diff = float("inf")
    t_epoch = t["timestamp"].timestamp()
    for p in placements:
        if p["token_id"] == t["token_id"] and p["side"] == t["side"]:
            diff = t_epoch - p["epoch"]
            if 0 <= diff < min_diff:
                min_diff = diff
                matching_p = p
                
    if matching_p:
        reconstructed_trades.append({
            "timestamp_str": t["timestamp_str"],
            "token_id": t["token_id"],
            "side": t["side"],
            "pnl": t["pnl"],
            "size": matching_p["size"],
            "price": matching_p["price"],
            "latency": min_diff
        })
    else:
        # Fallback to defaults or look for another log
        reconstructed_trades.append({
            "timestamp_str": t["timestamp_str"],
            "token_id": t["token_id"],
            "side": t["side"],
            "pnl": t["pnl"],
            "size": 0.0,
            "price": 0.0,
            "latency": 0.0
        })

print(f"\nReconstructed {len(reconstructed_trades)} trades:")
for idx, rt in enumerate(reconstructed_trades, 1):
    print(f"{idx:2d} | {rt['timestamp_str']} | {rt['token_id'][:15]}... | {rt['side']} | size={rt['size']:.2f} @ {rt['price']:.4f} | Latency={rt['latency']:.1f}s")
    
# Save to JSON
with open("storage/paper_validation_reconstructed_trades.json", "w") as fh:
    json.dump(reconstructed_trades, fh, indent=2)
