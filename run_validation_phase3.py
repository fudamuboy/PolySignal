import asyncio
import random
import sqlite3
import time
import json
import copy

# Monkey-patch config first to define runs
import app.config

def set_config(max_spread, zombie_max_spread, min_rr_ratio):
    import app.config
    import app.risk_manager
    
    # Update config module
    app.config.MAX_SPREAD = max_spread
    app.config.ZOMBIE_MAX_SPREAD = zombie_max_spread
    app.config.MIN_RR_RATIO = min_rr_ratio
    app.config.MARKET_COOLDOWN = 0
    app.config.COOLDOWN_DURATION = 0
    
    # Also inject directly into imported namespace of risk_manager to bypass direct imports
    app.risk_manager.MAX_SPREAD = max_spread
    app.risk_manager.ZOMBIE_MAX_SPREAD = zombie_max_spread
    app.risk_manager.MIN_RR_RATIO = min_rr_ratio
    app.risk_manager.MARKET_COOLDOWN = 0
    app.risk_manager.COOLDOWN_DURATION = 0

from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from app.paper_validation_tracker import PaperValidationTracker
from app.logger import logger

def clear_db():
    pm = PositionManager()
    pm.clear_all_positions()
    conn = sqlite3.connect("storage/database.db")
    conn.execute("DELETE FROM trades;")
    conn.commit()
    conn.close()

async def run_simulation(max_spread, zombie_max_spread, min_rr_ratio, seed=42):
    # Set seed for identical price path and signal generation
    random.seed(seed)
    
    # Apply configurations
    set_config(max_spread, zombie_max_spread, min_rr_ratio)
    clear_db()
    
    pm = PositionManager()
    ee = ExecutionEngine(paper_trading=True)
    tracker = PaperValidationTracker(total_capital=1000.0)
    ee.set_tracker(tracker)
    rm = RiskManager(position_manager=pm)
    
    tokens = [f"sim_token_{i}" for i in range(5)]
    prices = {t: 0.50 for t in tokens}
    
    entry_times = {}
    holding_times = []
    round_trips = []
    
    signals_evaluated = 0
    signals_accepted = 0
    
    peak_equity = 1000.0
    max_drawdown = 0.0
    
    wins_count = 0
    losses_count = 0
    completed_trips = 0
    
    # Run 1000 simulation steps
    for tick in range(1000):
        # 1. Update prices and generate fluctuating spreads
        market_data_map = {}
        enriched_tokens = []
        for t in tokens:
            # Volatile price steps (random walk)
            prices[t] += random.normalvariate(0, 0.02)
            prices[t] = max(0.10, min(0.90, prices[t]))
            
            # Fluctuating spreads (varies between 2% and 25%) to trigger the spread filters
            # Sometimes narrow, sometimes wide
            spread_abs = random.uniform(0.01, 0.12)
            best_bid = round(prices[t] - spread_abs/2, 4)
            best_ask = round(prices[t] + spread_abs/2, 4)
            mid_price = round(prices[t], 4)
            spread = round(spread_abs / mid_price, 4)
            
            mdata = {
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
            market_data_map[t] = mdata
            enriched_tokens.append(mdata)
            
        # 2. Check limit fills
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
                slippage=fill.get("slippage", 0.0)
            )
            rm.update_after_trade(success=True, token_id=tok_id, pnl=pnl)
            
            if fill["side"] == "BUY":
                entry_times[tok_id] = tick
            elif fill["side"] == "SELL":
                completed_trips += 1
                hold_time = tick - entry_times.get(tok_id, tick)
                holding_times.append(hold_time)
                
                outcome = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT"
                if pnl > 0: wins_count += 1
                elif pnl < 0: losses_count += 1
                
                round_trips.append({
                    "token_id": tok_id,
                    "pnl": pnl,
                    "outcome": outcome,
                    "hold_time": hold_time
                })
                
        # 3. Track telemetry
        tracker.update_telemetry(enriched_tokens)
        tracker.process_rejections_tick(market_data_map)
        
        # 4. Check Exit Engine
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
                        slippage=res.get("slippage", 0)
                    )
                    rm.update_after_trade(success=True, token_id=tok_id, pnl=pnl)
                    
                    hold_time = tick - entry_times.get(tok_id, tick)
                    holding_times.append(hold_time)
                    
                    outcome = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT"
                    if pnl > 0: wins_count += 1
                    elif pnl < 0: losses_count += 1
                    
                    round_trips.append({
                        "token_id": tok_id,
                        "pnl": pnl,
                        "outcome": outcome,
                        "hold_time": hold_time
                    })
            except ValueError:
                pass
                
        # 5. Propose limit buys to test evaluation/acceptance
        current_prices = {tid: pos['avg_price'] for tid, pos in pm.positions.items()}
        total_exposure = sum(pos['size'] * current_prices.get(tid, pos['avg_price']) for tid, pos in pm.positions.items())
        
        for t in tokens:
            if t not in pm.positions and not any(o["token_id"] == t for o in ee.pending_orders):
                price = prices[t]
                size = 5.0 / price
                
                # Propose a signal with a positive EV edge above the spread
                spread = market_data_map[t]["spread"]
                # Required edge in RiskManager is roughly spread + 0.0050.
                # We add a premium of 3% to 6% so that delta_pct is always positive EV
                delta_pct = spread + random.uniform(0.03, 0.06)
                delta = round(delta_pct * price, 4)
                score = random.uniform(0.75, 0.95)
                
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
                
                # Evaluate using Risk Manager
                if rm.validate_trade(signal, market_data_map[t]):
                    signals_accepted += 1
                    try:
                        await ee.place_limit_order(
                            token_id=t,
                            price=price,
                            size=size,
                            side="BUY",
                            market_data=market_data_map[t]
                        )
                    except ValueError:
                        pass
                        
        # 6. Calculate Drawdown
        # Current equity = cash + market value of open positions
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
            
    # Compute metrics
    acceptance_rate = (signals_accepted / signals_evaluated * 100) if signals_evaluated > 0 else 0.0
    win_rate = (wins_count / completed_trips * 100) if completed_trips > 0 else 0.0
    avg_win = sum(r["pnl"] for r in round_trips if r["pnl"] > 0) / wins_count if wins_count > 0 else 0.0
    avg_loss = sum(r["pnl"] for r in round_trips if r["pnl"] < 0) / losses_count if losses_count > 0 else 0.0
    
    total_wins = sum(r["pnl"] for r in round_trips if r["pnl"] > 0)
    total_losses = sum(r["pnl"] for r in round_trips if r["pnl"] < 0)
    profit_factor = abs(total_wins / total_losses) if total_losses != 0 else float('inf')
    ev_per_trade = pm.realized_pnl / completed_trips if completed_trips > 0 else 0.0
    
    return {
        "signals_evaluated": signals_evaluated,
        "signals_accepted": signals_accepted,
        "acceptance_rate": acceptance_rate,
        "round_trips": completed_trips,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "ev_per_trade": ev_per_trade,
        "realized_pnl": pm.realized_pnl,
        "max_drawdown": max_drawdown
    }

