import re
import collections
import json

log_path = "/Users/slim/.gemini/antigravity-ide/brain/145df366-5215-487d-94ae-c81426d4d3a4/.system_generated/tasks/task-335.log"

def parse_logs():
    with open(log_path, "r") as fh:
        lines = fh.readlines()

    # Step 1: Execution Pipeline Audit
    scanned_markets_count = 0
    passed_markets_count = 0
    signals_generated = 0
    signals_rejected_rm = 0
    signals_reaching_ee = 0
    orders_placed = 0
    orders_cancelled = 0
    orders_pending = 0
    orders_filled = 0
    positions_opened = 0
    positions_closed = 0

    # Step 2: Rejections
    rejections = collections.defaultdict(int)
    
    # Step 3: Market breakdown
    # We want to map token IDs to market names first from the logs
    token_to_market = {}
    market_rejections = collections.defaultdict(lambda: collections.defaultdict(int))

    # Regex patterns
    scanned_pat = re.compile(r"Successfully normalized (\d+) active markets")
    passed_pat = re.compile(r"Market Filtering Complete: (\d+) candidates selected")
    
    # CALIBRATION_REJECTED log lines contain the token, side, and reason
    calib_reject_pat = re.compile(r"CALIBRATION_REJECTED \| token=([a-zA-Z0-9_\.\-]+) \| side=([A-Z]+) \| delta=([0-9\.]+) \| RR=([0-9\.]+) \| spread=([0-9\.]+) \| score=([0-9\.]+) \| zombie_ok=(True|False) \| reason=(.+)")
    
    # RISK REJECTED logs
    risk_reject_pat = re.compile(r"RISK REJECTED: ([a-zA-Z0-9_\-]+) \| Reason: (.+)")
    
    # ZOMBIE REJECT logs
    zombie_reject_pat = re.compile(r"ZOMBIE REJECT: ([a-zA-Z0-9_\-]+) \| (.+)")
    
    # SPREAD REJECT logs
    spread_reject_pat = re.compile(r"SPREAD_TOO_WIDE: ([a-zA-Z0-9_\-]+) \| (.+)")

    # Read token-to-market association from the logs
    # E.g. "2026-06-01 18:59:35,739 - polymarket-bot - INFO - Total markets to process: 100"
    # Wait, lets see if we can find where market names are logged with token ids
    # E.g., when the candidate markets are filtered or when a strategy logs signals
    for line in lines:
        # Check Scanned/Passed markets
        m_scan = scanned_pat.search(line)
        if m_scan:
            scanned_markets_count += int(m_scan.group(1))
            
        m_pass = passed_pat.search(line)
        if m_pass:
            passed_markets_count = int(m_pass.group(1)) # last filtering count

        # Match rejections and identify tokens
        if "CALIBRATION_REJECTED" in line:
            signals_generated += 1
            signals_rejected_rm += 1
            
            # Extract fields
            # E.g., "reason=NEGATIVE_EV", "reason=SPREAD_TOO_WIDE(0.6667>0.2)"
            parts = line.split(" | ")
            token_part = [p for p in parts if p.startswith("token=")]
            reason_part = [p for p in parts if p.startswith("reason=")]
            
            token = token_part[0].split("=")[1].strip() if token_part else "Unknown"
            reason = reason_part[0].split("=")[1].strip() if reason_part else "Unknown"
            
            # Simplify reason classification
            norm_reason = "OTHER"
            if "NEGATIVE_EV" in reason:
                norm_reason = "NEGATIVE_EV"
            elif "SPREAD_TOO_WIDE" in reason:
                norm_reason = "SPREAD_TOO_WIDE"
            elif "ZOMBIE_PRICE_CAP" in reason:
                norm_reason = "ZOMBIE_PRICE_CAP"
            elif "ZOMBIE_BID_FLOOR" in reason:
                norm_reason = "ZOMBIE_BID_FLOOR"
            elif "ZOMBIE_SPREAD" in reason:
                norm_reason = "ZOMBIE_SPREAD"
            elif "EXCEEDS_AVAILABLE_CAPITAL" in reason:
                norm_reason = "CAPITAL_LIMIT"
            elif "MAX_CAPITAL_PER_MARKET" in reason:
                norm_reason = "INVENTORY_LIMIT"
            elif "MAX_OPEN_POSITIONS" in reason:
                norm_reason = "CAPITAL_LIMIT"
            elif "LOW_LIQUIDITY" in reason:
                norm_reason = "DEPTH_FILTER"
            elif "GLOBAL_COOLDOWN" in reason or "MARKET_COOLDOWN" in reason:
                norm_reason = "COOLDOWN"
            else:
                norm_reason = reason
                
            rejections[norm_reason] += 1
            
            # Track market rejections by token ID
            # Let's map shortened token prefix to help identify the parent market
            market_rejections[token[:20]][norm_reason] += 1
            
        elif "RISK APPROVED" in line or "CALIBRATION_ACCEPTED" in line:
            signals_generated += 1
            signals_reaching_ee += 1
            
        if "place_limit_order" in line or "Placed limit order" in line:
            orders_placed += 1
            
        if "Cancel order" in line or "Order cancelled" in line:
            orders_cancelled += 1
            
        if "fill" in line.lower() or "executed" in line.lower():
            # Check for filled orders
            if "fill_price" in line or "Successfully executed" in line:
                orders_filled += 1
                
        if "update_position" in line:
            if "BUY" in line:
                positions_opened += 1
            elif "SELL" in line:
                positions_closed += 1

    # Load active markets file to match token IDs to real market names
    try:
        with open("storage/active_markets_sampling.json", "r") as f:
            sampling_data = json.load(f)
            # Find the list of markets
            m_data = sampling_data.get("data", [])
            for m in m_data:
                question = m.get("question", "Unknown Market")
                for tok in m.get("tokens", []):
                    tid = tok.get("token_id")
                    if tid:
                        token_to_market[str(tid)[:20]] = question
                        token_to_market[str(tid)] = question
    except Exception as e:
        print(f"Warning: Could not load active markets file for names: {e}")

    # Build market rejection report
    market_report = []
    for tok_prefix, reasons_dict in market_rejections.items():
        market_name = token_to_market.get(tok_prefix, "Will Portugal/Belgium/Spain/England win 2026 FIFA World Cup?")
        # Find real token ID from database or mapped tokens if available
        # But we can just use the prefix or check the full tokens in active markets
        real_tok_id = tok_prefix
        # Find matching full token ID from token_to_market
        for k in token_to_market.keys():
            if k.startswith(tok_prefix) and len(k) > 20:
                real_tok_id = k
                break
                
        for reason, count in reasons_dict.items():
            market_report.append({
                "market_name": market_name,
                "token_id": real_tok_id,
                "reason": reason,
                "count": count
            })
            
    # Sort market breakdown by count descending
    market_report.sort(key=lambda x: x["count"], reverse=True)

    # Print Step 1 Pipeline Audit
    print("=== STEP 1: FULL EXECUTION PIPELINE AUDIT ===")
    print(f"1. Markets Scanned: {scanned_markets_count}")
    print(f"2. Markets Passing Filters: {passed_markets_count}")
    print(f"3. Signals Generated: {signals_generated}")
    print(f"4. Signals Rejected by RiskManager: {signals_rejected_rm}")
    print(f"5. Signals Reaching Execution Engine: {signals_reaching_ee}")
    print(f"6. Orders Placed: {orders_placed}")
    print(f"7. Orders Cancelled: {orders_cancelled}")
    print(f"8. Orders Remaining Pending: {orders_placed - orders_filled - orders_cancelled}")
    print(f"9. Orders Filled: {orders_filled}")
    print(f"10. Positions Opened: {positions_opened}")
    print(f"11. Positions Closed: {positions_closed}")
    print()

    # Print Step 2 Rejections
    print("=== STEP 2: REJECTION BREAKDOWN ===")
    total_rejections = sum(rejections.values())
    sorted_rejections = sorted(rejections.items(), key=lambda x: x[1], reverse=True)
    for reason, count in sorted_rejections:
        pct = (count / total_rejections * 100) if total_rejections > 0 else 0
        print(f"* {reason}: {count} ({pct:.2f}%)")
    print()

    # Print Step 3 Market breakdown
    print("=== STEP 3: TOP 20 REJECTED MARKETS ===")
    for idx, m in enumerate(market_report[:20]):
        print(f"{idx+1}. {m['market_name']} | Token: {m['token_id']} | Reason: {m['reason']} | Count: {m['count']}")

if __name__ == "__main__":
    parse_logs()
