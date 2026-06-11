import asyncio
import random
import sqlite3
import json
import copy
import time
import sys
import os

# Set PYTHONPATH and force configuration settings
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.config

# Standard validation configurations
app.config.MAX_SPREAD = 0.20
app.config.ZOMBIE_MAX_SPREAD = 0.20
app.config.MIN_RR_RATIO = 0.45
app.config.MARKET_COOLDOWN = 0
app.config.COOLDOWN_DURATION = 0
app.config.POLLING_INTERVAL = 0
app.config.ESTIMATED_FEE_BPS = 20
app.config.EV_SAFETY_MARGIN = 0.001
app.config.MIN_DELTA_FOR_TRADE = 0.020

from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from app.paper_validation_tracker import PaperValidationTracker
from app.database import Database
from app.logger import logger

# Redirect all database calls to a single shared in-memory database for 100x performance
_shared_db = sqlite3.connect(":memory:")
Database._get_connection = lambda self: _shared_db

def clear_db():
    pm = PositionManager()
    pm.clear_all_positions()

# Old EV math monkey-patch function
def evaluate_ev_old(self, signal, market_data, is_fallback=False):
    token_id = signal.get('token_id')
    spread = market_data.get('spread', 1.0)
    delta = abs(signal.get('delta', 0))
    price = float(signal.get('price', 1.0))
    
    delta_pct = delta / price if price > 0 else 0
    fee_pct = 20 / 10000  # ESTIMATED_FEE_BPS = 20
    margin = 0.001  # EV_SAFETY_MARGIN = 0.001
    
    # OLD behavior: ALWAYS subtract spread cost
    required_edge = spread + (fee_pct * 2) + margin
    expected_profit = delta_pct - required_edge
    return expected_profit > 0 or signal.get("test_mode") or is_fallback


