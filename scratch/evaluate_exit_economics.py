import asyncio
import random
import json
import collections
import time
import numpy as np

# Force configuration
import app.config
app.config.MAX_SPREAD = 0.20
app.config.ZOMBIE_MAX_SPREAD = 0.20
app.config.MIN_RR_RATIO = 0.45
app.config.MARKET_COOLDOWN = 0
app.config.COOLDOWN_DURATION = 0
app.config.MOMENTUM_THRESHOLD = 0.015

from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from app.paper_validation_tracker import PaperValidationTracker

class SimulatedMarket:
    def __init__(self, token_id, seed_prices):
        self.token_id = token_id
        self.mid = seed_prices[token_id]
        self.spread_pct = random.uniform(0.015, 0.035)
        
    def step(self):
        # Random walk for mid price
        self.mid += random.normalvariate(0, 0.006)
        self.mid = max(0.10, min(0.90, self.mid))
        
        # Calculate bid/ask
        spread_abs = self.mid * self.spread_pct
        self.best_bid = round(self.mid - spread_abs / 2.0, 4)
        self.best_ask = round(self.mid + spread_abs / 2.0, 4)
        self.last_price = round(self.mid + random.normalvariate(0, spread_abs / 4.0), 4)
        
    def get_data(self):
        spread = (self.best_ask - self.best_bid) / self.mid if self.mid > 0 else 0.02
        return {
            "token_id": self.token_id,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "last_price": self.last_price,
            "mid_price": self.mid,
            "spread": spread,
            "bid_depth": 1500.0,
            "ask_depth": 1500.0,
            "liquidity_score": 3000.0,
            "tokens": [
                {"token_id": self.token_id, "outcome": "YES", "price": str(self.mid)},
                {"token_id": f"{self.token_id}_OPP", "outcome": "NO", "price": str(1.0 - self.mid)}
            ]
        }

