import asyncio
import time
import json
import sys
import os
import datetime

# Add root directory to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import (
    POLLING_INTERVAL, PAPER_TRADING, TEST_MODE,
    TP_HIGH_CONFIDENCE, TP_STRONG_TREND, MAX_SPREAD,
    MOMENTUM_THRESHOLD, MOMENTUM_MIN_LIQUIDITY,
    ZOMBIE_MIN_DEPTH_TICKS, DATA_AGE_BLOCK_SECONDS,
    PRE_TRADE_REVALIDATE, MAX_SIGNAL_DELTA
)
from app.logger import logger
from app.data_fetcher import DataFetcher
from app.market_filter import MarketFilter
from app.signal_engine import SignalEngine
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from app.position_manager import PositionManager
from strategies.momentum_strategy import MomentumStrategy
from strategies.mean_reversion_strategy import MeanReversionStrategy
from strategies.two_sided_mm_strategy import TwoSidedMMStrategy
from app.websocket_client import WebsocketClient
from app.token_blacklist import TokenBlacklist

async def run_live_paper_validation():
    logger.info("Initializing Live Paper-Trading Validation for YES+NO MM...")
    
    # 1. Initialize core production components
    data_fetcher = DataFetcher()
    ws_client = WebsocketClient()
    data_fetcher.set_websocket(ws_client)
    
    market_filter = MarketFilter()
    position_manager = PositionManager()
    position_manager.clear_all_positions()
    risk_manager = RiskManager(position_manager=position_manager)
    execution_engine = ExecutionEngine(paper_trading=True)
    token_blacklist = TokenBlacklist()
    
    # Set up paper trading tracker with strict $500 capital limit
    from app.paper_validation_tracker import PaperValidationTracker
    paper_tracker = PaperValidationTracker(total_capital=500.0)
    execution_engine.set_tracker(paper_tracker)
    
    # Initialize strategies (including our new production TwoSidedMMStrategy)
    strategies = [
        MomentumStrategy(
            window_size=10, 
            momentum_threshold=MOMENTUM_THRESHOLD if not TEST_MODE else 0.001, 
            min_liquidity=MOMENTUM_MIN_LIQUIDITY
        ),
        MeanReversionStrategy(window_size=5),
        TwoSidedMMStrategy()
    ]
    
    confidence_threshold = 70 if not TEST_MODE else 10
    signal_engine = SignalEngine(strategies, confidence_threshold=confidence_threshold)
    
    # Start WebSocket client in background
    asyncio.create_task(ws_client.connect())
    
    logger.info("Subscribing to active tokens...")
    initial_markets = await data_fetcher.fetch_markets()
    
    if isinstance(initial_markets, dict):
        _mlist = initial_markets.get("data", list(initial_markets.values()))
    else:
        _mlist = list(initial_markets)
        
    active_tokens = []
    for m in _mlist:
        if not isinstance(m, dict):
            continue
        for tok in m.get("tokens", []):
            tid = tok.get("token_id")
            if tid:
                active_tokens.append(str(tid))
    logger.info(f"WS Subscribing to top {len(active_tokens[:150])} token ids...")
    await ws_client.subscribe(active_tokens[:150])

    # Performance logging helpers
    leg_risk_count = 0
    hedge_count = 0
    failed_hedge_count = 0
    spreads_captured = []
    round_trips_count = 0
    capital_recycled_volume = 0.0
    
    # Audit Counters (Step 1 & Step 2 requirements)
    total_signals_generated = 0
    total_signals_rejected = 0
    rejection_breakdown = {
        "ZOMBIE_PRICE_CAP": 0,
        "SPREAD_TOO_WIDE": 0,
        "NEGATIVE_EV": 0,
        "ZOMBIE_SPREAD": 0,
        "ZOMBIE_BID_FLOOR": 0,
        "EXCEEDS_AVAILABLE_CAPITAL": 0,
        "MAX_CAPITAL_PER_MARKET": 0,
        "MAX_OPEN_POSITIONS": 0,
        "NOISE_MOVE_TOO_SMALL": 0,
        "OTHER": 0
    }
    orders_reaching_ee = 0
    orders_placed = 0
    orders_filled = 0
    
    start_time = time.time()
    max_duration_seconds = 24 * 3600 # 24 hours auto-stop
    
    logger.info("="*60)
    logger.info("   LIVE YES+NO PAPER-TRADING VALIDATION LOOP STARTED")
    logger.info("="*60)
    
    while True:
        try:
            elapsed_time = time.time() - start_time
            
            # --- AUTO-STOP CONDITIONS ---
            # 1. 50 completed paired cycles (round trips)
            # 2. 24 hours elapsed
            if round_trips_count >= 50:
                logger.warning(f"AUTO-STOP TRIGGERED: Completed paired cycles ({round_trips_count}) >= 50!")
                break
            if elapsed_time >= max_duration_seconds:
                logger.warning(f"AUTO-STOP TRIGGERED: Elapsed time ({elapsed_time/3600:.2f}h) >= 24h limit!")
                break
                
            loop_stats = {"scanned": 0, "selected": 0, "generated": 0, "executed": 0}
            
            # 1. Fetch live markets
            markets = await data_fetcher.fetch_markets()
            if isinstance(markets, dict) and "data" in markets:
                loop_stats["scanned"] = len(markets["data"])
            elif isinstance(markets, list):
                loop_stats["scanned"] = len(markets)
                
            # 2. Filter markets
            candidate_markets = market_filter.filter_markets(markets)
            loop_stats["selected"] = len(candidate_markets)
            
            # 3. Enrich token market data
            enriched_tokens = []
            process_candidates = candidate_markets[:30]
            
            for market in process_candidates:
                tokens = market.get("tokens", [])
                for token_data in tokens:
                    t_id = token_data.get("token_id")
                    if not t_id: continue
                    
                    orderbook = await data_fetcher.get_orderbook(t_id)
                    if not orderbook: continue
                    
                    try:
                        raw_bids = orderbook.get("bids", []) if isinstance(orderbook, dict) else getattr(orderbook, "bids", [])
                        raw_asks = orderbook.get("asks", []) if isinstance(orderbook, dict) else getattr(orderbook, "asks", [])
                        
                        if not raw_bids or not raw_asks: continue
                        
                        bids = sorted(raw_bids, key=lambda x: float(x.price if hasattr(x, "price") else x.get("price", 0)), reverse=True)
                        asks = sorted(raw_asks, key=lambda x: float(x.price if hasattr(x, "price") else x.get("price", 0)))
                        
                        def get_price(level):
                            if hasattr(level, "price"): return float(level.price)
                            if isinstance(level, dict): return float(level.get("price") or level.get("p") or 0)
                            return 0.0
                            
                        best_bid, best_ask = get_price(bids[0]), get_price(asks[0])
                        bid_depth = sum(float(b.size if hasattr(b, "size") else b.get("size", 0)) for b in bids[:2]) * best_bid
                        ask_depth = sum(float(a.size if hasattr(a, "size") else a.get("size", 0)) for a in asks[:2]) * best_ask
                        
                        mid_price = (best_bid + best_ask) / 2
                        spread = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0
                        
                        token_market = market.copy()
                        token_market.update({
                            "token_id": t_id,
                            "orderbook": orderbook,
                            "last_price": mid_price,
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "mid_price": mid_price,
                            "bid_depth": bid_depth,
                            "ask_depth": ask_depth,
                            "spread": spread,
                            "liquidity_score": (bid_depth + ask_depth) / (spread + 0.001)
                        })
                        enriched_tokens.append(token_market)
                    except Exception:
                        continue
                        
            enriched_tokens.sort(key=lambda x: x["liquidity_score"], reverse=True)
            market_data_map = {t.get("token_id"): t for t in enriched_tokens if t.get("token_id")}
            
            # 4. Check resting passive limit order fills
            fills = execution_engine.check_resting_fills(market_data_map)
            for fill in fills:
                loop_stats["executed"] += 1
                orders_filled += 1
                pnl = position_manager.update_position(
                    token_id=fill["token_id"],
                    size=fill["fill_size"],
                    price=fill["fill_price"],
                    side=fill["side"],
                    strategy=fill.get("strategy", "PMM"),
                    spread=fill.get("spread", 0.0),
                    slippage=fill.get("slippage", 0.0)
                )
                risk_manager.update_after_trade(success=True, token_id=fill["token_id"], pnl=pnl)
                
                # Check for leg fills and cycles completion
                if fill["side"] == "BUY":
                    # Leg filled passively
                    leg_risk_count += 1
                elif fill["side"] == "SELL":
                    # Exit ask filled passively -> completed cycle leg!
                    round_trips_count += 0.5  # two sells = 1 completed cycle
                    capital_recycled_volume += fill["fill_size"] * fill["fill_price"]
                    spreads_captured.append(fill.get("spread", 0.0))
                    
            position_manager.update_inventory_metrics(enriched_tokens)
            
            # 5. Paired Package Exit Engine
            exits = position_manager.get_positions_for_exit(market_data_map)
            for exit_cand in exits:
                tok_id = exit_cand["token_id"]
                quoted_price = exit_cand["price"]
                
                pending_sells = [o for o in execution_engine.pending_orders if o["token_id"] == tok_id and o["side"] == "SELL"]
                if pending_sells:
                    # Repricing logic: If the market moved, cancel the stale order and reprice
                    o = pending_sells[0]
                    if abs(o["price"] - quoted_price) >= 1e-4:
                        logger.info(f"[NEW EXIT ENGINE] Repricing exit for {tok_id[:20]}: cancelling stale order {o['order_id']} at {o['price']:.4f} to reprice at {quoted_price:.4f}")
                        await execution_engine.cancel_order(o["order_id"])
                        if paper_tracker:
                            paper_tracker.record_cancellation(o["order_id"])
                    else:
                        continue
                    
                result = await execution_engine.place_limit_order(
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
                if result['status'] == "SUCCESS":
                    loop_stats["executed"] += 1
                    orders_reaching_ee += 1
                    orders_placed += 1
                    orders_filled += 1
                    pnl = position_manager.update_position(
                        token_id=tok_id,
                        size=result.get('fill_size', exit_cand["size"]),
                        price=result.get('fill_price', quoted_price),
                        side="SELL",
                        strategy="EXIT_TWO_SIDED",
                        spread=exit_cand.get("spread", 0),
                        slippage=result.get("slippage", 0)
                    )
                    risk_manager.update_after_trade(success=True, token_id=tok_id, pnl=pnl)
                    round_trips_count += 0.5
                    capital_recycled_volume += result.get('fill_size', exit_cand["size"]) * result.get('fill_price', quoted_price)
                    spreads_captured.append(exit_cand.get("spread", 0))

            # 6. Active Taker Hedge Engine (Leg Risk Mitigation)
            for token_id, pos in list(position_manager.positions.items()):
                minfo = market_data_map.get(token_id)
                if not minfo: continue
                
                opposing_token_id = None
                for tok in minfo.get("tokens", []):
                    tid = tok.get("token_id")
                    if tid and str(tid) != str(token_id):
                        opposing_token_id = str(tid)
                        break
                        
                if opposing_token_id and opposing_token_id not in position_manager.positions:
                    # Single-leg fill open for more than 3 loops (30s) -> hedge aggressively
                    entry_time = pos.get("entry_time", time.time())
                    if time.time() - entry_time >= 30:
                        opp_minfo = market_data_map.get(opposing_token_id)
                        if opp_minfo:
                            hedge_price = opp_minfo.get("best_ask") or opp_minfo.get("last_price") or 0.50
                            
                            # Cancel passive order
                            pending_buys = [o for o in execution_engine.pending_orders if o["token_id"] == opposing_token_id and o["side"] == "BUY"]
                            for o in pending_buys:
                                await execution_engine.cancel_order(o["order_id"])
                                
                            logger.warning(
                                f"LEG-RISK HEDGE TRIGGERED: Holding {token_id[:20]} but missing opposing leg. "
                                f"Executing active taker hedge BUY on {opposing_token_id[:20]} at {hedge_price:.4f}."
                            )
                            
                            result = await execution_engine.place_limit_order(
                                token_id=opposing_token_id,
                                price=hedge_price,
                                size=pos["size"],
                                side="BUY",
                                market_data=opp_minfo,
                                is_aggressive=True,
                                is_emergency=True,
                                signal_delta=0,
                                signal_spread=opp_minfo.get("spread", 0)
                            )
                            if result['status'] == "SUCCESS":
                                hedge_count += 1
                                loop_stats["executed"] += 1
                                orders_reaching_ee += 1
                                orders_placed += 1
                                orders_filled += 1
                                position_manager.update_position(
                                    token_id=opposing_token_id,
                                    size=result.get('fill_size', pos["size"]),
                                    price=result.get('fill_price', hedge_price),
                                    side="BUY",
                                    strategy="HEDGE",
                                    spread=opp_minfo.get("spread", 0),
                                    slippage=result.get("slippage", 0)
                                )
                                risk_manager.update_after_trade(success=True, token_id=opposing_token_id)
                                
                                # Add failed hedge/slippage audit tracking
                                if result.get("slippage", 0.0) > 0.02:
                                    failed_hedge_count += 1

            # 7. Generate & Filter Signal Pairs
            signals, sig_stats = await signal_engine.generate_signals(enriched_tokens)
            for signal in signals:
                tok_id = signal["token_id"]
                live_market_data = market_data_map.get(tok_id)
                if not live_market_data: continue
                
                total_signals_generated += 1
                
                if risk_manager.validate_trade(signal, live_market_data):
                    orders_reaching_ee += 1
                    dynamic_size = risk_manager.calculate_position_size(signal, live_market_data)
                    mid_price = (live_market_data.get("best_bid", 0.5) + live_market_data.get("best_ask", 0.5)) / 2
                    
                    skew = risk_manager.calculate_inventory_skew(tok_id, mid_price, signal['side'], market_data=live_market_data)
                    quoted_price = round(max(0.01, min(0.99, signal['price'] - skew)), 4)
                    
                    result = await execution_engine.place_limit_order(
                        token_id=tok_id,
                        price=quoted_price,
                        size=dynamic_size,
                        side="BUY",
                        market_data=live_market_data,
                        is_aggressive=False,
                        signal_delta=signal.get('delta', 0),
                        signal_spread=live_market_data.get('spread', 0)
                    )
                    if result['status'] == "PENDING":
                        orders_placed += 1
                    elif result['status'] == "SUCCESS":
                        orders_placed += 1
                        loop_stats["executed"] += 1
                        position_manager.update_position(
                            token_id=tok_id,
                            size=result.get('fill_size', dynamic_size),
                            price=result.get('fill_price', quoted_price),
                            side="BUY",
                            strategy=signal.get('strategy', 'Unknown'),
                            spread=live_market_data.get('spread', 0),
                            slippage=result.get("slippage", 0)
                        )
                        risk_manager.update_after_trade(success=True, token_id=tok_id)
                else:
                    total_signals_rejected += 1
                    reason = getattr(risk_manager, "last_rejection_reason", "OTHER")
                    if reason in rejection_breakdown:
                        rejection_breakdown[reason] += 1
                    else:
                        rejection_breakdown["OTHER"] += 1

            # Update telemetry and check outcomes of rejections
            if PAPER_TRADING and paper_tracker:
                paper_tracker.update_telemetry(enriched_tokens)
                paper_tracker.process_rejections_tick(market_data_map)
                
            # Periodically write intermediate status to file (every 6 loops = 1 min)
            if int(elapsed_time) % 60 < 10:
                stats = {
                    "elapsed_hours": round(elapsed_time / 3600, 3),
                    "completed_paired_cycles": int(round_trips_count),
                    "realized_pnl": round(position_manager.realized_pnl, 4),
                    "max_drawdown": round(paper_tracker.max_drawdown if paper_tracker else 0.0, 4),
                    "leg_risk_count": leg_risk_count,
                    "hedge_count": hedge_count,
                    "failed_hedge_count": failed_hedge_count,
                    "capital_recycled_volume": round(capital_recycled_volume, 2)
                }
                with open("storage/live_paper_validation_status.json", "w") as fh:
                    json.dump(stats, fh, indent=2)

            await asyncio.sleep(POLLING_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in live paper loop: {e}")
            await asyncio.sleep(POLLING_INTERVAL)

    # ==================================================================
    # PAPER TRADING RUN COMPLETE: GENERATE COMPARATIVE AUDIT REPORT
    # ==================================================================
    logger.info("Live Paper-Trading Run Complete. Generating comparative results...")
    
    # Calculate live paper performance metrics (AFTER values)
    realized_pnl = position_manager.realized_pnl
    fill_rate = (orders_filled / orders_placed * 100) if orders_placed > 0 else 0.0
    
    # BEFORE values from OLD queue model run (baseline)
    before_res = {
        "orders_placed": 56,
        "orders_filled": 0,
        "fill_rate": 0.0,
        "avg_latency": 0.0,
        "completed_cycles": 0,
        "pnl": 0.0,
        "max_drawdown": 0.0
    }
    
    avg_latency = (paper_tracker.total_latency_seconds / paper_tracker.total_fills) if paper_tracker and paper_tracker.total_fills > 0 else 0.0
    
    after_res = {
        "orders_placed": orders_placed,
        "orders_filled": orders_filled,
        "fill_rate": fill_rate,
        "avg_latency": avg_latency,
        "completed_cycles": int(round_trips_count),
        "pnl": realized_pnl,
        "max_drawdown": paper_tracker.max_drawdown if paper_tracker else 0.0
    }

    # Print final validation comparison table
    print("\n" + "="*95)
    print("             YES+NO PMM EXECUTION AUDIT: OLD VS NEW QUEUE MODEL COMPARISON")
    print("="*95)
    print(f"{'Metric':<30} | {'OLD Queue Model':<20} | {'NEW Queue Model':<20} | {'% Change':<15}")
    print("-" * 95)
    
    def format_val(val, key):
        if key == "pnl" or key == "max_drawdown":
            return f"${val:+.4f}" if val != 0 else "$0.0000"
        elif key in ["fill_rate"]:
            return f"{val:.2f}%"
        elif key == "avg_latency":
            return f"{val:.2f}s"
        return str(val)

    def pct_change(after, before):
        if before == 0:
            if after > 0:
                return "+100.00% (Unblocked!)"
            return "0.00%"
        return f"{((after - before) / before * 100):+.2f}%"

    comparison_keys = [
        ("orders_placed", "1. Orders Placed"),
        ("orders_filled", "2. Orders Filled"),
        ("fill_rate", "3. Fill Rate (%)"),
        ("avg_latency", "4. Avg Fill Latency (s)"),
        ("completed_cycles", "5. Completed Paired Cycles"),
        ("pnl", "6. Realized PnL ($)"),
        ("max_drawdown", "7. Maximum Drawdown ($)")
    ]
    
    for key, name in comparison_keys:
        before_val = before_res[key]
        after_val = after_res[key]
        change = pct_change(after_val, before_val)
        
        before_str = format_val(before_val, key)
        after_str = format_val(after_val, key)
        
        print(f"{name:<30} | {before_str:<20} | {after_str:<20} | {change:<15}")
        
    print("="*95)
    print("\nAFTER REJECTION BREAKDOWN:")
    for reason, count in sorted(rejection_breakdown.items(), key=lambda x: x[1], reverse=True):
        if count > 0 or reason in ["ZOMBIE_PRICE_CAP", "SPREAD_TOO_WIDE", "NEGATIVE_EV"]:
            pct = (count / total_signals_rejected * 100) if total_signals_rejected > 0 else 0.0
            print(f"  * {reason}: {count} ({pct:.2f}%)")
    print("="*95)
    
    # Save the comparison report
    final_report = {
        "before_metrics": before_res,
        "after_metrics": after_res,
        "rejection_breakdown": rejection_breakdown
    }
    with open("storage/live_paper_validation_report.json", "w") as fh:
        json.dump(final_report, fh, indent=2)

if __name__ == "__main__":
    asyncio.run(run_live_paper_validation())
