import json

def load_data():
    with open("storage/gamma_universe_audit_data.json") as f:
        return json.load(f)

def main():
    data = load_data()
    markets = data["markets"]
    
    print(f"Loaded {len(markets)} audited active markets from storage.")
    
    # Enrich each market with an PMM Quality Score:
    # A standard PMM quality score ranks by: volume (40%), liquidity (40%), spread (20%)
    # Let's write a formula:
    # Score = min(10.0, volume / 100000) * 4 + min(10.0, liquidity / 25000) * 4 + max(0.0, 1.0 - spread) * 20
    # Capped at 100.
    for m in markets:
        vol = m["volume"]
        liq = m["liquidity"]
        spread = m["spread"]
        
        # Quality score out of 100
        vol_score = min(10.0, vol / 50000) * 4       # max 40 points
        liq_score = min(10.0, liq / 10000) * 4       # max 40 points
        spread_score = max(0.0, 1.0 - spread) * 20   # max 20 points if spread=0
        
        m["quality_score"] = round(vol_score + liq_score + spread_score, 2)

    # Sorts
    by_liquidity = sorted(markets, key=lambda x: x["liquidity"], reverse=True)
    by_volume = sorted(markets, key=lambda x: x["volume"], reverse=True)
    
    # Tightest spread: sort by spread ascending, then by liquidity descending to break ties
    by_spread = sorted(markets, key=lambda x: (x["spread"], -x["liquidity"]))

    # 1. Output Top 50 by Liquidity
    print("\n--- TOP 10 BY LIQUIDITY ---")
    for i, m in enumerate(by_liquidity[:10], 1):
        print(f"{i}. {m['title'][:50]}... | Liq: ${m['liquidity']:.2f} | Vol: ${m['volume']:.2f} | Spread: {m['spread']*100:.2f}% | Score: {m['quality_score']}")

    # 2. Output Top 10 by Volume
    print("\n--- TOP 10 BY VOLUME ---")
    for i, m in enumerate(by_volume[:10], 1):
        print(f"{i}. {m['title'][:50]}... | Liq: ${m['liquidity']:.2f} | Vol: ${m['volume']:.2f} | Spread: {m['spread']*100:.2f}% | Score: {m['quality_score']}")

    # 3. Output Top 10 by Spread
    print("\n--- TOP 10 BY SPREAD ---")
    for i, m in enumerate(by_spread[:10], 1):
        print(f"{i}. {m['title'][:50]}... | Liq: ${m['liquidity']:.2f} | Vol: ${m['volume']:.2f} | Spread: {m['spread']*100:.2f}% | Score: {m['quality_score']}")

    # 4. Comparative Analysis
    # Let's compare current sampled markets (the first 100 in get_sampling_markets, or a random 100 from the overlap pool)
    # vs the top 100 best full universe markets by Quality Score
    sampled_pool = markets[:100]  # First 100 represent the first visible ones
    
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
            "avg_score": avg_score
        }
        
    sampled_stats = get_stats(sampled_pool)
    best_stats = get_stats(best_pool)
    
    print("\n" + "="*50)
    print("           COMPARATIVE METRICS REPORT            ")
    print("="*50)
    print(f"Metric             | Sampled Markets | Best Full-Universe | Improvement")
    print("-" * 75)
    print(f"Avg Spread         | {sampled_stats['avg_spread']*100:.2f}%          | {best_stats['avg_spread']*100:.2f}%             | {(sampled_stats['avg_spread'] - best_stats['avg_spread'])*100:+.2f}%")
    print(f"Avg 24h Volume     | ${sampled_stats['avg_volume']:,.2f}     | ${best_stats['avg_volume']:,.2f}      | {((best_stats['avg_volume'] - sampled_stats['avg_volume']) / (sampled_stats['avg_volume'] + 1)*100):+.1f}%")
    print(f"Avg Liquidity      | ${sampled_stats['avg_liquidity']:,.2f}     | ${best_stats['avg_liquidity']:,.2f}      | {((best_stats['avg_liquidity'] - sampled_stats['avg_liquidity']) / (sampled_stats['avg_liquidity'] + 1)*100):+.1f}%")
    print(f"Avg Quality Score  | {sampled_stats['avg_score']:.2f}            | {best_stats['avg_score']:.2f}             | {best_stats['avg_score'] - sampled_stats['avg_score']:+.2f}")
    print("="*50)

    # Save outputs for the final report
    with open("storage/gamma_universe_audit_results.json", "w") as fh:
        json.dump({
            "top_liquidity": by_liquidity[:50],
            "top_volume": by_volume[:50],
            "top_spread": by_spread[:50],
            "sampled_stats": sampled_stats,
            "best_stats": best_stats
        }, fh, indent=2)

if __name__ == "__main__":
    main()
