import asyncio
import random
import sqlite3
import time
import json

# Force environment config settings
import app.config
app.config.MAX_SPREAD = 0.20
app.config.ZOMBIE_MAX_SPREAD = 0.20
app.config.MIN_RR_RATIO = 0.45
app.config.MARKET_COOLDOWN = 0
app.config.COOLDOWN_DURATION = 0

from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from app.paper_validation_tracker import PaperValidationTracker

def clear_db():
    pm = PositionManager()
    pm.clear_all_positions()
    conn = sqlite3.connect("storage/database.db")
    conn.execute("DELETE FROM trades;")
    conn.commit()
    conn.close()

# Standard BID-based exit logic for the comparison run
def bid_based_get_positions_for_exit(self, market_map):
    exits = []
    from app.config import MAX_SPREAD, MAX_SL_ABSOLUTE, DYNAMIC_SL_FLOOR, DYNAMIC_SL_SPREAD_MULT, TP_HIGH_CONFIDENCE, TP_STRONG_TREND
    
    for token_id, pos in list(self.positions.items()):
        entry_price = pos.get('avg_price')
        size = pos.get('size')
        if entry_price is None or size is None:
            continue

        market_info = market_map.get(token_id)
        if not market_info:
            continue

        # OLD LOGIC: uses best_bid for everything
        curr_exit_price = market_info.get("best_bid") or market_info.get("last_price")
        if curr_exit_price is None:
            continue

        pos['last_known_price'] = curr_exit_price
        change = (curr_exit_price - entry_price) / entry_price

        # Stop Loss
        current_spread = market_info.get("spread", MAX_SPREAD)
        dynamic_sl = min(
            MAX_SL_ABSOLUTE,
            max(DYNAMIC_SL_FLOOR, current_spread * DYNAMIC_SL_SPREAD_MULT)
        )

        if change <= -dynamic_sl:
            exits.append({
                'token_id': token_id,
                'reason': f"SL_DYNAMIC ({change*100:.1f}% vs SL {dynamic_sl*100:.1f}%)",
                'side': 'SELL',
                'size': size,
                'price': curr_exit_price,
                'spread': current_spread
            })
            continue

        # Take Profit
        tp_target = TP_HIGH_CONFIDENCE
        strategy = pos.get('strategy', 'Unknown')
        if 'Trend' in strategy:
            tp_target = TP_STRONG_TREND
        
        if change >= tp_target:
            exits.append({
                'token_id': token_id, 
                'reason': f"TP_DYNAMIC ({change*100:.1f}% vs TP {tp_target*100:.1f}%)",
                'side': 'SELL',
                'size': size,
                'price': curr_exit_price,
                'spread': current_spread
            })
            continue
            
    return exits

