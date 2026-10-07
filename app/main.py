import asyncio
import copy
import time
import collections
import datetime
from .config import (
    POLLING_INTERVAL, PAPER_TRADING, TEST_MODE,
    TP_HIGH_CONFIDENCE, TP_STRONG_TREND, MAX_SPREAD, MARKET_COOLDOWN,
    LOGS_DIR, ZOMBIE_MIN_DEPTH_TICKS, DATA_AGE_BLOCK_SECONDS,
    PRE_TRADE_REVALIDATE, MAX_SIGNAL_DELTA
)
from .logger import logger
from .data_fetcher import DataFetcher
from .market_filter import MarketFilter
from .signal_engine import SignalEngine
from .risk_manager import RiskManager, is_aggressive_signal
from .execution_engine import ExecutionEngine
from .position_manager import PositionManager
from .safety_layer import SafetyLayer
from strategies.spread_strategy import SpreadStrategy
from strategies.consistency_strategy import ConsistencyStrategy
from strategies.arbitrage_strategy import ArbitrageStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.mean_reversion_strategy import MeanReversionStrategy
from strategies.two_sided_mm_strategy import TwoSidedMMStrategy
from strategies.news_strategy import NewsStrategy
from strategies.volume_spike_strategy import VolumeSpikeStrategy
from strategies.order_book_imbalance_strategy import OrderBookImbalanceStrategy
from .websocket_client import WebsocketClient
from .token_blacklist import TokenBlacklist
from .paper_fill_model import get_tick_size, round_to_tick


class HourlyCalibrationTracker:
    """
    Accumulates per-loop signal statistics and emits a CALIBRATION_HOURLY_REPORT
    log line once per hour. Does NOT modify any trading logic.
    """

    def __init__(self):
        self._hour = datetime.datetime.now().hour
        self._reset()

    def _reset(self):
        self._signals_generated   = 0
        self._signals_accepted    = 0   # driven by CALIBRATION_ACCEPTED counter below
        self._paper_trades        = 0
        self._rejection_reasons   = collections.Counter()

    def record_loop(self, loop_stats):
        """Call once per loop with the current loop_stats dict."""
        self._signals_generated += loop_stats.get("generated", 0)
        self._paper_trades      += loop_stats.get("executed", 0)

    def record_accepted(self):
        """Call every time risk_manager.validate_trade() returns True."""
        self._signals_accepted += 1

    def record_rejection(self, reason: str):
        """Call with the CALIBRATION_REJECTED reason string."""
        self._rejection_reasons[reason] += 1

    def tick(self):
        """
        Check if the hour has rolled over. If so, flush the report and reset.
        Should be called once per loop iteration.
        """
        now_hour = datetime.datetime.now().hour
        if now_hour != self._hour:
            self._flush()
            self._hour = now_hour
            self._reset()

    def _flush(self):
        total = self._signals_generated
        accepted = self._signals_accepted
        ratio = (accepted / total * 100) if total > 0 else 0.0
        top_reason = self._rejection_reasons.most_common(1)
        top_reason_str = (
            f"{top_reason[0][0]} x{top_reason[0][1]}" if top_reason else "none"
        )
        all_reasons = ", ".join(
            f"{r}={c}" for r, c in self._rejection_reasons.most_common(5)
        ) or "none"
        logger.info(
            f"CALIBRATION_HOURLY_REPORT | "
            f"hour={self._hour:02d}:xx | "
            f"signals_generated={total} | "
            f"signals_accepted={accepted} | "
            f"paper_trades={self._paper_trades} | "
            f"acceptance_ratio={ratio:.1f}% | "
            f"top_rejection={top_reason_str} | "
            f"all_rejections=[{all_reasons}]"
        )


