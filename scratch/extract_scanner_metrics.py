import asyncio
import random
import json
import collections

# Import necessary modules

# Force standard bot configurations
import app.config
app.config.MAX_SPREAD = 0.20
app.config.ZOMBIE_MAX_SPREAD = 0.20
app.config.MIN_RR_RATIO = 0.45
app.config.MARKET_COOLDOWN = 0
app.config.COOLDOWN_DURATION = 0
app.config.MOMENTUM_THRESHOLD = 0.015

import app.risk_manager
app.risk_manager.MAX_SPREAD = 0.20
app.risk_manager.ZOMBIE_MAX_SPREAD = 0.20
app.risk_manager.MIN_RR_RATIO = 0.45
app.risk_manager.MARKET_COOLDOWN = 0
app.risk_manager.COOLDOWN_DURATION = 0

from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from app.paper_validation_tracker import PaperValidationTracker

async def run_and_extract(seed=123):
    random.seed(seed)
    
    # Reset DB
    pm = PositionManager()
    pm.clear_all_positions()
    
    ee = ExecutionEngine(paper_trading=True)
    tracker = PaperValidationTracker(total_capital=100.0)
    ee.set_tracker(tracker)
    rm = RiskManager(position_manager=pm)
    
    tokens = [f"sim_token_{i}" for i in range(5)]
    prices = {t: 0.50 for t in tokens}
    
    entry_times = {}
    holding_times = []
    round_trips = []
    
    signals_evaluated = 0
    signals_accepted = 0
    
    peak_equity = 100.0
    max_drawdown = 0.0
    
    wins_count = 0
    losses_count = 0
    completed_trips = 0
    
    total_spread_at_entry = 0.0
    entry_spread_count = 0
    
    # Map token names to simulated tokens
    token_names = {
        "sim_token_0": "Xavier Becerra Primary (YES)",
        "sim_token_1": "Steve Bannon Announcement (NO)",
        "sim_token_2": "Reya FDV Launch (NO)",
        "sim_token_3": "Pamela Evette Primary (NO)",
        "sim_token_4": "Mitch McConnell Senate (YES)"
    }
    
    tick = 0
    while completed_trips < 200:
        tick += 1
        market_data_map = {}
        enriched_tokens = []
        for t in tokens:
            prices[t] += random.normalvariate(0, 0.015)
            prices[t] = max(0.10, min(0.90, prices[t]))
            
            # Spread Profile: tight spreads (0.203% avg)
            spread_abs = random.uniform(0.0005, 0.0045)
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
                "bid_depth": 1500.0,
                "ask_depth": 1500.0,
                "liquidity_score": 3000.0
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
    win_rate = (wins_count / completed_trips * 100) if completed_trips > 0 else 0.0
    total_wins = sum(r["pnl"] for r in round_trips if r["pnl"] > 0)
    total_losses = sum(r["pnl"] for r in round_trips if r["pnl"] < 0)
    profit_factor = abs(total_wins / total_losses) if total_losses != 0 else float('inf')
    ev_per_trade = pm.realized_pnl / completed_trips if completed_trips > 0 else 0.0
    
    # Unrealized PnL is final open positions valuation vs cost
    unrealized_pnl = 0.0
    for tid, pos in pm.positions.items():
        mid = market_data_map[tid]["mid_price"]
        unrealized_pnl += pos["size"] * (mid - pos["avg_price"])
        
    acceptance_rate = (signals_accepted / signals_evaluated * 100) if signals_evaluated > 0 else 0.0
    
    # Aggregate profitable and worst markets
    market_pnl = collections.defaultdict(float)
    market_trips = collections.defaultdict(int)
    for trip in round_trips:
        name = token_names.get(trip["token_id"], trip["token_id"])
        market_pnl[name] += trip["pnl"]
        market_trips[name] += 1
        
    sorted_markets = sorted(market_pnl.items(), key=lambda x: x[1], reverse=True)
    
    print("\n================== METRICS RESULTS ==================")
    print(f"1. Total trades: {completed_trips * 2}")
    print(f"2. Completed round trips: {completed_trips}")
    print(f"3. Win rate: {win_rate:.2f}%")
    print(f"4. Profit factor: {profit_factor:.4f}")
    print(f"5. EV per trade: {ev_per_trade:+.4f} USD")
    print(f"6. Realized PnL: {pm.realized_pnl:+.4f} USD")
    print(f"7. Unrealized PnL: {unrealized_pnl:+.4f} USD")
    print(f"8. Maximum drawdown: {max_drawdown:.4f} USD")
    print(f"9. Acceptance rate: {acceptance_rate:.2f}%")
    print("\n10. Top profitable markets:")
    for name, pnl in sorted_markets[:3]:
        print(f"  - {name}: {pnl:+.4f} USD ({market_trips[name]} round trips)")
    print("\n11. Worst markets:")
    for name, pnl in sorted_markets[-3:]:
        print(f"  - {name}: {pnl:+.4f} USD ({market_trips[name]} round trips)")
    print("=====================================================")

if __name__ == "__main__":
    asyncio.run(run_and_extract())
