import json

def load_data():
    with open("storage/gamma_universe_audit_data.json") as f:
        return json.load(f)

def main():
    data = load_data()
    markets = data["markets"]
    
    # Enrich Quality Score
    for m in markets:
        vol = m["volume"]
        liq = m["liquidity"]
        spread = m["spread"]
        
        # Quality score out of 100
        vol_score = min(10.0, vol / 50000) * 4       # max 40 points
        liq_score = min(10.0, liq / 10000) * 4       # max 40 points
        spread_score = max(0.0, 1.0 - spread) * 20   # max 20 points if spread=0
        
        m["quality_score"] = round(vol_score + liq_score + spread_score, 2)

    # In our audit, we saved overlapping conditionIds. Let's load the overlapping markets
    # Let's load the raw data with overlapping markers
    # Wait, we can identify sampled markets because get_sampling_markets() returned 1000 markets.
    # In the audit script, we fetched 1000 active markets from Gamma, and get_sampling_markets returned 1000 markets.
    # Let's write a more precise comparison:
    # A. Sampled Pool: Markets that are in `overlapping` (349 markets)
    # B. Best Pool: Top 100 markets in the full universe by Quality Score
    
    # We need to load conditionIds of get_sampling_markets to mark them
    # Let's do that by reading storage/active_markets_sampling.json which has the 1000 sampled markets
    with open("storage/active_markets_sampling.json") as f:
        samp_data = json.load(f)
        samp_ids = {m["condition_id"].lower() for m in samp_data["data"] if m.get("condition_id")}
        
    sampled_pool = [m for m in markets if m["conditionId"].lower() in samp_ids]
    
    # Sort entire universe by quality score
    best_pool = sorted(markets, key=lambda x: x["quality_score"], reverse=True)[:100]
    
    def get_stats(pool):
        avg_spread = sum(m["spread"] for m in pool) / len(pool) if pool else 0
        avg_volume = sum(m["volume"] for m in pool) / len(pool) if pool else 0
        avg_liquidity = sum(m["liquidity"] for m in pool) / len(pool) if pool else 0
        avg_score = sum(m["quality_score"] for m in pool) / len(pool) if pool else 0
        return {
            "avg_spread": avg_spread,
            "avg_volume": avg_volume,
            "avg_liquidity": avg_liquidity,
            "avg_score": avg_score,
            "count": len(pool)
        }
        
    sampled_stats = get_stats(sampled_pool)
    best_stats = get_stats(best_pool)
    
    print("\n" + "="*60)
    print("      GENUINE SAMPLED VS BEST COMPARATIVE METRICS REPORT       ")
    print("="*60)
    print(f"Metric             | Sampled Pool ({sampled_stats['count']}) | Best Pool ({best_stats['count']}) | Improvement")
    print("-" * 80)
    print(f"Avg Spread         | {sampled_stats['avg_spread']*100:.3f}%          | {best_stats['avg_spread']*100:.3f}%          | {(sampled_stats['avg_spread'] - best_stats['avg_spread'])*100:+.3f}%")
    print(f"Avg 24h Volume     | ${sampled_stats['avg_volume']:,.2f}      | ${best_stats['avg_volume']:,.2f}      | {((best_stats['avg_volume'] - sampled_stats['avg_volume']) / (sampled_stats['avg_volume'] + 1)*100):+.1f}%")
    print(f"Avg Liquidity      | ${sampled_stats['avg_liquidity']:,.2f}      | ${best_stats['avg_liquidity']:,.2f}      | {((best_stats['avg_liquidity'] - sampled_stats['avg_liquidity']) / (sampled_stats['avg_liquidity'] + 1)*100):+.1f}%")
    print(f"Avg Quality Score  | {sampled_stats['avg_score']:.2f}             | {best_stats['avg_score']:.2f}             | {best_stats['avg_score'] - sampled_stats['avg_score']:+.2f}")
    print("="*60)

if __name__ == "__main__":
    main()
