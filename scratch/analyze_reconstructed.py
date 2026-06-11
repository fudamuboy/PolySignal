import json
import collections

with open("storage/paper_validation_reconstructed_trades.json", "r") as f:
    trades = json.load(f)

# Sort trades by timestamp
# Trades format: list of dicts
print(f"Loaded {len(trades)} trades.")

buy_orders = collections.defaultdict(list)
completed_round_trips = []
realized_pnl = 0.0
total_wins = 0.0
total_losses = 0.0
wins_count = 0
losses_count = 0

open_positions = collections.defaultdict(float)  # token_id: size
open_position_costs = collections.defaultdict(float) # token_id: total_cost

for t in trades:
    # Filter out mocked/test tokens if needed, but let's see what unique tokens exist
    token_id = t["token_id"]
    side = t["side"]
    price = float(t.get("price") or 0.0)
    size = float(t.get("size") or 0.0)
    pnl = float(t.get("pnl") or 0.0)

    # Let's reconstruct matching logic or read it
    if side == "BUY":
        buy_orders[token_id].append(t)
        open_positions[token_id] += size
        open_position_costs[token_id] += size * price
    elif side == "SELL":
        open_positions[token_id] -= size
        # Find matching buy order to compute round trip metrics
        if buy_orders[token_id]:
            match_buy = buy_orders[token_id].pop(0)
            buy_price = match_buy["price"]
            buy_size = match_buy["size"]
            # PnL calculated using the real PnL field or reconstructed
            # Let's use the t["pnl"] if it is non-zero, otherwise calculate
            trip_pnl = pnl
            if trip_pnl == 0.0 and price > 0.0 and buy_price > 0.0:
                trip_pnl = (price - buy_price) * size
            
            realized_pnl += trip_pnl
            if trip_pnl > 0:
                total_wins += trip_pnl
                wins_count += 1
            elif trip_pnl < 0:
                total_losses += abs(trip_pnl)
                losses_count += 1
                
            completed_round_trips.append({
                "token_id": token_id,
                "buy_price": buy_price,
                "sell_price": price,
                "size": size,
                "pnl": trip_pnl
            })

# Let's map real token names if possible
token_map = {
    "113585128461808554139088332956774914584404743535283179667078778495379772593123": "Steve Bannon Announcement (NO)",
    "29308269585917040698756405269369277965835278101044026988987379832371144259087": "Pamela Evette Primary (NO)",
    "62419753549060557163164233939381694805760527051999619924042222919057447816": "Pamela Evette Primary (YES)",
    "50346565575310273995396997144874891836871065259829083228393044602519086496922": "Reya FDV Launch (NO)",
    "60977129292396881848833910361112107174416489010090570432106122538120602786646": "Xavier Becerra Election (YES)",
    "42544248033910169115455586624331983477679665735561815306855702312176773945155": "Xavier Becerra Election (NO)",
    "79397895660526576528066965797785113521241099187839967306891454236823723226031": "Mitch McConnell Senate (YES)",
    "101956855572379773739057381598800735026280011489835740928793113207560616123923": "Antonio Reynoso Nomination (NO)",
    "6176387069967725418539368518279260352303182827227966329832868478861860932409": "Mitch McConnell Senate (NO)"
}

# Aggregate by token
market_pnl = collections.defaultdict(float)
market_trades = collections.defaultdict(int)
for trip in completed_round_trips:
    tid = trip["token_id"]
    name = token_map.get(tid, tid[:20])
    market_pnl[name] += trip["pnl"]
    market_trades[name] += 1

print("\n=== METRICS SUMMARY ===")
print("Total trades logged:", len(trades))
print("Completed round trips:", len(completed_round_trips))
print("Realized PnL ($):", realized_pnl)
print("Win rate:", (wins_count / len(completed_round_trips) * 100) if completed_round_trips else 0.0)
print("Profit factor:", (total_wins / total_losses) if total_losses else float('inf'))
print("EV per trade ($):", (realized_pnl / len(completed_round_trips)) if completed_round_trips else 0.0)

# Calculate top/worst
sorted_markets = sorted(market_pnl.items(), key=lambda x: x[1], reverse=True)
print("\nTop Profitable Markets:")
for name, pnl in sorted_markets[:5]:
    print(f"  {name}: {pnl:+.4f} USD ({market_trades[name]} round trips)")

print("\nWorst Performing Markets:")
for name, pnl in sorted_markets[-5:]:
    print(f"  {name}: {pnl:+.4f} USD ({market_trades[name]} round trips)")