async def main():
    logger.info("Starting Polymarket Trading Bot...")
    
    # Initialize components
    data_fetcher = DataFetcher()
    ws_client = WebsocketClient()
    data_fetcher.set_websocket(ws_client)
    
    market_filter = MarketFilter()
    position_manager = PositionManager()
    risk_manager = RiskManager(position_manager=position_manager)
    execution_engine = ExecutionEngine(paper_trading=PAPER_TRADING)
    safety_layer = SafetyLayer(position_manager=position_manager, execution_engine=execution_engine)
    token_blacklist = TokenBlacklist()  # drift-based token suppression
    
    # Initialize Live Paper Trading Validation Mode (Week 2B)
    paper_tracker = None
    if PAPER_TRADING:
        from .paper_validation_tracker import PaperValidationTracker
        from .config import PAPER_INITIAL_CAPITAL
        paper_tracker = PaperValidationTracker(total_capital=PAPER_INITIAL_CAPITAL)
        execution_engine.set_tracker(paper_tracker)
    
    if PAPER_TRADING:
        logger.info("Startup: Recovering database positions for fresh Paper Trading session.")
        # position_manager.clear_all_positions()  # Disabled for database position recovery
    
    # Initialize strategies
    from .config import MOMENTUM_THRESHOLD, MOMENTUM_MIN_LIQUIDITY, TOTAL_CAPITAL_POOL
    strategies = [
        TwoSidedMMStrategy(),
        NewsStrategy(poll_interval=300, data_fetcher=data_fetcher),
        MomentumStrategy(
            window_size=10, 
            momentum_threshold=MOMENTUM_THRESHOLD if not TEST_MODE else 0.001, 
            min_liquidity=MOMENTUM_MIN_LIQUIDITY
        ),
        VolumeSpikeStrategy(window_size=8, spike_threshold=3.0),
        OrderBookImbalanceStrategy()  # v2: tightened thresholds, log-delta, spread filter
    ]
    
    # Threshold for SignalEngine (0-100 scale)
    confidence_threshold = 70 if not TEST_MODE else 10
    signal_engine = SignalEngine(strategies, confidence_threshold=confidence_threshold, total_capital_pool=TOTAL_CAPITAL_POOL)
    
    # Start Websocket in background
    asyncio.create_task(ws_client.connect())
    
    logger.info("Pre-subscribing to active tokens...")
    initial_markets = await data_fetcher.fetch_markets()
    # Flatten to per-token IDs — the WS endpoint expects integer token_id,
    # NOT the market-level condition_id (hex hash) which is a different identifier.
    if isinstance(initial_markets, dict):
        _mlist = initial_markets.get("data", list(initial_markets.values()))
    else:
        _mlist = list(initial_markets)
    active_tokens = []
    for m in _mlist:
        if not isinstance(m, dict):
            continue
        for tok in m.get("tokens", []):
            tid = tok.get("token_id") if isinstance(tok, dict) else None
            if tid:
                active_tokens.append(str(tid))
    logger.info(f"WS_PRE_SUBSCRIBE: collected {len(active_tokens)} token_ids from {len(_mlist)} markets")
    await ws_client.subscribe(active_tokens[:200])

    loops_since_last_trade = 0
    hourly_tracker = HourlyCalibrationTracker()
    current_hour = datetime.datetime.now().hour
    market_data_map = {}  # last enriched book snapshot, reused by the emergency stop
    while True:
        try:
            # --- SAFETY LAYER GUARD CHECKS ---
            if await safety_layer.is_kill_switch_active():
                logger.critical("SAFETY HALT: Emergency Kill Switch is ACTIVE! Initiating safety lockdown.")
                await safety_layer.trigger_emergency_stop(market_data_map)
                break

            daily_loss_ok = await safety_layer.check_daily_loss()
            ws_health_ok = await safety_layer.check_ws_health(ws_client)
            await safety_layer.run_stale_order_watchdog()

            loop_stats = {
                "scanned": 0,
                "selected": 0,
                "generated": 0,
                "rejected_noise": 0,
                "rejected_score": 0,
                "rejected_expected_profit": 0,
                "executed": 0
            }

            # 1. Fetch Markets
            markets = await data_fetcher.fetch_markets()
            if isinstance(markets, dict) and "data" in markets:
                loop_stats["scanned"] = len(markets["data"])
            elif isinstance(markets, list):
                loop_stats["scanned"] = len(markets)
            else:
                loop_stats["scanned"] = 0
            
            # 2. Filter Markets
            candidate_markets = market_filter.filter_markets(markets)
            loop_stats["selected"] = len(candidate_markets)
            
            # Dynamically subscribe candidate tokens to live WebSocket feed
            cand_tokens = []
            for cm in candidate_markets[:30]:
                for tok in cm.get("tokens", []):
                    tid = tok.get("token_id")
                    if tid and str(tid) not in ws_client.subscriptions:
                        cand_tokens.append(str(tid))
            if cand_tokens:
                await ws_client.subscribe(cand_tokens)

            # 3. Enrich Market Data
            enriched_tokens = []
            process_candidates = candidate_markets[:30]

            # ── Enrichment diagnostic counters ───────────────────────────
            _enr_tokens_discovered   = 0
            _enr_ob_requested        = 0
            _enr_ob_success          = 0
            _enr_ob_empty_response   = 0  # returned None
            _enr_ob_no_bids          = 0
            _enr_ob_no_asks          = 0
            _enr_ob_parse_failure    = 0

            for market in process_candidates:
                tokens = market.get("tokens", [])
                for token_data in tokens:
                    t_id = token_data.get("token_id")
                    if not t_id: continue

                    _enr_tokens_discovered += 1
                    _enr_ob_requested      += 1

                    orderbook = await data_fetcher.get_orderbook(t_id)
                    if not orderbook:
                        _enr_ob_empty_response += 1
                        continue

                    try:
                        raw_bids = orderbook.get("bids", []) if isinstance(orderbook, dict) else getattr(orderbook, "bids", [])
                        raw_asks = orderbook.get("asks", []) if isinstance(orderbook, dict) else getattr(orderbook, "asks", [])
                    except Exception:
                        _enr_ob_parse_failure += 1
                        continue

                    if not raw_bids:
                        _enr_ob_no_bids += 1
                        continue
                    if not raw_asks:
                        _enr_ob_no_asks += 1
                        continue

                    _enr_ob_success += 1

                    bids = sorted(list(raw_bids), key=lambda x: float(x.price if hasattr(x, "price") else x.get("price", 0)), reverse=True)
                    asks = sorted(list(raw_asks), key=lambda x: float(x.price if hasattr(x, "price") else x.get("price", 0)))

                    def get_price(level):
                        if hasattr(level, "price"): return float(level.price)
                        if isinstance(level, dict): return float(level.get("price") or level.get("p") or 0)
                        return 0.0

                    best_bid, best_ask = get_price(bids[0]), get_price(asks[0])
                    bid_depth = sum(float(b.size if hasattr(b, "size") else b.get("size", 0)) for b in bids[:2]) * best_bid
                    ask_depth = sum(float(a.size if hasattr(a, "size") else a.get("size", 0)) for a in asks[:2]) * best_ask

                    mid_price = (best_bid + best_ask) / 2
                    spread = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0

                    # Use live orderbook mid_price as last_price (real-time WS bids/asks).
                    # Falls back to REST snapshot only if mid_price is zero/unavailable.
                    rest_snapshot_price = float(token_data.get("price") or 0)
                    live_last_price = mid_price if mid_price > 0 else rest_snapshot_price

                    token_market = market.copy()
                    token_market.update({
                        "token_id": t_id,
                        "orderbook": orderbook,
                        "last_price": live_last_price,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "bid_depth": bid_depth,
                        "ask_depth": ask_depth,
                        "spread": spread,
                        "liquidity_score": (bid_depth + ask_depth) / (spread + 0.001)
                    })
                    enriched_tokens.append(token_market)

            enriched_tokens.sort(key=lambda x: x["liquidity_score"], reverse=True)

            # ── ENRICHMENT_REPORT ──────────────────────────────────────
            logger.info(
                f"ENRICHMENT_REPORT | "
                f"candidates={len(candidate_markets)} | "
                f"process_candidates={len(process_candidates)} | "
                f"tokens_discovered={_enr_tokens_discovered} | "
                f"orderbooks_requested={_enr_ob_requested} | "
                f"orderbooks_success={_enr_ob_success} | "
                f"orderbooks_empty_response={_enr_ob_empty_response} | "
                f"orderbooks_no_bids={_enr_ob_no_bids} | "
                f"orderbooks_no_asks={_enr_ob_no_asks} | "
                f"orderbooks_parse_failure={_enr_ob_parse_failure} | "
                f"enriched_tokens={len(enriched_tokens)}"
            )

            # 3.5 Check resting passive limit order fills
            market_data_map = {t.get("token_id"): t for t in enriched_tokens if t.get("token_id")}
            fills = execution_engine.check_resting_fills(market_data_map)
            for fill in fills:
                loop_stats["executed"] += 1
                pnl = position_manager.update_position(
                    token_id=fill["token_id"],
                    size=fill["fill_size"],
                    price=fill["fill_price"],
                    side=fill["side"],
                    strategy=fill.get("strategy", "PMM"),
                    spread=fill.get("spread", 0.0),
                    slippage=fill.get("slippage", 0.0),
                    is_maker=fill.get("is_maker", True),
                    order_id=fill.get("order_id"),
                    fee=fill.get("fee")
                )
                risk_manager.update_after_trade(success=True, token_id=fill["token_id"], pnl=pnl, strategy=fill.get("strategy", "PMM"))

            # Update session inventory tracking & metrics
            position_manager.update_inventory_metrics(enriched_tokens)

            # --- REAL EXIT ENGINE (Priority 1) ---
            exits = position_manager.get_positions_for_exit(market_data_map)
            for exit_cand in exits:
                tok_id = exit_cand["token_id"]
                is_emergency = "SL" in exit_cand["reason"]
                # Maker exits (TP attempts, two-sided MM unwinds) rest on the book;
                # only stop-losses and TP fallbacks cross the spread.
                is_aggressive_exit = is_emergency or exit_cand.get("is_maker", True) is False
                exit_market_data = market_data_map.get(tok_id)
                if exit_market_data is None:
                    # Position filtered out of this loop's universe: fetch its book directly
                    exit_market_data = await data_fetcher.get_fresh_orderbook_rest(tok_id)
                quoted_price = round_to_tick(
                    exit_cand["price"], get_tick_size(exit_market_data), "SELL", aggressive=is_aggressive_exit
                )
                
                # Check if we already have a pending SELL order for this token to avoid duplicates
                pending_sells = [o for o in execution_engine.pending_orders if o["token_id"] == tok_id and o["side"] == "SELL"]
                if pending_sells:
                    if is_aggressive_exit:
                        # Cancel existing passive sell to execute an immediate taker exit (SL / TP fallback)
                        for o in pending_sells:
                            await execution_engine.cancel_order(o["order_id"])
                    else:
                        # If existing passive sell is already placed, check if it needs repricing
                        o = pending_sells[0]
                        if abs(o["price"] - quoted_price) >= 1e-4:
                            logger.info(f"REPRICING EXIT: Cancelling stale exit order {o['order_id']} at {o['price']:.4f} to reprice at {quoted_price:.4f}")
                            await execution_engine.cancel_order(o["order_id"])
                        else:
                            # Price is still optimal, let it rest
                            continue
                
                logger.info(
                    f"EXIT TRIGGERED: {tok_id[:20]}... | Reason: {exit_cand['reason']} | "
                    f"placing SELL order of size {exit_cand['size']:.2f} @ {quoted_price:.4f} "
                    f"(emergency={is_emergency})"
                )
                
                result = await execution_engine.place_limit_order(
                    token_id=tok_id,
                    price=quoted_price,
                    size=exit_cand["size"],
                    side="SELL",
                    market_data=exit_market_data,
                    is_aggressive=is_aggressive_exit,
                    is_emergency=is_emergency,
                    signal_delta=0,
                    signal_spread=exit_cand.get("spread", 0)
                )
                
                if result['status'] == "SUCCESS":
                    loop_stats["executed"] += 1
                    pnl = position_manager.update_position(
                        token_id=tok_id,
                        size=result.get('fill_size', exit_cand["size"]),
                        price=result.get('fill_price', quoted_price),
                        side="SELL",
                        strategy="EXIT_" + exit_cand["reason"].split()[0],
                        spread=exit_cand.get("spread", 0),
                        slippage=result.get("slippage", 0),
                        is_maker=result.get("is_maker", False),
                        order_id=result.get("order_id"),
                        exit_reason=exit_cand["reason"],
                        fee=result.get("fee")
                    )
                    risk_manager.update_after_trade(success=True, token_id=tok_id, pnl=pnl, strategy="EXIT_" + exit_cand["reason"].split()[0])
                    logger.info(f"EXIT EXECUTED: {tok_id[:20]}... | PnL: {pnl:+.4f} | Total Realized: {position_manager.realized_pnl:+.4f}")

            # --- LEG-RISK HEDGE ENGINE (Priority 1B) ---
            for token_id, pos in list(position_manager.positions.items()):
                market_info = market_data_map.get(token_id)
                if not market_info:
                    continue
                
                opposing_token_id = None
                for tok in market_info.get("tokens", []):
                    tid = tok.get("token_id")
                    if tid and str(tid) != str(token_id):
                        opposing_token_id = str(tid)
                        break
                        
                # Leg-risk hedging only applies to two-sided market making positions (e.g., TwoSidedMMStrategy)
                # We do NOT want to hedge directional positions opened by OBI, News, Momentum, or Volume Spike strategies.
                is_mm_strategy = pos.get("strategy") in ["TwoSidedMMStrategy", "SpreadStrategy", "PMM"]
                if is_mm_strategy and opposing_token_id and opposing_token_id not in position_manager.positions:
                    # We hold token_id, but do NOT hold the opposing leg!
                    # Check if the position has been open for too long (e.g. 30 seconds = 3 loops at 10s polling)
                    entry_time = pos.get("entry_time", time.time())
                    if time.time() - entry_time >= 30:
                        opp_market_info = market_data_map.get(opposing_token_id)
                        if opp_market_info:
                            hedge_price = opp_market_info.get("best_ask") or opp_market_info.get("last_price") or 0.50
                            
                            # Check if we already have a pending BUY order for this opposing token
                            pending_buys = [o for o in execution_engine.pending_orders if o["token_id"] == opposing_token_id and o["side"] == "BUY"]
                            if pending_buys:
                                for o in pending_buys:
                                    await execution_engine.cancel_order(o["order_id"])
                            
                            logger.warning(
                                f"LEG-RISK HEDGE TRIGGERED: Holding {token_id[:20]} but missing {opposing_token_id[:20]}. "
                                f"Executing active taker hedge BUY on opposing leg at {hedge_price:.4f}."
                            )
                            
                            result = await execution_engine.place_limit_order(
                                token_id=opposing_token_id,
                                price=hedge_price,
                                size=pos["size"],
                                side="BUY",
                                market_data=opp_market_info,
                                is_aggressive=True,
                                is_emergency=True,
                                signal_delta=0,
                                signal_spread=opp_market_info.get("spread", 0)
                            )
                            if result['status'] == "SUCCESS":
                                loop_stats["executed"] += 1
                                position_manager.update_position(
                                    token_id=opposing_token_id,
                                    size=result.get('fill_size', pos["size"]),
                                    price=result.get('fill_price', hedge_price),
                                    side="BUY",
                                    strategy="HEDGE",
                                    spread=opp_market_info.get("spread", 0),
                                    slippage=result.get("slippage", 0),
                                    is_maker=False,
                                    fee=result.get("fee")
                                )
                                risk_manager.update_after_trade(success=True, token_id=opposing_token_id, strategy="HEDGE")

            # Update telemetry and check outcomes of rejections (Week 2B Paper Validation Mode)
            if PAPER_TRADING and paper_tracker:
                paper_tracker.update_telemetry(enriched_tokens)
                paper_tracker.process_rejections_tick(market_data_map)
                
                # Check for hour rollover to emit hourly validation report
                now_hour = datetime.datetime.now().hour
                if now_hour != current_hour:
                    paper_tracker.generate_hourly_report()
                    current_hour = now_hour

            # 4. Generate & Filter Signals
            signals, sig_stats = await signal_engine.generate_signals(enriched_tokens, risk_manager=risk_manager)
            loop_stats["generated"] = sig_stats["generated"]
            loop_stats["rejected_score"] = sig_stats["rejected_score"]
            
            for signal in signals:
                token_info = next((t for t in enriched_tokens if t.get("token_id") == signal["token_id"]), {})
                score  = signal.get('score', 0)
                tok_id = signal['token_id']

                # ----------------------------------------------------------
                # Blacklist gate — skip tokens with repeated drift failures
                # ----------------------------------------------------------
                if token_blacklist.is_blacklisted(tok_id):
                    loop_stats["rejected_noise"] += 1
                    continue

                # ----------------------------------------------------------
                # Requirement 4: Spike filter — reject corrupted large deltas
                # ----------------------------------------------------------
                if abs(signal.get('delta', 0)) > MAX_SIGNAL_DELTA:
                    logger.warning(
                        f"DELTA_SPIKE_REJECTED: {tok_id[:20]}... | "
                        f"delta={signal.get('delta'):.4f} > MAX={MAX_SIGNAL_DELTA} "
                        f"(data anomaly)"
                    )
                    loop_stats["rejected_noise"] += 1
                    continue

                # ----------------------------------------------------------
                # Requirement 1 + 3: Hybrid data gate
                #   WS fresh  → proceed as normal
                #   WS stale  → attempt REST fallback
                #   REST stale → block trade
                # ----------------------------------------------------------
                ws_age = ws_client.data_age_seconds
                ws_fresh = ws_client.is_data_fresh

                if ws_fresh:
                    live_market_data = token_info  # WS data is fresh, use it directly
                    data_source = "WS"
                else:
                    # WS stale — try REST override
                    live_market_data = await data_fetcher.get_validated_market_data(
                        tok_id, token_info
                    )
                    if live_market_data is None:
                        # REST also failed or stale — block trade
                        logger.warning(
                            f"DATA_STALE_BLOCK: {tok_id[:20]}... | "
                            f"WS age={ws_age:.1f}s, REST fallback unavailable. "
                            f"Trade blocked."
                        )
                        loop_stats["rejected_noise"] += 1
                        continue
                    data_source = live_market_data.get("source", "REST")

                # ----------------------------------------------------------
                # Requirement 2: Pre-trade validation via REST (always)
                # Re-fetch live price and check signal still coherent
                # ----------------------------------------------------------
                if PRE_TRADE_REVALIDATE:
                    validation = await data_fetcher.pre_trade_validate(signal)
                    if not validation["valid"]:
                        reason = validation.get('reason', '')
                        logger.warning(
                            f"PRE_TRADE_CANCELLED: {tok_id[:20]}... | "
                            f"{reason}"
                        )
                        # Record drift cancel — blacklist if threshold reached
                        if "drift" in reason.lower() or "Price drift" in reason:
                            drift_pct = validation.get("price_drift_pct", 0)
                            token_blacklist.record_drift_cancel(tok_id, drift_pct)
                        loop_stats["rejected_noise"] += 1
                        continue
                    # Update market data with freshest REST snapshot
                    live_market_data.update({
                        "best_bid": validation["rest_data"]["best_bid"],
                        "best_ask": validation["rest_data"]["best_ask"],
                        "spread":   validation["rest_data"]["spread"],
                    })

                # ----------------------------------------------------------
                # Safety Layer Check before trade entry
                # ----------------------------------------------------------
                if not daily_loss_ok:
                    logger.warning("SafetyLayer: Daily loss limit reached. Skipping new trade entries.")
                    loop_stats["rejected_noise"] += 1
                    continue

                if not ws_health_ok:
                    logger.warning("SafetyLayer: WebSocket data stale. Skipping new trade entries.")
                    loop_stats["rejected_noise"] += 1
                    continue

                # ----------------------------------------------------------
                # Risk Validation (uses freshly validated market data)
                # ----------------------------------------------------------
                if risk_manager.validate_trade(signal, live_market_data):
                    hourly_tracker.record_accepted()
                    dynamic_size = risk_manager.calculate_position_size(signal, live_market_data)
                    if dynamic_size <= 0:
                        loop_stats["rejected_noise"] += 1
                        continue
                    
                    # Apply asymmetric inventory skew to order price (Week 2B)
                    mid_price = (live_market_data.get("best_bid", 0.5) + live_market_data.get("best_ask", 0.5)) / 2
                    if mid_price <= 0:
                        mid_price = float(live_market_data.get("last_price") or 0.5)
                        
                    skew = risk_manager.calculate_inventory_skew(
                        tok_id, mid_price, signal['side'], 
                        total_capital=signal.get('capital_limit'), 
                        market_data=live_market_data
                    )
                    quoted_price = round(max(0.01, min(0.99, signal['price'] - skew)), 4)
                    
                    # Execution aggressiveness: taker for high conviction or news
                    is_aggressive = is_aggressive_signal(signal)
                    
                    # Safety check: Portfolio Exposure Cap
                    order_val = dynamic_size * quoted_price
                    if not await safety_layer.check_exposure_cap(order_val):
                        logger.warning(f"SafetyLayer: Order for {tok_id[:20]} blocked due to portfolio exposure cap.")
                        loop_stats["rejected_noise"] += 1
                        continue

                    # Execution
                    result = await execution_engine.place_limit_order(
                        token_id=tok_id,
                        price=quoted_price,
                        size=dynamic_size,
                        side=signal['side'],
                        market_data=live_market_data,
                        is_aggressive=is_aggressive,
                        signal_delta=signal.get('delta', 0),
                        signal_spread=signal.get('spread', 0),
                        strategy=signal.get('strategy')
                    )
                    
                    if result['status'] == "SUCCESS":
                        loop_stats["executed"] += 1
                        position_manager.update_position(
                            token_id=tok_id,
                            size=result.get('fill_size', dynamic_size),
                            price=result.get('fill_price', quoted_price),
                            side=signal['side'],
                            strategy=signal.get('strategy', 'Unknown'),
                            spread=live_market_data.get('spread', 0),
                            slippage=result.get("slippage", 0),
                            is_maker=result.get("is_maker", not is_aggressive),
                            order_id=result.get("order_id"),
                            fee=result.get("fee")
                        )
                        risk_manager.update_after_trade(success=True, token_id=tok_id, strategy=signal.get('strategy', 'Unknown'))
                        logger.info(
                            f"TRADE_EXECUTED: {tok_id[:20]}... | "
                            f"side={signal['side']} | data_source={data_source} | "
                            f"size={result.get('fill_size', dynamic_size):.2f} | price={result.get('fill_price', quoted_price):.4f}"
                        )

                    elif "profit" in result.get("error", "").lower():
                        loop_stats["rejected_expected_profit"] += 1
                else:
                    loop_stats["rejected_noise"] += 1
                    if PAPER_TRADING and paper_tracker:
                        mid_p = (live_market_data.get("best_bid", 0.5) + live_market_data.get("best_ask", 0.5)) / 2
                        paper_tracker.record_rejected_signal(signal, mid_p)
            
            # 5. Performance & Telemetry
            pnl_report = position_manager.calculate_pnl_report(enriched_tokens)
            
            logger.info("="*40)
            logger.info("LOOP SUMMARY:")
            logger.info(f"- markets scanned: {loop_stats['scanned']}")
            logger.info(f"- candidates selected: {loop_stats['selected']}")
            logger.info(f"- signals generated: {loop_stats['generated']}")
            logger.info(f"- signals rejected_noise: {loop_stats['rejected_noise']}")
            logger.info(f"- signals rejected_score: {loop_stats['rejected_score']}")
            logger.info(f"- signals rejected_expected_profit: {loop_stats['rejected_expected_profit']}")
            logger.info(f"- paper trades executed: {loop_stats['executed']}")
            logger.info(f"Net Realized PnL: {pnl_report['realized']:+.4f}")
            logger.info("="*40)

            # ── WS_SUBSCRIPTION_REPORT ──────────────────────────────────
            ws_snap = ws_client.get_ws_diagnostic_snapshot()
            logger.info(
                f"WS_SUBSCRIPTION_REPORT | "
                f"subscribed_tokens={ws_snap['subscribed_tokens']} | "
                f"cache_populated={ws_snap['cache_populated']} | "
                f"reconnects={ws_snap['reconnects']} | "
                f"last_sub_success={ws_snap['last_sub_success']} | "
                f"total_messages={ws_snap['total_messages']} | "
                f"messages_per_min={ws_snap['messages_per_min']} | "
                f"last_message_age={ws_snap['last_message_age_s']}s | "
                f"msg_types={ws_snap.get('msg_types', {})}"
            )

            # Hourly calibration metrics
            hourly_tracker.record_loop(loop_stats)
            hourly_tracker.tick()
            
            if loop_stats["executed"] > 0:
                loops_since_last_trade = 0
            else:
                loops_since_last_trade += 1

            await asyncio.sleep(POLLING_INTERVAL)
            
        except Exception as e:
            logger.exception("Error in main loop:")
            await asyncio.sleep(POLLING_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
