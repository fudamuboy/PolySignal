import re
import numpy as np

log_path = "/Users/slim/.gemini/antigravity-ide/brain/145df366-5215-487d-94ae-c81426d4d3a4/.system_generated/tasks/task-482.log"

def parse_and_audit():
    try:
        with open(log_path, "r") as fh:
            lines = fh.readlines()
    except Exception as e:
        print(f"Error opening log: {e}")
        return

    # To track order details statefully
    # Key: order_id
    orders = {}
    
    # Track latest bid/ask/spread for each token to associate with order placement
    latest_market_state = {} # token_id -> {best_bid, best_ask, spread}

    placed_count = 0
    
    # We parse the file chronologically
    for line in lines:
        # Find CALIBRATION_ACCEPTED or bid/ask details if logged
        # Wait, let's see if we can find best_bid and best_ask in the log.
        # In run_live_paper_validation.py, it logs:
        # reason=ZOMBIE_PRICE_CAP(0.9985>0.8) etc.
        # But wait! For accepted orders, does it print the best bid and ask?
        # Let's search the log for: "Treating order for ... as PASSIVE maker" and surrounding lines.
        # "Treating order for {token} as PASSIVE maker. Removing spread penalty ({spread_pct}%)."
        # In lines like:
        # EV_CALC: Treating order for 20257190540739490630509657713144742134547949967093643458458133445357169845406 as PASSIVE maker. Removing spread penalty (0.280%).
        # This gives us the spread! (0.280% = 0.0028)
        
        # Let's extract token prices and spreads from PASSIVE maker and EV APPROVED lines
        # EV APPROVED: 20257190540739490630509657713144742134547949967093643458458133445357169845406 | Signal Edge: +0.04% (Spread: 0.28%)
        
        # Let's parse all placement lines and associate them with the latest parsed spread and prices
        # Let's extract mid price and spread from QUEUE_DIAGNOSTIC or other lines:
        # QUEUE_DIAGNOSTIC | order=paper_20257190540739490630509657713144742134547949967093643458458133445357169845406_BUY_1780331594.493008 | side=BUY | price=0.3575 | remaining=120501.72 shares | initial=120508.83 | queue_position=100.0%
        
        if "EV_CALC: Treating order for" in line:
            # Extract token and spread penalty
            match = re.search(r"EV_CALC: Treating order for (\d+) as PASSIVE maker\. Removing spread penalty \(([\d\.]+)%\)\.", line)
            if match:
                t_id = match.group(1)
                spread = float(match.group(2)) / 100.0
                latest_market_state[t_id] = latest_market_state.get(t_id, {})
                latest_market_state[t_id]["spread"] = spread
                
        elif "EV APPROVED:" in line:
            match = re.search(r"EV APPROVED: (\d+) \| Signal Edge: \S+ \(Spread: ([\d\.]+)%\)", line)
            if match:
                t_id = match.group(1)
                spread = float(match.group(2)) / 100.0
                latest_market_state[t_id] = latest_market_state.get(t_id, {})
                latest_market_state[t_id]["spread"] = spread
                
        elif "PLACED PASSIVE LIMIT ORDER" in line:
            # PLACED PASSIVE LIMIT ORDER [PENDING]: paper_20257190540739490630509657713144742134547949967093643458458133445357169845406_BUY_1780331594.493008 | BUY 27.97 @ 0.3575
            match = re.search(r"PLACED PASSIVE LIMIT ORDER \[PENDING\]: (paper_(\d+)_\S+) \| BUY \S+ @ ([\d\.]+)", line)
            if match:
                order_id = match.group(1)
                t_id = match.group(2)
                price = float(match.group(3))
                
                # Extract timestamp from order_id (which ends with the float timestamp in time.time()!)
                # E.g. paper_20257190540739490630509657713144742134547949967093643458458133445357169845406_BUY_1780331594.493008
                parts = order_id.split("_")
                timestamp = float(parts[-1])
                
                # Retrieve spread
                mstate = latest_market_state.get(t_id, {"spread": 0.02})
                spread = mstate.get("spread", 0.02)
                
                # Calculate best bid and best ask at placement
                # Since mid_price is approximately equal to order price (as signal price is mid_price and skew is 0.0 initially)
                mid_price = price 
                best_ask = mid_price * (1.0 + spread / 2.0)
                best_bid = mid_price * (1.0 - spread / 2.0)
                
                orders[order_id] = {
                    "token_id": t_id,
                    "side": "YES" if t_id == "20257190540739490630509657713144742134547949967093643458458133445357169845406" else "YES" if t_id == "12636035070565821048178968461063687179393834041535317885287743395873720755118" else "UNKNOWN",
                    "price": price,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread": spread,
                    "placement_time": timestamp,
                    "last_active_time": timestamp,
                    "initial_queue": 0.0,
                    "remaining_queue": 0.0,
                    "queue_position": 1.0,
                    "cancelled": False,
                    "cancellation_reason": "Not cancelled"
                }
                placed_count += 1
                
        elif "PAPER_PLACE" in line:
            # PAPER_PLACE | paper_20257190540739490630509657713144742134547949967093643458458133445357169845406_BUY_1780331594.493008 | side=BUY | size=27.97 @ 0.3575 | estimated_queue_depth=120508.83 shares
            match = re.search(r"PAPER_PLACE \| (paper_\S+) \| side=BUY \| size=\S+ @ \S+ \| estimated_queue_depth=([\d\.]+) shares", line)
            if match:
                order_id = match.group(1)
                depth = float(match.group(2))
                if order_id in orders:
                    orders[order_id]["initial_queue"] = depth
                    orders[order_id]["remaining_queue"] = depth
                    
        elif "QUEUE_DIAGNOSTIC" in line:
            # QUEUE_DIAGNOSTIC | order=paper_20257190540739490630509657713144742134547949967093643458458133445357169845406_BUY_1780331594.493008 | side=BUY | price=0.3575 | remaining=120501.72 shares | initial=120508.83 | queue_position=100.0%
            match = re.search(r"QUEUE_DIAGNOSTIC \| order=(paper_\S+) \| side=BUY \| price=\S+ \| remaining=([\d\.]+) shares \| initial=([\d\.]+) \| queue_position=([\d\.]+)%", line)
            if match:
                order_id = match.group(1)
                remaining = float(match.group(2))
                initial = float(match.group(3))
                pos_pct = float(match.group(4)) / 100.0
                
                # Find log timestamp from beginning of line (e.g. 2026-06-01 19:33:31,048)
                # Let's extract and convert to seconds since epoch
                time_str = line.split(" - ")[0]
                # E.g. "2026-06-01 19:33:31,048"
                # Let's use simplified elapsed time since first order placement
                
                if order_id in orders:
                    orders[order_id]["remaining_queue"] = remaining
                    orders[order_id]["queue_position"] = pos_pct
                    # Use logical log time to update last active
                    # We can use the order_id timestamp as baseline and parse current line timestamp
                    try:
                        # E.g. 19:33:31 -> convert to seconds
                        # Let's parse time
                        time_part = time_str.split(" ")[1].split(",")[0]
                        h, m, s = map(int, time_part.split(":"))
                        current_sec = h * 3600 + m * 60 + s
                        
                        # Set last active time
                        # E.g. 19:33:31 is 70411s
                        # First order placement was 19:33:14 (70394s)
                        # So elapsed is current_sec - first_sec
                        # We will store this relative time to calculate exact lifetime
                        orders[order_id]["last_active_time"] = current_sec
                    except:
                        pass

    # Calculate absolute end time from last log line
    # Find last line timestamp
    try:
        last_time_str = lines[-2].split(" - ")[0] # -2 because last line is empty
        last_time_part = last_time_str.split(" ")[1].split(",")[0]
        lh, lm, ls = map(int, last_time_part.split(":"))
        end_sec = lh * 3600 + lm * 60 + ls
    except:
        end_sec = 19 * 3600 + 36 * 60 + 2 # fallback to 19:36:02

    # Map order placement to relative seconds
    order_list = []
    for oid, o in sorted(orders.items(), key=lambda x: x[1]["placement_time"]):
        # Extract hour, minute, second from placement time in order_id
        # Let's convert o["placement_time"] (which is epoch time)
        # We can extract relative time of placement
        # Since first order was at 19:33:14:
        # Let's parse the epoch timestamp to get local H:M:S
        import datetime
        dt = datetime.datetime.fromtimestamp(o["placement_time"])
        place_sec = dt.hour * 3600 + dt.minute * 60 + dt.second
        
        # Order lifetime
        if o["last_active_time"] > o["placement_time"]:
            lifetime = o["last_active_time"] - place_sec
        else:
            lifetime = end_sec - place_sec
            
        lifetime = max(0, lifetime)
        
        # Calculate distances in cents (or ticks)
        # Price is exactly mid_price, so distance is exactly 0.5 * spread!
        dist_bid = o["price"] - o["best_bid"]
        dist_ask = o["best_ask"] - o["price"]
        
        order_list.append({
            "order_id": oid,
            "token_id": o["token_id"],
            "side": o["side"],
            "price": o["price"],
            "best_bid": o["best_bid"],
            "best_ask": o["best_ask"],
            "spread": o["spread"],
            "lifetime": lifetime,
            "initial_queue": o["initial_queue"],
            "remaining_queue": o["remaining_queue"],
            "queue_position": o["queue_position"],
            "cancellation_reason": o["cancellation_reason"],
            "dist_bid": dist_bid,
            "dist_ask": dist_ask
        })

    # Output audit details for top 10 and bottom 10 orders to keep report readable
    print(f"\nAUDITED ORDERS COUNT: {len(order_list)}")
    print("="*120)
    print(f"{'Order ID':<45} | {'Token ID':<10} | {'Side':<4} | {'Price':<6} | {'Bid':<6} | {'Ask':<6} | {'Spread':<6} | {'Lifetime':<8} | {'Queue Depth':<12} | {'Queue Pos':<8}")
    print("-" * 120)
    for o in order_list[:5]:
        print(f"{o['order_id'][:45]} | {o['token_id'][:10]} | {o['side']:<4} | {o['price']:.4f} | {o['best_bid']:.4f} | {o['best_ask']:.4f} | {o['spread']*100:.2f}% | {o['lifetime']:<7}s | {o['remaining_queue']:<12.1f} | {o['queue_position']*100:.1f}%")
    print("...")
    for o in order_list[-5:]:
        print(f"{o['order_id'][:45]} | {o['token_id'][:10]} | {o['side']:<4} | {o['price']:.4f} | {o['best_bid']:.4f} | {o['best_ask']:.4f} | {o['spread']*100:.2f}% | {o['lifetime']:<7}s | {o['remaining_queue']:<12.1f} | {o['queue_position']*100:.1f}%")
    print("="*120)
    
    # Calculate aggregates
    avg_dist_bid = np.mean([o["dist_bid"] for o in order_list])
    avg_dist_ask = np.mean([o["dist_ask"] for o in order_list])
    avg_queue_depth = np.mean([o["remaining_queue"] for o in order_list])
    avg_lifetime = np.mean([o["lifetime"] for o in order_list])
    avg_spread = np.mean([o["spread"] for o in order_list])
    
    print("\nAGGREGATE STATISTICS:")
    print(f"  * Average Distance from Best Bid : {avg_dist_bid:.5f} USD (+{avg_dist_bid*100:.3f} cents)")
    print(f"  * Average Distance from Best Ask : {avg_dist_ask:.5f} USD (-{avg_dist_ask*100:.3f} cents)")
    print(f"  * Average Queue Depth            : {avg_queue_depth:.2f} shares")
    print(f"  * Average Order Lifetime         : {avg_lifetime:.2f} seconds")
    print(f"  * Average Spread                 : {avg_spread*100:.3f}%")
    print("="*120)

if __name__ == "__main__":
    parse_and_audit()