async def run_simulation(use_new_ev, pre_generated_data, seed=123):
    random.seed(seed)
    clear_db()
    
    # Temporarily set log level to ERROR to bypass disk writes and run 100x faster
    import logging
    old_log_level = logger.level
    logger.setLevel(logging.ERROR)
    
    pm = PositionManager()
    ee = ExecutionEngine(paper_trading=True)
    
    # Capital = $500 USD as approved by the user
    tracker = PaperValidationTracker(total_capital=500.0)
    ee.set_tracker(tracker)
    
    rm = RiskManager(position_manager=pm)
    
    if not use_new_ev:
        # Monkey patch back to the OLD EV logic
        import types
        rm.evaluate_ev = types.MethodType(evaluate_ev_old, rm)
        
    entry_times = {}
    holding_times = []
    round_trips = []
    
    wins_count = 0
    losses_count = 0
    completed_trips = 0
    
    signals_evaluated = 0
    signals_accepted = 0
    
    total_spread_at_entry = 0.0
    entry_spread_count = 0
    
    peak_equity = 500.0
    max_drawdown = 0.0
    
    tick = 0
    max_ticks = len(pre_generated_data)
    
    while tick < max_ticks:
        # Stop check: Collect at least 250 round trips, 100+ wins, and 100+ losses
        if completed_trips >= 250 and wins_count >= 100 and losses_count >= 100:
            break
            
        # Retrieve pre-generated identical market data snapshot for this tick
        market_data_map = pre_generated_data[tick]
        enriched_tokens = list(market_data_map.values())
        
        # 1. Check resting limit fills
        fills = ee.check_resting_fills(market_data_map)
        for fill in fills:
            tok_id = fill["token_id"]
            pnl = pm.update_position(
                token_id=tok_id,
                size=fill["fill_size"],
                price=fill["fill_price"],
                side=fill["side"],
                strategy=fill.get("strategy", "PMM"),
                spread=fill.get("spread", 0.0),
                slippage=0.0
            )
            rm.update_after_trade(success=True, token_id=tok_id, pnl=pnl)
            
            if fill["side"] == "BUY":
                entry_times[tok_id] = tick
                total_spread_at_entry += fill.get("spread", 0.0)
                entry_spread_count += 1
            elif fill["side"] == "SELL":
                completed_trips += 1
                hold_time = tick - entry_times.get(tok_id, tick)
                holding_times.append(hold_time)
                
                if pnl > 0: wins_count += 1
                elif pnl < 0: losses_count += 1
                
                round_trips.append({
                    "token_id": tok_id,
                    "pnl": pnl,
                    "hold_time": hold_time,
                    "spread": fill.get("spread", 0.0)
                })
                
        tracker.update_telemetry(enriched_tokens)
        tracker.process_rejections_tick(market_data_map)
        
        # 2. Check Exit Engine
        exits = pm.get_positions_for_exit(market_data_map)
        for exit_cand in exits:
            tok_id = exit_cand["token_id"]
            is_emergency = "SL" in exit_cand["reason"]
            quoted_price = exit_cand["price"]
            
            pending_sells = [o for o in ee.pending_orders if o["token_id"] == tok_id and o["side"] == "SELL"]
            if pending_sells:
                if is_emergency:
                    for o in pending_sells:
                        ee.pending_orders.remove(o)
                        tracker.record_cancellation(o["order_id"])
                else:
                    continue
                    
            try:
                res = await ee.place_limit_order(
                    token_id=tok_id,
                    price=quoted_price,
                    size=exit_cand["size"],
                    side="SELL",
                    market_data=market_data_map.get(tok_id),
                    is_aggressive=True,
                    is_emergency=is_emergency,
                    signal_delta=0,
                    signal_spread=exit_cand.get("spread", 0)
                )
                if res['status'] == "SUCCESS":
                    completed_trips += 1
                    pnl = pm.update_position(
                        token_id=tok_id,
                        size=res.get('fill_size', exit_cand["size"]),
                        price=res.get('fill_price', quoted_price),
                        side="SELL",
                        strategy="EXIT_" + exit_cand["reason"].split()[0],
                        spread=exit_cand.get("spread", 0),
                        slippage=0.0
                    )
                    rm.update_after_trade(success=True, token_id=tok_id, pnl=pnl)
                    
                    hold_time = tick - entry_times.get(tok_id, tick)
                    holding_times.append(hold_time)
                    
                    if pnl > 0: wins_count += 1
                    elif pnl < 0: losses_count += 1
                    
                    round_trips.append({
                        "token_id": tok_id,
                        "pnl": pnl,
                        "hold_time": hold_time,
                        "spread": exit_cand.get("spread", 0)
                    })
            except ValueError:
                pass
                
        # 3. Propose Limit BUYs
        for t in market_data_map.keys():
            if t not in pm.positions and not any(o["token_id"] == t for o in ee.pending_orders):
                mdata = market_data_map[t]
                price = mdata["mid_price"]
                size = 5.0 / price
                spread = mdata["spread"]
                
                # Signal delta pct is generated based on identical seed offsets
                # Random offsets are drawn from pre-generated data sequence index
                # to guarantee 100% deterministic identical signals for both runs.
                state_rand = random.Random(seed + tick + hash(t))
                delta_pct = state_rand.uniform(0.015, 0.08)
                delta = round(delta_pct * price, 4)
                score = state_rand.uniform(65.0, 95.0)
                
                signal = {
                    "token_id": t,
                    "side": "BUY",
                    "price": price,
                    "size": size,
                    "score": score,
                    "delta": delta,
                    "spread": spread
                }
                
                signals_evaluated += 1
                
                if rm.validate_trade(signal, mdata):
                    signals_accepted += 1
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
                        
        # 4. Calculate Drawdown
        open_val = 0.0
        for tid, pos in pm.positions.items():
            mid = market_data_map[tid]["mid_price"]
            open_val += pos["size"] * mid
            
        current_equity = tracker.virtual_cash + open_val
        if current_equity > peak_equity:
            peak_equity = current_equity
        dd = peak_equity - current_equity
        if dd > max_drawdown:
            max_drawdown = dd
            
        # Reset daily loss limit every 100 ticks (simulating daily rollover)
        if tick > 0 and tick % 100 == 0:
            rm.daily_total_loss = 0.0
            
        tick += 1
        
    win_rate = (wins_count / completed_trips * 100) if completed_trips > 0 else 0.0
    ev_per_trade = pm.realized_pnl / completed_trips if completed_trips > 0 else 0.0
    
    total_wins = sum(r["pnl"] for r in round_trips if r["pnl"] > 0)
    total_losses = sum(r["pnl"] for r in round_trips if r["pnl"] < 0)
    profit_factor = abs(total_wins / total_losses) if total_losses != 0 else float('inf')
    
    acceptance_rate = (signals_accepted / signals_evaluated * 100) if signals_evaluated > 0 else 0.0
    avg_spread_captured = (total_spread_at_entry / entry_spread_count * 100) if entry_spread_count > 0 else 0.0
    avg_win = total_wins / wins_count if wins_count > 0 else 0.0
    avg_loss = total_losses / losses_count if losses_count > 0 else 0.0
    
    # Restore original log level
    logger.setLevel(old_log_level)
    
    return {
        "round_trips": completed_trips,
        "wins": wins_count,
        "losses": losses_count,
        "win_rate": win_rate,
        "ev_per_trade": ev_per_trade,
        "realized_pnl": pm.realized_pnl,
        "profit_factor": profit_factor,
        "acceptance_rate": acceptance_rate,
        "avg_spread_captured": avg_spread_captured,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_drawdown": max_drawdown,
        "ticks_run": tick
    }

