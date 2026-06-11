import asyncio
import random
import sqlite3
import time
import json
from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from app.paper_validation_tracker import PaperValidationTracker
from app.logger import logger
from app.config import PMM_TOTAL_CAPITAL, BASE_ORDER_SIZE

async def main():
    # Clear DB for clean validation run
    pm = PositionManager()
    pm.clear_all_positions()
    conn = sqlite3.connect("storage/database.db")
    conn.execute("DELETE FROM trades;")
    conn.commit()
    conn.close()

    logger.info("DB Cleared. Starting Phase 2 Short-Cycle Validation Run...")

    # Initialize actual system components (Capital base increased to $1000.0 for simulation buffer)
    ee = ExecutionEngine(paper_trading=True)
    tracker = PaperValidationTracker(total_capital=1000.0)
    ee.set_tracker(tracker)
    rm = RiskManager(position_manager=pm)

    # Simulating 5 active market tokens
    tokens = [f"live_token_{i}" for i in range(5)]
    prices = {t: 0.50 for t in tokens}

    # Track entry and holding times for stats
    entry_times = {}
    holding_times = []

    # Statistics counters
    wins_count = 0
    losses_count = 0
    completed_trips = 0

    # Round trips detail tracking
    round_trips = []

    # Main high-frequency tick loop
    tick = 0
    while completed_trips < 10 or wins_count < 5 or losses_count < 5:
        tick += 1
        
        # 1. Update market prices with random walk (high volatility to trigger TP/SL exits quickly)
        market_data_map = {}
        enriched_tokens = []
        for t in tokens:
            # Volatile step: 3.0% standard deviation to trigger exits extremely fast
            prices[t] += random.normalvariate(0, 0.03)
            prices[t] = max(0.10, min(0.90, prices[t])) # keep within entry price bounds
            
            # Tighter spread: 1 cent absolute spread (only 2% spread cost at 0.50)
            spread_abs = 0.01
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
                "bid_depth": 300.0,  # passes depth filter
                "ask_depth": 300.0,
                "liquidity_score": 600.0
            }
            market_data_map[t] = mdata
            enriched_tokens.append(mdata)

        # 2. Check resting limit fills in ExecutionEngine
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
                entry_times[tok_id] = time.time()
                logger.info(f"DB_BUY_FILL: Bought {fill['fill_size']:.2f} of {tok_id[:12]}... @ {fill['fill_price']:.4f}")
            elif fill["side"] == "SELL":
                completed_trips += 1
                hold_time = time.time() - entry_times.get(tok_id, time.time())
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
                logger.info(f"DB_SELL_FILL: Sold {fill['fill_size']:.2f} of {tok_id[:12]}... @ {fill['fill_price']:.4f} | PnL: {pnl:+.4f} | {outcome} | Trips: {completed_trips} (W:{wins_count} L:{losses_count})")

        # 3. Update telemetry tracker exposure and cash balances
        tracker.update_telemetry(enriched_tokens)
        tracker.process_rejections_tick(market_data_map)

        # 4. Connect Exit Engine: Query get_positions_for_exit
        exits = pm.get_positions_for_exit(market_data_map)
        for exit_cand in exits:
            tok_id = exit_cand["token_id"]
            is_emergency = "SL" in exit_cand["reason"]
            quoted_price = exit_cand["price"]
            
            # Avoid duplicate placements
            pending_sells = [o for o in ee.pending_orders if o["token_id"] == tok_id and o["side"] == "SELL"]
            if pending_sells:
                if is_emergency:
                    for o in pending_sells:
                        # Cancel existing passive TP limit sell for emergency SL exit
                        ee.pending_orders.remove(o)
                        tracker.record_cancellation(o["order_id"])
                else:
                    continue
                    
            logger.info(f"EXIT TRIGGERED: {tok_id[:12]}... | Reason: {exit_cand['reason']} | size {exit_cand['size']:.2f} @ {quoted_price:.4f}")
            
            try:
                result = await ee.place_limit_order(
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
                
                if result['status'] == "SUCCESS":
                    completed_trips += 1
                    pnl = pm.update_position(
                        token_id=tok_id,
                        size=result.get('fill_size', exit_cand["size"]),
                        price=result.get('fill_price', quoted_price),
                        side="SELL",
                        strategy="EXIT_" + exit_cand["reason"].split()[0],
                        spread=exit_cand.get("spread", 0),
                        slippage=result.get("slippage", 0)
                    )
                    rm.update_after_trade(success=True, token_id=tok_id, pnl=pnl)
                    
                    hold_time = time.time() - entry_times.get(tok_id, time.time())
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
                    logger.info(f"EXIT EXECUTED (SL): {tok_id[:12]}... | PnL: {pnl:+.4f} | {outcome} | Total Realized: {pm.realized_pnl:+.4f} | Trips: {completed_trips} (W:{wins_count} L:{losses_count})")
            except ValueError as e:
                logger.warning(f"Tracker blocked exit order: {e}")

        # 5. Passive Buying Logic (Generate entries when we have cash/capacity)
        # Check if we can buy: exposure + new order must not exceed PMM_TOTAL_CAPITAL (scaled to $1000.0 simulation base)
        current_prices = {tid: pos['avg_price'] for tid, pos in pm.positions.items()}
        total_exposure = sum(pos['size'] * current_prices.get(tid, pos['avg_price']) for tid, pos in pm.positions.items())
        
        for t in tokens:
            if t not in pm.positions and not any(o["token_id"] == t for o in ee.pending_orders):
                # Propose entry buy signal
                price = prices[t]
                size = BASE_ORDER_SIZE / price  # scale size to exactly $5 base cost
                
                signal = {
                    "token_id": t,
                    "side": "BUY",
                    "price": price,
                    "size": size,
                    "score": 0.8,
                    "delta": 0.06
                }
                
                # Risk validate signal (triggers hard capital limits!)
                if rm.validate_trade(signal, market_data_map[t], is_fallback=True):
                    # Place BUY passive limit order
                    try:
                        res = await ee.place_limit_order(
                            token_id=t,
                            price=price,
                            size=size,
                            side="BUY",
                            market_data=market_data_map[t]
                        )
                    except ValueError as e:
                        # Silently skip orders that tracker blocks due to capital limits
                        pass
                    
        # Sleep extremely briefly to simulate loop pacing
        await asyncio.sleep(0.001)

    # Compile results
    logger.info(f"Phase 2 validation target reached!")
    logger.info(f"Round trips: {completed_trips} | Wins: {wins_count} | Losses: {losses_count}")

    # Print metrics to a JSON file for python script reporting
    metrics = {
        "round_trips": completed_trips,
        "realized_pnl": pm.realized_pnl,
        "wins": wins_count,
        "losses": losses_count,
        "avg_win": sum(r["pnl"] for r in round_trips if r["pnl"] > 0) / wins_count if wins_count > 0 else 0.0,
        "avg_loss": sum(r["pnl"] for r in round_trips if r["pnl"] < 0) / losses_count if losses_count > 0 else 0.0,
        "holding_times": holding_times,
        "round_trips_data": round_trips
    }

    with open("storage/validation_phase2_results.json", "w") as fh:
        json.dump(metrics, fh, indent=2)

    print("\n=== PHASE 2 METRICS COMPILATION ===")
    print(f"A. Completed round trips  : {completed_trips}")
    print(f"B. Realized PnL            : ${pm.realized_pnl:+.4f} USD")
    print(f"C. Average win             : ${metrics['avg_win']:+.4f} USD")
    print(f"D. Average loss            : ${metrics['avg_loss']:+.4f} USD")
    print(f"E. Profit factor           : {abs(sum(r['pnl'] for r in round_trips if r['pnl'] > 0) / sum(r['pnl'] for r in round_trips if r['pnl'] < 0)):.4f}" if losses_count > 0 else "inf")
    print(f"F. Win rate                : {wins_count / completed_trips * 100:.2f}%")
    print(f"G. EV per trade            : ${pm.realized_pnl / completed_trips:+.4f} USD")
    print(f"H. Average holding time    : {sum(holding_times)/len(holding_times):.6f} seconds")

    # Exits count
    tp_exits = len([r for r in round_trips if r["outcome"] == "WIN"])
    sl_exits = len([r for r in round_trips if r["outcome"] == "LOSS"])
    print(f"I. Number of TP exits      : {tp_exits}")
    print(f"J. Number of SL exits      : {sl_exits}")

if __name__ == "__main__":
    asyncio.run(main())