async def main():
    print("Starting Comparative Validation Run (BEFORE vs AFTER)...")
    
    # Run BEFORE calibration (seed=123 for reproducibility)
    before_metrics = await run_simulation(0.10, 0.15, 0.50, seed=123)
    
    # Run AFTER calibration (identical seed=123)
    after_metrics = await run_simulation(0.20, 0.20, 0.45, seed=123)
    
    # Print comparison
    comparison = {
        "before": before_metrics,
        "after": after_metrics
    }
    
    with open("storage/validation_phase3_comparison.json", "w") as fh:
        json.dump(comparison, fh, indent=2)
        
    print("\n==================================================")
    print("PHASE 3 RECALIBRATION COMPARISON AUDIT")
    print("==================================================")
    print(f"{'Metric':<30} | {'BEFORE (10% Sp)':<15} | {'AFTER (20% Sp)':<15}")
    print("-" * 66)
    print(f"{'1. Signals evaluated':<30} | {before_metrics['signals_evaluated']:<15} | {after_metrics['signals_evaluated']:<15}")
    print(f"{'2. Signals accepted':<30} | {before_metrics['signals_accepted']:<15} | {after_metrics['signals_accepted']:<15}")
    print(f"{'3. Acceptance rate':<30} | {before_metrics['acceptance_rate']:0.2f}% | {after_metrics['acceptance_rate']:0.2f}%")
    print(f"{'4. Round trips completed':<30} | {before_metrics['round_trips']:<15} | {after_metrics['round_trips']:<15}")
    print(f"{'5. Win rate':<30} | {before_metrics['win_rate']:0.2f}% | {after_metrics['win_rate']:0.2f}%")
    print(f"{'6. Average win':<30} | ${before_metrics['avg_win']:0.4f} | ${after_metrics['avg_win']:0.4f}")
    print(f"{'7. Average loss':<30} | ${before_metrics['avg_loss']:0.4f} | ${after_metrics['avg_loss']:0.4f}")
    print(f"{'8. Profit factor':<30} | {before_metrics['profit_factor']:0.4f} | {after_metrics['profit_factor']:0.4f}")
    print(f"{'9. EV per trade':<30} | ${before_metrics['ev_per_trade']:+0.4f} | ${after_metrics['ev_per_trade']:+0.4f}")
    print(f"{'10. Realized PnL':<30} | ${before_metrics['realized_pnl']:+0.4f} | ${after_metrics['realized_pnl']:+0.4f}")
    print(f"{'11. Maximum drawdown':<30} | ${before_metrics['max_drawdown']:0.4f} | ${after_metrics['max_drawdown']:0.4f}")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