def pre_generate_market_data(seed=123, steps=10000):
    random.seed(seed)
    tokens = [f"sim_token_{i}" for i in range(10)]
    prices = {t: 0.50 for t in tokens}
    
    pre_generated_data = []
    
    for _ in range(steps):
        market_data_map = {}
        for t in tokens:
            prices[t] += random.normalvariate(0, 0.02)
            prices[t] = max(0.10, min(0.90, prices[t]))
            
            # Simulated spreads from narrow (1%) to wide (15%)
            spread_abs = random.uniform(0.005, 0.075)
            best_bid = round(prices[t] - spread_abs/2, 4)
            best_ask = round(prices[t] + spread_abs/2, 4)
            mid_price = round(prices[t], 4)
            spread = round(spread_abs / mid_price, 4)
            
            market_data_map[t] = {
                "token_id": t,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "last_price": mid_price,
                "mid_price": mid_price,
                "spread": spread,
                "bid_depth": 350.0,
                "ask_depth": 350.0,
                "liquidity_score": 700.0
            }
        pre_generated_data.append(market_data_map)
        
    return pre_generated_data

async def main():
    print("Pre-generating deterministic market price paths (10,000 ticks)...")
    pre_generated_data = pre_generate_market_data(seed=123, steps=10000)
    
    print("\n[1/2] Running OLD VERSION (Full Spread Penalty Always Subtracted)...")
    old_metrics = await run_simulation(use_new_ev=False, pre_generated_data=pre_generated_data, seed=123)
    
    print("\n[2/2] Running NEW VERSION (Spread Penalty Removed for Passive Maker Orders)...")
    new_metrics = await run_simulation(use_new_ev=True, pre_generated_data=pre_generated_data, seed=123)
    
    print("\n" + "="*80)
    print("             PMM EV CORRECTION: MEASURED VALIDATION RESULTS")
    print("="*80)
    print(f"{'Metric':<30} | {'OLD (Always Penalized)':<22} | {'NEW (Passive Maker)':<22}")
    print("-" * 80)
    print(f"{'1. EV per Trade ($)':<30} | {old_metrics['ev_per_trade']:+0.5f} {'':<14} | {new_metrics['ev_per_trade']:+0.5f}")
    print(f"{'2. Profit Factor':<30} | {old_metrics['profit_factor']:0.5f} {'':<14} | {new_metrics['profit_factor']:0.5f}")
    print(f"{'3. Realized PnL ($)':<30} | {old_metrics['realized_pnl']:.4f} {'':<14} | {new_metrics['realized_pnl']:.4f}")
    print(f"{'4. Win Rate (%)':<30} | {old_metrics['win_rate']:0.2f}% {'':<15} | {new_metrics['win_rate']:0.2f}%")
    print(f"{'5. Acceptance Rate (%)':<30} | {old_metrics['acceptance_rate']:0.2f}% {'':<15} | {new_metrics['acceptance_rate']:0.2f}%")
    print(f"{'6. Avg Spread Captured (%)':<30} | {old_metrics['avg_spread_captured']:0.2f}% {'':<15} | {new_metrics['avg_spread_captured']:0.2f}%")
    print(f"{'7. Average Win ($)':<30} | {old_metrics['avg_win']:0.4f} {'':<14} | {new_metrics['avg_win']:0.4f}")
    print(f"{'8. Average Loss ($)':<30} | {old_metrics['avg_loss']:0.4f} {'':<14} | {new_metrics['avg_loss']:0.4f}")
    print(f"{'9. Maximum Drawdown ($)':<30} | {old_metrics['max_drawdown']:0.4f} {'':<14} | {new_metrics['max_drawdown']:0.4f}")
    print(f"{'10. Completed Round Trips':<30} | {old_metrics['round_trips']:<22} | {new_metrics['round_trips']}")
    print(f"{'11. Winning / Losing Trades':<30} | {old_metrics['wins']} W / {old_metrics['losses']} L {'':<6} | {new_metrics['wins']} W / {new_metrics['losses']} L")
    print(f"{'12. Simulation Ticks Run':<30} | {old_metrics['ticks_run']:<22} | {new_metrics['ticks_run']}")
    print("="*80)
    
    # Save comparison report
    results = {
        "old": old_metrics,
        "new": new_metrics
    }
    with open("storage/validation_ev_correction_results.json", "w") as fh:
        json.dump(results, fh, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
