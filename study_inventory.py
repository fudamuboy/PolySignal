import asyncio
import random
import pandas as pd
import numpy as np
from app.execution_engine import ExecutionEngine
from app.position_manager import PositionManager

# Define simulated tokens
TOKENS = [f"token_{i}" for i in range(5)]

class SyntheticMarket:
    def __init__(self, token_id):
        self.token_id = token_id
        self.mid = random.uniform(0.30, 0.70)
        self.spread_pct = random.uniform(0.015, 0.035)
        self.update_prices()

    def update_prices(self):
        self.mid += random.normalvariate(0, 0.003)
        self.mid = max(0.05, min(0.95, self.mid))
        spread = self.mid * self.spread_pct
        self.best_bid = self.mid - (spread / 2)
        self.best_ask = self.mid + (spread / 2)
        self.last_price = self.mid + random.normalvariate(0, spread / 4)
        
    def get_market_data(self):
        return {
            "token_id": self.token_id,
            "best_bid": round(self.best_bid, 4),
            "best_ask": round(self.best_ask, 4),
            "last_price": round(self.last_price, 4),
            "spread": round((self.best_ask - self.best_bid) / self.mid, 4),
            "mid_price": round(self.mid, 4)
        }

async def run_study():
    ee = ExecutionEngine(paper_trading=True)
    pm = PositionManager()
    
    pm.clear_all_positions()
    pm.max_exposure_observed = 0.0
    pm.total_exposure_sum = 0.0
    pm.ticks_count = 0
    
    markets = [SyntheticMarket(tid) for tid in TOKENS]
    submission_ticks = {}
    
    # Store records for pandas analysis
    records = []
    
    for tick in range(1, 1001):
        market_data_map = {}
        enriched_tokens = []
        for m in markets:
            m.update_prices()
            mdata = m.get_market_data()
            market_data_map[m.token_id] = mdata
            enriched_tokens.append(mdata)
            
        # Check fills
        fills = ee.check_resting_fills(market_data_map)
        for fill in fills:
            order_id = fill["order_id"]
            if order_id in submission_ticks:
                del submission_ticks[order_id]
            pm.update_position(
                token_id=fill["token_id"],
                size=fill["fill_size"],
                price=fill["fill_price"],
                side=fill["side"],
                strategy=fill.get("strategy", "PMM")
            )
            
        # Update metrics and save snapshot
        inv_metrics = pm.update_inventory_metrics(enriched_tokens)
        records.append({
            "tick": tick,
            "exposure": inv_metrics["total_exposure"],
            "concentration": inv_metrics["concentration"]
        })
        
        # Passive quoting logic
        for m in markets:
            tid = m.token_id
            mdata = market_data_map[tid]
            token_orders = [o for o in ee.pending_orders if o["token_id"] == tid]
            
            # Cancel if mid price moved too far
            for order in list(token_orders):
                if abs(order["price"] - mdata["mid_price"]) > 0.015:
                    await ee.cancel_order(order["order_id"])
                    token_orders.remove(order)
                    if order["order_id"] in submission_ticks:
                        del submission_ticks[order["order_id"]]
            
            # Place new orders
            has_buy = any(o["side"] == "BUY" for o in token_orders)
            has_sell = any(o["side"] == "SELL" for o in token_orders)
            
            # Place BUY passive order
            if not has_buy and len(pm.positions) < 4:
                bid_price = mdata["best_bid"]
                res = await ee.place_limit_order(tid, bid_price, 10.0, "BUY")
                if res["status"] == "PENDING":
                    submission_ticks[res["order_id"]] = tick
                    
            # Place SELL passive order (only if we hold position)
            open_pos = pm.positions.get(tid, {})
            if open_pos.get("size", 0) > 0 and not has_sell:
                ask_price = mdata["best_ask"]
                res = await ee.place_limit_order(tid, ask_price, open_pos["size"], "SELL")
                if res["status"] == "PENDING":
                    submission_ticks[res["order_id"]] = tick

    # 4. Perform Data Analysis
    df = pd.DataFrame(records)
    
    print("\n" + "="*50)
    print("      INVENTORY SIMULATION STUDY RESULTS        ")
    print("="*50)
    
    # 1. Exposure Distribution
    print("1. DISTRIBUTION OF INVENTORY EXPOSURE:")
    print(f"   Min Exposure    : {df['exposure'].min():.2f} USD")
    print(f"   25th Percentile : {df['exposure'].quantile(0.25):.2f} USD")
    print(f"   Median (50th)   : {df['exposure'].median():.2f} USD")
    print(f"   75th Percentile : {df['exposure'].quantile(0.75):.2f} USD")
    print(f"   Max Exposure    : {df['exposure'].max():.2f} USD")
    print(f"   Mean Exposure   : {df['exposure'].mean():.2f} USD")
    
    # 2. Concentration Distribution
    print("\n2. DISTRIBUTION OF CONCENTRATION RATIO:")
    print(f"   Min Conc        : {df['concentration'].min()*100:.1f}%")
    print(f"   25th Percentile : {df['concentration'].quantile(0.25)*100:.1f}%")
    print(f"   Median (50th)   : {df['concentration'].median()*100:.1f}%")
    print(f"   75th Percentile : {df['concentration'].quantile(0.75)*100:.1f}%")
    print(f"   Mean Conc       : {df['concentration'].mean()*100:.1f}%")
    
    # 3. Worst Concentration Observed
    worst_conc = df['concentration'].max()
    print(f"\n3. WORST CONCENTRATION OBSERVED: {worst_conc*100:.2f}%")
    
    # 4. Threshold Exceedances
    over_40 = (df['concentration'] > 0.40).sum()
    over_60 = (df['concentration'] > 0.60).sum()
    over_80 = (df['concentration'] > 0.80).sum()
    
    print("\n4. THRESHOLD EXCEEDANCE COUNT (Out of 1000 ticks):")
    print(f"   Concentration > 40% : {over_40} times ({over_40/10:.1f}% of time)")
    print(f"   Concentration > 60% : {over_60} times ({over_60/10:.1f}% of time)")
    print(f"   Concentration > 80% : {over_80} times ({over_80/10:.1f}% of time)")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(run_study())