async def run_simulation(use_new_exit_engine=False, seed=42):
    random.seed(seed)
    
    # 1. Setup components
    pm = PositionManager()
    pm.clear_all_positions()
    
    ee = ExecutionEngine(paper_trading=True)
    tracker = PaperValidationTracker(total_capital=100.0)
    ee.set_tracker(tracker)
    
    rm = RiskManager(position_manager=pm)
    
    tokens = [f"sim_token_{i}" for i in range(5)]
    seed_prices = {t: random.uniform(0.30, 0.70) for t in tokens}
    markets = [SimulatedMarket(t, seed_prices) for t in tokens]
    
    entry_ticks = {}
    completed_cycles = 0
    round_trips = []
    
    ticks = 0
    max_ticks = 10000  # Safeguard cap
    
    # Loop until we hit 20 completed cycles
    while completed_cycles < 20 and ticks < max_ticks:
        ticks += 1
        
        # A. Step markets and build market_data_map
        market_data_map = {}
        for m in markets:
            m.step()
            mdata = m.get_data()
            market_data_map[m.token_id] = mdata
            
        # B. Check fills
        fills = ee.check_resting_fills(market_data_map)
        for fill in fills:
            tok_id = fill["token_id"]
            pnl = pm.update_position(
                token_id=tok_id,
                size=fill["fill_size"],
                price=fill["fill_price"],
                side=fill["side"],
                strategy="PMM",
                spread=fill.get("spread", 0.0),
                slippage=0.0
            )
            rm.update_after_trade(success=True, token_id=tok_id, pnl=pnl)
            
            if fill["side"] == "BUY":
                entry_ticks[tok_id] = ticks
            elif fill["side"] == "SELL":
                completed_cycles += 1
                hold_time_ticks = ticks - entry_ticks.get(tok_id, ticks)
                round_trips.append({
                    "token_id": tok_id,
                    "pnl": pnl,
                    "hold_ticks": hold_time_ticks,
                    "entry_price": pm.trade_history[-2]["price"] if len(pm.trade_history) >= 2 else 0.5,
                    "exit_price": fill["fill_price"],
                    "spread_captured": fill["fill_price"] - (pm.trade_history[-2]["price"] if len(pm.trade_history) >= 2 else 0.5)
                })
                
        # C. Update telemetry
        tracker.update_telemetry(list(market_data_map.values()))
        tracker.process_rejections_tick(market_data_map)
        
        # D. Exit Engine
        exits = pm.get_positions_for_exit(market_data_map)
        for exit_cand in exits:
            tok_id = exit_cand["token_id"]
            quoted_price = exit_cand["price"]
            
            # If NEW Exit Engine, we override PositionManager exit price to be inside spread
            if use_new_exit_engine:
                mdata = market_data_map[tok_id]
                best_bid = mdata["best_bid"]
                best_ask = mdata["best_ask"]
                epsilon = 0.0005
                if best_ask - best_bid <= 0.0010:
                    epsilon = 0.0001
                quoted_price = max(best_bid, best_ask - epsilon)
                
            pending_sells = [o for o in ee.pending_orders if o["token_id"] == tok_id and o["side"] == "SELL"]
            if pending_sells:
                if use_new_exit_engine:
                    # Dynamic Repricing Check
                    o = pending_sells[0]
                    if abs(o["price"] - quoted_price) >= 1e-4:
                        await ee.cancel_order(o["order_id"])
                        tracker.record_cancellation(o["order_id"])
                    else:
                        continue
                else:
                    # OLD Exit Engine behavior: skip placing new order, let the stale one rest
                    continue
                    
            try:
                await ee.place_limit_order(
                    token_id=tok_id,
                    price=quoted_price,
                    size=exit_cand["size"],
                    side="SELL",
                    market_data=market_data_map.get(tok_id),
                    is_aggressive=True,
                    is_emergency=False,
                    signal_delta=0,
                    signal_spread=exit_cand.get("spread", 0)
                )
            except ValueError:
                pass
                
        # E. Placement of BUY orders
        for t in tokens:
            if t not in pm.positions and not any(o["token_id"] == t for o in ee.pending_orders):
                mdata = market_data_map[t]
                price = mdata["mid_price"]
                size = 5.0 / price
                spread = mdata["spread"]
                delta_pct = spread + random.uniform(0.03, 0.06)
                delta = round(delta_pct * price, 4)
                
                signal = {
                    "token_id": t,
                    "side": "BUY",
                    "price": price,
                    "size": size,
                    "score": 0.80,
                    "delta": delta,
                    "spread": spread
                }
                
                if rm.validate_trade(signal, mdata):
                    try:
                        await ee.place_limit_order(
                            token_id=t,
                            price=price,
                            size=size,
                            side="BUY",
                            market_data=mdata
                        )
                    except ValueError:
                        pass

    # Post-process results
    total_wins = sum(r["pnl"] for r in round_trips if r["pnl"] > 0)
    total_losses = sum(r["pnl"] for r in round_trips if r["pnl"] < 0)
    profit_factor = abs(total_wins / total_losses) if total_losses != 0 else float('inf')
    
    avg_hold_ticks = np.mean([r["hold_ticks"] for r in round_trips]) if round_trips else 0.0
    # Polling interval is 10s. 1 day = 8640 ticks.
    avg_hold_time_hours = (avg_hold_ticks * 10.0) / 3600.0
    
    days_elapsed = (ticks * 10.0) / 86400.0
    completed_cycles_per_day = completed_cycles / days_elapsed if days_elapsed > 0 else 0.0
    
    total_exit_volume = sum(r["exit_price"] * 27.97 for r in round_trips) # 27.97 is size
    capital_turnover_rate = total_exit_volume / 100.0
    
    ev_per_cycle = pm.realized_pnl / completed_cycles if completed_cycles > 0 else 0.0
    ev_per_day = completed_cycles_per_day * ev_per_cycle
    
    avg_spread_captured = np.mean([r["spread_captured"] for r in round_trips]) if round_trips else 0.0
    
    # Capital recycle speed: how quickly does the base recycle?
    # Estimated monthly profit
    monthly_profit = completed_cycles_per_day * ev_per_cycle * 30
    
    return {
        "avg_spread_captured": avg_spread_captured,
        "avg_holding_time_hours": avg_hold_time_hours,
        "completed_cycles_per_day": completed_cycles_per_day,
        "capital_turnover_rate": capital_turnover_rate,
        "ev_per_cycle": ev_per_cycle,
        "ev_per_day": ev_per_day,
        "realized_pnl": pm.realized_pnl,
        "profit_factor": profit_factor,
        "monthly_profit": monthly_profit,
        "ticks": ticks,
        "completed_cycles": completed_cycles,
        "fill_rate": (tracker.total_fills / tracker.total_placed * 100) if tracker.total_placed > 0 else 0.0,
        "max_drawdown": tracker.max_drawdown
    }