async def run_validation(use_mid_based, seed=42):
    random.seed(seed)
    clear_db()
    
    pm = PositionManager()
    if not use_mid_based:
        # Monkey patch back to the old BID-based logic
        import types
        pm.get_positions_for_exit = types.MethodType(bid_based_get_positions_for_exit, pm)
        
    ee = ExecutionEngine(paper_trading=True)
    tracker = PaperValidationTracker(total_capital=1000.0)
    ee.set_tracker(tracker)
    rm = RiskManager(position_manager=pm)
    
    # 5 active simulated markets
    tokens = [f"sim_token_{i}" for i in range(5)]
    prices = {t: 0.50 for t in tokens}
    
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
    
    tick = 0
    # Stop condition: >= 20 round trips AND >= 10 TP AND >= 10 SL (with a max cap to prevent infinite loops on the old logic)
    max_ticks = 3000
    
    while tick < max_ticks:
        tick += 1
        
        # Stop check
        if use_mid_based and completed_trips >= 20 and wins_count >= 10 and losses_count >= 10:
            break
        # For old logic, since wins are almost impossible, stop once we hit 20 completed round trips or max ticks
        if not use_mid_based and completed_trips >= 20:
            break
            
        market_data_map = {}
        enriched_tokens = []
        for t in tokens:
            prices[t] += random.normalvariate(0, 0.025) # 2.5% step size
            prices[t] = max(0.10, min(0.90, prices[t]))
            
            # Fluctuating spreads to replicate Phase 3 parameters
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
                total_spread_at_entry += fill.get("spread", 0.0)
                entry_spread_count += 1
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
                    "hold_time": hold_time,
                    "sell_strategy": fill.get("strategy", "PMM")
                })
                
        tracker.update_telemetry(enriched_tokens)
        tracker.process_rejections_tick(market_data_map)
        
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
                        "hold_time": hold_time,
                        "sell_strategy": "EXIT_" + exit_cand["reason"].split()[0]
                    })
            except ValueError:
                pass
                
        current_prices = {tid: pos['avg_price'] for tid, pos in pm.positions.items()}
        total_exposure = sum(pos['size'] * current_prices.get(tid, pos['avg_price']) for tid, pos in pm.positions.items())
        
        for t in tokens:
            if t not in pm.positions and not any(o["token_id"] == t for o in ee.pending_orders):
                price = prices[t]
                size = 5.0 / price
                
                spread = market_data_map[t]["spread"]
                # Required edge + 3% to 6% to ensure positive EV
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
                        
    # Compute metrics
    win_rate = (wins_count / completed_trips * 100) if completed_trips > 0 else 0.0
    avg_win = sum(r["pnl"] for r in round_trips if r["pnl"] > 0) / wins_count if wins_count > 0 else 0.0
    avg_loss = sum(r["pnl"] for r in round_trips if r["pnl"] < 0) / losses_count if losses_count > 0 else 0.0
    
    total_wins = sum(r["pnl"] for r in round_trips if r["pnl"] > 0)
    total_losses = sum(r["pnl"] for r in round_trips if r["pnl"] < 0)
    profit_factor = abs(total_wins / total_losses) if total_losses != 0 else float('inf')
    ev_per_trade = pm.realized_pnl / completed_trips if completed_trips > 0 else 0.0
    avg_spread_at_entry = (total_spread_at_entry / entry_spread_count * 100) if entry_spread_count > 0 else 0.0
    avg_hold_time = sum(holding_times)/len(holding_times) if len(holding_times) > 0 else 0.0
    
    return {
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "ev_per_trade": ev_per_trade,
        "realized_pnl": pm.realized_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "tp_count": wins_count,
        "sl_count": losses_count,
        "completed_trips": completed_trips,
        "avg_spread": avg_spread_at_entry,
        "avg_hold_time": avg_hold_time
    }

async def main():
    print("Starting Phase A Exit Logic Validation...")
    
    print("\n[1/2] Running standard BID-based Exit Logic validation...")
    old_metrics = await run_validation(use_mid_based=False, seed=456)
    
    print("[2/2] Running new MID-based Exit Logic validation...")
    new_metrics = await run_validation(use_mid_based=True, seed=456)
    
    comparison = {
        "old": old_metrics,
        "new": new_metrics
    }
    
    with open("storage/validation_phase_a_results.json", "w") as fh:
        json.dump(comparison, fh, indent=2)
        
    print("\n==================================================")
    print("PHASE A: EXIT LOGIC VALIDATION RESULTS")
    print("==================================================")
    print(f"{'Metric':<30} | {'OLD (BID-based)':<15} | {'NEW (MID-based)':<15}")
    print("-" * 66)
    print(f"{'1. Completed round trips':<30} | {old_metrics['completed_trips']:<15} | {new_metrics['completed_trips']:<15}")
    print(f"{'2. Win rate':<30} | {old_metrics['win_rate']:0.2f}% | {new_metrics['win_rate']:0.2f}%")
    print(f"{'3. Profit factor':<30} | {old_metrics['profit_factor']:0.4f} | {new_metrics['profit_factor']:0.4f}")
    print(f"{'4. EV per trade':<30} | ${old_metrics['ev_per_trade']:+0.4f} | ${new_metrics['ev_per_trade']:+0.4f}")
    print(f"{'5. Realized PnL':<30} | ${old_metrics['realized_pnl']:+0.4f} | ${new_metrics['realized_pnl']:+0.4f}")
    print(f"{'6. Average win':<30} | ${old_metrics['avg_win']:+0.4f} | ${new_metrics['avg_win']:+0.4f}")
    print(f"{'7. Average loss':<30} | ${old_metrics['avg_loss']:+0.4f} | ${new_metrics['avg_loss']:+0.4f}")
    print(f"{'8. TP count (wins)':<30} | {old_metrics['tp_count']:<15} | {new_metrics['tp_count']:<15}")
    print(f"{'9. SL count (losses)':<30} | {old_metrics['sl_count']:<15} | {new_metrics['sl_count']:<15}")
    print(f"{'10. Average spread at entry':<30} | {old_metrics['avg_spread']:0.2f}% | {new_metrics['avg_spread']:0.2f}%")
    print(f"{'11. Average holding time (ticks)':<30} | {old_metrics['avg_hold_time']:0.2f} | {new_metrics['avg_hold_time']:0.2f}")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
