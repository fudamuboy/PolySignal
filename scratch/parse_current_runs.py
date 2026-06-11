import re
from collections import Counter

log_path = "/Users/slim/.gemini/antigravity-ide/brain/145df366-5215-487d-94ae-c81426d4d3a4/.system_generated/tasks/task-482.log"

def parse_log():
    try:
        with open(log_path, "r") as fh:
            lines = fh.readlines()
    except Exception as e:
        print(f"Error opening log: {e}")
        return

    signals_generated = 0
    signals_rejected = 0
    orders_placed = 0
    orders_filled = 0
    rejections = Counter()
    placed_orders = set()
    filled_orders = set()
    tokens_placed = set()

    for line in lines:
        if "CALIBRATION_REJECTED" in line:
            signals_generated += 1
            signals_rejected += 1
            # Extract reason
            match = re.search(r"reason=([A-Z_0-9\(\)>\[\],]+)", line)
            if match:
                reason_raw = match.group(1)
                # Normalize the reason (e.g. SPREAD_TOO_WIDE(0.6667>0.2) -> SPREAD_TOO_WIDE)
                reason = reason_raw.split("(")[0]
                rejections[reason] += 1
            else:
                rejections["OTHER"] += 1
        elif "CALIBRATION_ACCEPTED" in line:
            signals_generated += 1
            # Extract token id
            match = re.search(r"token=(\d+)", line)
            if match:
                tokens_placed.add(match.group(1))
        elif "PLACED PASSIVE LIMIT ORDER" in line:
            orders_placed += 1
            match = re.search(r"order=(paper_\S+)", line)
            if match:
                placed_orders.add(match.group(1))
        elif "RESTING ORDER FILLED" in line:
            orders_filled += 1
            match = re.search(r"FILLED: (paper_\S+)", line)
            if match:
                filled_orders.add(match.group(1))

    total_signals = signals_generated
    print("="*60)
    print("           CURRENT LIVE PAPER TRADING AUDIT COUNTS")
    print("="*60)
    print(f"Total Signals Generated: {total_signals}")
    print(f"Total Signals Rejected : {signals_rejected} ({signals_rejected/max(1, total_signals)*100:.2f}%)")
    print(f"Total Signals Accepted : {total_signals - signals_rejected} ({(total_signals - signals_rejected)/max(1, total_signals)*100:.2f}%)")
    print(f"Orders Placed          : {orders_placed}")
    print(f"Orders Filled          : {orders_filled}")
    print(f"Unique Tokens Placed   : {len(tokens_placed)}")
    print("\nREJECTION BREAKDOWN:")
    for reason, count in sorted(rejections.items(), key=lambda x: x[1], reverse=True):
        print(f"  * {reason:<25}: {count:<5} ({count/max(1, signals_rejected)*100:.2f}%)")
    print("="*60)

if __name__ == "__main__":
    parse_log()