async def run_and_report():
    print("Running simulation for OLD EXIT ENGINE...")
    old_res = await run_simulation(use_new_exit_engine=False, seed=42)
    
    print("Running simulation for NEW EXIT ENGINE...")
    new_res = await run_simulation(use_new_exit_engine=True, seed=42)
    
    comparison = {
        "old": old_res,
        "new": new_res
    }
    
    with open("storage/exit_engine_economic_comparison.json", "w") as fh:
        json.dump(comparison, fh, indent=2)
        
    print("\n" + "="*80)
    print("         EXIT ENGINE UPGRADE: ECONOMIC & PERFORMANCE COMPARISON")
    print("="*80)
    print(f"{'Metric':<35} | {'OLD EXIT ENGINE':<18} | {'NEW EXIT ENGINE':<18}")
    print("-" * 80)
    print(f"{'1. Avg Spread Captured ($)':<35} | {old_res['avg_spread_captured']:<18.4f} | {new_res['avg_spread_captured']:<18.4f}")
    print(f"{'2. Avg Holding Time (Hours)':<35} | {old_res['avg_holding_time_hours']:<18.2f} | {new_res['avg_holding_time_hours']:<18.2f}")
    print(f"{'3. Completed Cycles per Day':<35} | {old_res['completed_cycles_per_day']:<18.2f} | {new_res['completed_cycles_per_day']:<18.2f}")
    print(f"{'4. Capital Turnover Rate':<35} | {old_res['capital_turnover_rate']:<18.2f} | {new_res['capital_turnover_rate']:<18.2f}")
    print(f"{'5. EV per Cycle ($)':<35} | {old_res['ev_per_cycle']:<18.4f} | {new_res['ev_per_cycle']:<18.4f}")
    print(f"{'6. EV per Day ($)':<35} | {old_res['ev_per_day']:<18.4f} | {new_res['ev_per_day']:<18.4f}")
    print(f"{'7. Realized PnL ($)':<35} | {old_res['realized_pnl']:<18.4f} | {new_res['realized_pnl']:<18.4f}")
    print(f"{'8. Profit Factor':<35} | {old_res['profit_factor']:<18.4f} | {new_res['profit_factor']:<18.4f}")
    print(f"{'9. Fill Rate (%)':<35} | {old_res['fill_rate']:<18.2f} | {new_res['fill_rate']:<18.2f}")
    print(f"{'10. Max Drawdown ($)':<35} | {old_res['max_drawdown']:<18.4f} | {new_res['max_drawdown']:<18.4f}")
    print("-" * 80)
    print(f"{'11. ESTIMATED MONTHLY PROFIT ($)':<35} | ${old_res['monthly_profit']:<17.2f} | ${new_res['monthly_profit']:<17.2f}")
    print("="*80)
    
if __name__ == "__main__":
    asyncio.run(run_and_report())
