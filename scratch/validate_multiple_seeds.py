import asyncio
import random
import sqlite3
import json
import copy

# Force environment config settings
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

def clear_db():
    pm = PositionManager()
    pm.clear_all_positions()
    conn = sqlite3.connect("storage/database.db")
    conn.execute("DELETE FROM trades;")
    conn.commit()
    conn.close()

async def run_simulation(seed=42, enable_obi=False):
    random.seed(seed)
    clear_db()
    
    pm = PositionManager()
    ee = ExecutionEngine(paper_trading=True)
    tracker = PaperValidationTracker(total_capital=100.0)
    ee.set_tracker(tracker)
    rm = RiskManager(position_manager=pm)
    
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
    
    peak_equity = 100.0
    max_drawdown = 0.0
    
    for tick in range(1000):
        # Update prices
        market_data_map = {}
        enriched_tokens = []
        for t in tokens:
            prices[t] += random.normalvariate(0, 0.015)
            prices[t] = max(0.10, min(0.90, prices[t]))
            
            # Full-universe scanner spread profile: tight spreads (0.203% avg)
            spread_abs = random.uniform(0.0005, 0.0045)
            best_bid = round(prices[t] - spread_abs/2, 4)
            best_ask = round(prices[t] + spread_abs/2, 4)
            mid_price = round(prices[t], 4)
            spread = round(spread_abs / mid_price, 4)
            
            # Dynamically model order book imbalance (OBI)
            # Total depth is held stable at 3000.0 (Liquidity constant)
            total_depth = 3000.0
            obi_sim = random.normalvariate(0.0, 0.40)
            obi_sim = max(-0.95, min(0.95, obi_sim))
            
            bid_depth = total_depth * (1 + obi_sim) / 2
            ask_depth = total_depth * (1 - obi_sim) / 2
            
            mdata = {
                "token_id": t,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "last_price": mid_price,
                "mid_price": mid_price,
                "spread": spread,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "liquidity_score": total_depth
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
                slippage=0.0
            )
            rm.update_after_trade(success=True, token_id=tok_id, pnl=pnl)
            
            if fill["side"] == "BUY":
                entry_times[tok_id] = tick
            elif fill["side"] == "SELL":
                completed_trips += 1
                hold_time = tick - entry_times.get(tok_id, tick)
                holding_times.append(hold_time)
                
                if pnl > 0: wins_count += 1
                elif pnl < 0: losses_count += 1
                
                round_trips.append({
                    "token_id": tok_id,
                    "pnl": pnl,
                    "hold_time": hold_time
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
                        "hold_time": hold_time
                    })
            except ValueError:
                pass
                
        for t in tokens:
            if t not in pm.positions and not any(o["token_id"] == t for o in ee.pending_orders):
                price = prices[t]
                size = 5.0 / price
                spread = market_data_map[t]["spread"]
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
                
                # Mock OBI value to pass validation or bypass inside simulated risk checks
                # If OBI filter is NOT enabled, we temporarily bypass the OBI check by overriding
                # market depths in the signal verification or mocking RiskManager OBI checks.
                # To be perfectly correct, we can temporarily monkey-patch or toggle OBI bypass:
                signal_data = copy.deepcopy(market_data_map[t])
                if not enable_obi:
                    # Bypasses OBI gate by setting bid_depth high so OBI is always >= +0.20
                    signal_data["bid_depth"] = 2000.0
                    signal_data["ask_depth"] = 1000.0
                
                if rm.validate_trade(signal, signal_data):
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
            
    win_rate = (wins_count / completed_trips * 100) if completed_trips > 0 else 0.0
    ev_per_trade = pm.realized_pnl / completed_trips if completed_trips > 0 else 0.0
    
    total_wins = sum(r["pnl"] for r in round_trips if r["pnl"] > 0)
    total_losses = sum(r["pnl"] for r in round_trips if r["pnl"] < 0)
    profit_factor = abs(total_wins / total_losses) if total_losses != 0 else float('inf')
    
    acceptance_rate = (signals_accepted / signals_evaluated * 100) if signals_evaluated > 0 else 0.0
    
    return {
        "completed_trips": completed_trips,
        "win_rate": win_rate,
        "ev_per_trade": ev_per_trade,
        "realized_pnl": pm.realized_pnl,
        "max_drawdown": max_drawdown,
        "profit_factor": profit_factor,
        "acceptance_rate": acceptance_rate
    }

async def main():
    print("Executing Comparative PMM vs PMM + OBI Filter Validation...")
    seeds = [100, 200, 300, 400, 500]
    
    baseline_results = {}
    obi_results = {}
    
    # 1. Run Baseline (no OBI check)
    print("\n[1/2] Simulating Baseline PMM (No OBI filter)...")
    for s in seeds:
        print(f"  Running Baseline seed={s}...")
        baseline_results[s] = await run_simulation(seed=s, enable_obi=False)
        
    # 2. Run PMM + OBI Filter (OBI >= +0.20)
    print("\n[2/2] Simulating PMM + OBI (OBI >= +0.20 filter)...")
    for s in seeds:
        print(f"  Running OBI seed={s}...")
        obi_results[s] = await run_simulation(seed=s, enable_obi=True)
        
    # Save comparison data
    comparison_data = {
        "baseline": baseline_results,
        "obi": obi_results
    }
    with open("storage/validation_obi_comparison.json", "w") as fh:
        json.dump(comparison_data, fh, indent=2)
        
    print("\n" + "="*95)
    print("                    PMM SCANNER VS PMM + OBI FILTER PERFORMANCE REPORT")
    print("="*95)
    print(f"{'Seed':<5} | {'Metric':<22} | {'A. Baseline PMM':<18} | {'B. PMM + OBI Filter':<18} | {'Delta / Impact':<15}")
    print("-" * 95)
    
    metrics_to_print = [
        ("completed_trips", "Completed Trades", "{:0.0f}", "{:+0.0f}"),
        ("win_rate", "Win Rate (%)", "{:0.2f}%", "{:+0.2f}%"),
        ("ev_per_trade", "EV per Trade ($)", "{:+0.4f}", "{:+0.4f}"),
        ("realized_pnl", "Realized PnL ($)", "{:+0.4f}", "{:+0.4f}"),
        ("profit_factor", "Profit Factor", "{:0.4f}", "{:+0.4f}"),
        ("max_drawdown", "Max Drawdown ($)", "{:0.4f}", "{:+0.4f}"),
        ("acceptance_rate", "Acceptance Rate (%)", "{:0.2f}%", "{:+0.2f}%")
    ]
    
    # Track overall average differences
    avg_diffs = {}
    
    for s in seeds:
        print(f"Seed {s}:")
        for key, name, fmt, delta_fmt in metrics_to_print:
            base_val = baseline_results[s][key]
            obi_val = obi_results[s][key]
            delta = obi_val - base_val
            
            base_str = fmt.format(base_val)
            obi_str = fmt.format(obi_val)
            delta_str = delta_fmt.format(delta)
            
            print(f"      | {name:<22} | {base_str:<18} | {obi_str:<18} | {delta_str:<15}")
        print("-" * 95)
        
    # Averages
    print("AVERAGES ACROSS 5 SEEDS:")
    for key, name, fmt, delta_fmt in metrics_to_print:
        avg_base = sum(baseline_results[s][key] for s in seeds) / len(seeds)
        avg_obi = sum(obi_results[s][key] for s in seeds) / len(seeds)
        avg_delta = avg_obi - avg_base
        
        base_str = fmt.format(avg_base)
        obi_str = fmt.format(avg_obi)
        delta_str = delta_fmt.format(avg_delta)
        
        print(f"      | {name:<22} | {base_str:<18} | {obi_str:<18} | {delta_str:<15}")
    print("="*95)

if __name__ == "__main__":
    asyncio.run(main())
