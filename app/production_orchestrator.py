import asyncio
import os
import sys
import time
import json
import signal
import datetime
import collections
from multiprocessing import Process, Event

# Make sure directory is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import (
    POLLING_INTERVAL, TOTAL_CAPITAL_POOL, PAPER_TRADING, TEST_MODE,
    PRE_TRADE_REVALIDATE, MAX_SIGNAL_DELTA, MARKET_COOLDOWN
)
from app.logger import logger
from app.database import Database
from app.redis_manager import RedisManager
from app.data_fetcher import DataFetcher
from app.market_filter import MarketFilter
from app.execution_engine import ExecutionEngine
from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.safety_layer import SafetyLayer
from app.websocket_client import WebsocketClient
from app.token_blacklist import TokenBlacklist

from strategies.two_sided_mm_strategy import TwoSidedMMStrategy
from strategies.news_strategy import NewsStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.volume_spike_strategy import VolumeSpikeStrategy
from strategies.order_book_imbalance_strategy import OrderBookImbalanceStrategy

# ----------------------------------------------------------------------
# RUNNERS FOR INDIVIDUAL SUB-PROCESSES
# ----------------------------------------------------------------------

def run_pmm_process(exit_event):
    """PMM Process: evaluates PMM strategy and pushes signals to Redis."""
    logger.info("Starting PMM process...")
    async def loop():
        db = Database()
        rm = RedisManager()
        await rm.connect()
        df = DataFetcher()
        mf = MarketFilter()
        strategy = TwoSidedMMStrategy()

        while not exit_event.is_set():
            try:
                markets = await df.fetch_markets()
                candidates = mf.filter_markets(markets)
                
                # Enrich candidate markets using cached data
                enriched = []
                for m in candidates[:30]:
                    tokens = m.get("tokens", [])
                    for token_data in tokens:
                        t_id = token_data.get("token_id")
                        if not t_id: continue
                        
                        ob = await df.get_orderbook(t_id)
                        if not ob: continue
                        
                        # Compute mid price and spread
                        raw_bids = ob.get("bids", [])
                        raw_asks = ob.get("asks", [])
                        if not raw_bids or not raw_asks: continue
                        
                        def get_price(l):
                            return float(l.get("price") or l.get("p") or 0.0) if isinstance(l, dict) else float(getattr(l, "price", 0.0))
                            
                        best_bid = get_price(raw_bids[0])
                        best_ask = get_price(raw_asks[0])
                        mid = (best_bid + best_ask) / 2.0
                        spread = (best_ask - best_bid) / mid if mid > 0 else 1.0
                        
                        token_market = m.copy()
                        token_market.update({
                            "token_id": t_id,
                            "orderbook": ob,
                            "last_price": mid,
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "spread": spread
                        })
                        enriched.append(token_market)
                
                if enriched:
                    signals = await strategy.evaluate(enriched)
                    for sig in signals:
                        logger.info(f"PMM Engine: Generated signal for {sig['token_id'][:20]}... {sig['side']}")
                        await rm.push_signal(sig)
                        
            except Exception as e:
                logger.error(f"PMM Process loop error: {e}")
            await asyncio.sleep(POLLING_INTERVAL)
            
        await rm.close()

    asyncio.run(loop())


def run_news_process(exit_event):
    """News & Twitter Process: evaluates breaking news alerts and sentiment."""
    logger.info("Starting News / Twitter process...")
    async def loop():
        db = Database()
        rm = RedisManager()
        await rm.connect()
        df = DataFetcher()
        strategy = NewsStrategy(poll_interval=15, data_fetcher=df)

        while not exit_event.is_set():
            try:
                # News strategy evaluates against candidate markets
                markets = await df.fetch_markets()
                
                # Check news strategy queue for mapped alerts
                signals = await strategy.evaluate(markets)
                for sig in signals:
                    logger.info(f"News Engine: Generated high-alpha signal: {sig['reason']}")
                    await rm.push_signal(sig)
                    
            except Exception as e:
                logger.error(f"News Process loop error: {e}")
            await asyncio.sleep(5) # Poll news alerts more frequently
            
        strategy.stop()
        await rm.close()

    asyncio.run(loop())


def run_obi_momentum_process(exit_event):
    """OBI & Momentum Process: evaluates order book imbalances and price momentum."""
    logger.info("Starting OBI / Momentum process...")
    async def loop():
        db = Database()
        rm = RedisManager()
        await rm.connect()
        df = DataFetcher()
        mf = MarketFilter()
        
        from app.config import MOMENTUM_THRESHOLD, MOMENTUM_MIN_LIQUIDITY
        strats = [
            MomentumStrategy(
                window_size=10, 
                momentum_threshold=MOMENTUM_THRESHOLD if not TEST_MODE else 0.001, 
                min_liquidity=MOMENTUM_MIN_LIQUIDITY
            ),
            VolumeSpikeStrategy(window_size=8, spike_threshold=3.0),
            OrderBookImbalanceStrategy()
        ]

        while not exit_event.is_set():
            try:
                markets = await df.fetch_markets()
                candidates = mf.filter_markets(markets)
                
                enriched = []
                for m in candidates[:30]:
                    tokens = m.get("tokens", [])
                    for token_data in tokens:
                        t_id = token_data.get("token_id")
                        if not t_id: continue
                        
                        ob = await df.get_orderbook(t_id)
                        if not ob: continue
                        
                        raw_bids = ob.get("bids", [])
                        raw_asks = ob.get("asks", [])
                        if not raw_bids or not raw_asks: continue
                        
                        def get_price(l):
                            return float(l.get("price") or l.get("p") or 0.0) if isinstance(l, dict) else float(getattr(l, "price", 0.0))
                        def get_size(l):
                            return float(l.get("size") or 0.0) if isinstance(l, dict) else float(getattr(l, "size", 0.0))
                            
                        best_bid = get_price(raw_bids[0])
                        best_ask = get_price(raw_asks[0])
                        bid_depth = sum(get_size(b) for b in raw_bids[:2]) * best_bid
                        ask_depth = sum(get_size(a) for a in raw_asks[:2]) * best_ask
                        mid = (best_bid + best_ask) / 2.0
                        spread = (best_ask - best_bid) / mid if mid > 0 else 1.0
                        
                        token_market = m.copy()
                        token_market.update({
                            "token_id": t_id,
                            "orderbook": ob,
                            "last_price": mid,
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "bid_depth": bid_depth,
                            "ask_depth": ask_depth,
                            "spread": spread
                        })
                        enriched.append(token_market)

                for strategy in strats:
                    signals = await strategy.evaluate(enriched)
                    for sig in signals:
                        logger.info(f"OBI/Momentum Engine: Generated signal from {strategy.name}: {sig['token_id'][:20]}")
                        await rm.push_signal(sig)
                        
            except Exception as e:
                logger.error(f"OBI/Momentum Process loop error: {e}")
            await asyncio.sleep(POLLING_INTERVAL)
            
        await rm.close()

    asyncio.run(loop())


# ----------------------------------------------------------------------
# COORDINATOR PROCESS (CONSUMER & ROUTER)
# ----------------------------------------------------------------------

class CoordinatorProcess:
    def __init__(self, exit_event):
        self.exit_event = exit_event
        self.db = Database()
        self.rm = RedisManager()
        self.df = DataFetcher()
        self.pm = PositionManager()
        self.risk = RiskManager(position_manager=self.pm)
        self.ee = ExecutionEngine(paper_trading=PAPER_TRADING)
        self.safety = SafetyLayer(self.pm, self.ee, self.rm)
        self.ws = WebsocketClient()
        self.df.set_websocket(self.ws)
        self.blacklist = TokenBlacklist()
        self.signal_buffer = defaultdict(list)  # (token_id, side): [signals]
        
        if PAPER_TRADING:
            from app.paper_validation_tracker import PaperValidationTracker
            self.tracker = PaperValidationTracker()
            self.ee.set_tracker(self.tracker)
        else:
            self.tracker = None

    async def run(self):
        logger.info("Starting Coordinator process...")
        await self.rm.connect()
        
        # Start Websocket in background
        asyncio.create_task(self.ws.connect())
        
        # Pre-subscribe to active tokens
        logger.info("Coordinator: Subscribing to active tokens...")
        initial_markets = await self.df.fetch_markets()
        active_tokens = []
        for m in (initial_markets if isinstance(initial_markets, list) else []):
            for tok in m.get("tokens", []):
                tid = tok.get("token_id")
                if tid: active_tokens.append(str(tid))
        await self.ws.subscribe(active_tokens[:200])

        # Spawn background loops
        asyncio.create_task(self.stale_order_watchdog())
        asyncio.create_task(self.buffer_flusher_loop())
        asyncio.create_task(self.resting_fills_loop())
        asyncio.create_task(self.telemetry_loop())

        # Main consumer loop
        while not self.exit_event.is_set():
            try:
                # Check Emergency Kill Switch
                if await self.safety.is_kill_switch_active():
                    await self.safety.trigger_emergency_stop()
                    logger.critical("Lockdown active. Suspending trading.")
                    await asyncio.sleep(5)
                    continue
                
                # Fetch signals from Redis Queue
                signal = await self.rm.pop_signal(timeout=1)
                if signal:
                    # Buffer signal to allow confirmations (500ms window)
                    key = (signal["token_id"], signal["side"])
                    self.signal_buffer[key].append(signal)
            except Exception as e:
                logger.error(f"Coordinator consumer error: {e}")
                await asyncio.sleep(1)

        await self.rm.close()

    async def buffer_flusher_loop(self):
        """Flushes the signal buffer every 500ms to process grouped confirmations."""
        while not self.exit_event.is_set():
            await asyncio.sleep(0.5)
            if not self.signal_buffer:
                continue

            current_buffer = dict(self.signal_buffer)
            self.signal_buffer.clear()

            # Process each group
            for (token_id, side), signals in current_buffer.items():
                try:
                    # Sort signals descending by strategy score weight, then signal score
                    # Resolve strategy scores: TwoSidedMMStrategy=5, NewsStrategy=20, etc.
                    # We can assign weights or fetch rankings
                    signals.sort(key=lambda s: s.get("score", 0), reverse=True)
                    best_signal = signals[0]

                    # Confirmation boost
                    confirmation_boost = (len(signals) - 1) * 15
                    best_signal["score"] = min(100, best_signal.get("score", 50) + confirmation_boost)

                    # Route and process the selected trade
                    asyncio.create_task(self.process_trade(best_signal))
                except Exception as e:
                    logger.error(f"Coordinator: Error flushing signals for {token_id}: {e}")

    async def process_trade(self, signal):
        """Executes risk checks and dispatches validated signals to ExecutionEngine."""
        token_id = signal["token_id"]
        score = signal["score"]

        # Blacklist check
        if self.blacklist.is_blacklisted(token_id):
            return

        # Fetch market info from local cache or REST fallback
        markets = await self.df.fetch_markets()
        market_info = next((m for m in markets if any(str(t.get("token_id")) == str(token_id) for t in m.get("tokens", []))), {})
        if not market_info:
            return

        # Build enriched target info for validation checks
        token_data = next((t for t in market_info.get("tokens", []) if str(t.get("token_id")) == str(token_id)), {})
        ob = await self.df.get_orderbook(token_id)
        if not ob: return

        raw_bids = ob.get("bids", [])
        raw_asks = ob.get("asks", [])
        if not raw_bids or not raw_asks: return

        def get_price(l):
            return float(l.get("price") or l.get("p") or 0.0) if isinstance(l, dict) else float(getattr(l, "price", 0.0))
        def get_size(l):
            return float(l.get("size") or 0.0) if isinstance(l, dict) else float(getattr(l, "size", 0.0))

        best_bid = get_price(raw_bids[0])
        best_ask = get_price(raw_asks[0])
        mid = (best_bid + best_ask) / 2.0
        spread = (best_ask - best_bid) / mid if mid > 0 else 1.0
        bid_depth = sum(get_size(b) for b in raw_bids[:2]) * best_bid
        ask_depth = sum(get_size(a) for a in raw_asks[:2]) * best_ask

        live_market_data = market_info.copy()
        live_market_data.update({
            "token_id": token_id,
            "orderbook": ob,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "spread": spread,
            "last_price": mid,
            "source": "WS"
        })

        # Daily loss safety check
        if not await self.safety.check_daily_loss():
            return

        # Exposure safety check
        if not await self.safety.check_exposure_cap():
            return

        # Pre-trade validation check
        if PRE_TRADE_REVALIDATE:
            val_res = await self.df.pre_trade_validate(signal)
            if not val_res["valid"]:
                if "drift" in val_res.get("reason", "").lower():
                    self.blacklist.record_drift_cancel(token_id, val_res.get("price_drift_pct", 0))
                return
            
            live_market_data.update({
                "best_bid": val_res["rest_data"]["best_bid"],
                "best_ask": val_res["rest_data"]["best_ask"],
                "spread":   val_res["rest_data"]["spread"],
            })

        # Validate Trade via RiskManager
        if self.risk.validate_trade(signal, live_market_data):
            dynamic_size = self.risk.calculate_position_size(signal, live_market_data)
            
            # Apply inventory price skew
            skew = self.risk.calculate_inventory_skew(
                token_id, mid, signal["side"], 
                total_capital=TOTAL_CAPITAL_POOL, 
                market_data=live_market_data
            )
            quoted_price = round(max(0.01, min(0.99, signal["price"] - skew)), 4)
            
            # Apply News override position sizing
            is_news = (signal.get("strategy") == "NewsStrategy")
            if is_news and score >= 70:
                allocation_pct = 0.30 + 0.10 * (score - 70) / 30
                capital_to_risk = TOTAL_CAPITAL_POOL * allocation_pct
                if self.tracker:
                    available_cash = self.tracker.virtual_cash
                else:
                    current_prices = {tid: pos['avg_price'] for tid, pos in self.pm.positions.items()}
                    total_exposure = sum(pos['size'] * current_prices.get(tid, pos['avg_price']) for tid, pos in self.pm.positions.items())
                    available_cash = max(0.0, TOTAL_CAPITAL_POOL - total_exposure)
                
                capital_to_risk = min(capital_to_risk, available_cash)
                entry_price = quoted_price if quoted_price > 0 else best_ask
                dynamic_size = round(capital_to_risk / entry_price, 2)
                is_aggressive = True
            else:
                is_aggressive = (score >= 85)

            # Order execution
            result = await self.ee.place_limit_order(
                token_id=token_id,
                price=quoted_price,
                size=dynamic_size,
                side=signal["side"],
                market_data=live_market_data,
                is_aggressive=is_aggressive,
                signal_delta=signal.get("delta", 0),
                signal_spread=signal.get("spread", 0),
                strategy=signal.get("strategy")
            )

            if result["status"] == "SUCCESS":
                pnl = self.pm.update_position(
                    token_id=token_id,
                    size=result.get("fill_size", dynamic_size),
                    price=result.get("fill_price", quoted_price),
                    side=signal["side"],
                    strategy=signal.get("strategy", "Unknown"),
                    spread=live_market_data.get("spread", 0),
                    slippage=result.get("slippage", 0),
                    is_maker=result.get("is_maker", not is_aggressive),
                    fee=result.get("fee")
                )
                self.risk.update_after_trade(success=True, token_id=token_id, pnl=pnl, strategy=signal.get("strategy"))
                logger.info(f"Coordinator: Executed order successfully. PnL: {pnl:+.4f}")

    async def stale_order_watchdog(self):
        """Monitors and cancels stale maker orders in background."""
        while not self.exit_event.is_set():
            try:
                await self.safety.run_stale_order_watchdog()
            except Exception as e:
                logger.error(f"Coordinator: Error in stale order watchdog: {e}")
            await asyncio.sleep(10)

    async def resting_fills_loop(self):
        """Periodically checks resting maker order fills and triggers exits or hedges."""
        while not self.exit_event.is_set():
            try:
                # 1. Fetch updated order books
                markets = await self.df.fetch_markets()
                market_data_map = {}
                for m in (markets if isinstance(markets, list) else []):
                    for tok in m.get("tokens", []):
                        tid = tok.get("token_id")
                        if tid:
                            market_data_map[str(tid)] = m

                # 2. Check limit fills
                fills = self.ee.check_resting_fills(market_data_map)
                for fill in fills:
                    pnl = self.pm.update_position(
                        token_id=fill["token_id"],
                        size=fill["fill_size"],
                        price=fill["fill_price"],
                        side=fill["side"],
                        strategy=fill.get("strategy", "PMM"),
                        spread=fill.get("spread", 0.0),
                        slippage=fill.get("slippage", 0.0),
                        is_maker=True,
                        fee=fill.get("fee")
                    )
                    self.risk.update_after_trade(success=True, token_id=fill["token_id"], pnl=pnl, strategy=fill.get("strategy", "PMM"))

                # 3. Check exits
                exits = self.pm.get_positions_for_exit(market_data_map)
                for exit_cand in exits:
                    tok_id = exit_cand["token_id"]
                    is_emergency = "SL" in exit_cand["reason"]
                    is_aggressive_exit = is_emergency or exit_cand.get("is_maker", True) is False
                    quoted_price = exit_cand["price"]
                    
                    logger.info(f"Coordinator: Triggering exit for {tok_id[:20]}... Reason: {exit_cand['reason']}")
                    result = await self.ee.place_limit_order(
                        token_id=tok_id,
                        price=quoted_price,
                        size=exit_cand["size"],
                        side="SELL",
                        market_data=market_data_map.get(tok_id),
                        is_aggressive=is_aggressive_exit,
                        is_emergency=is_emergency,
                        signal_delta=0,
                        signal_spread=exit_cand.get("spread", 0)
                    )
                    if result["status"] == "SUCCESS":
                        pnl = self.pm.update_position(
                            token_id=tok_id,
                            size=result.get("fill_size", exit_cand["size"]),
                            price=result.get("fill_price", quoted_price),
                            side="SELL",
                            strategy="EXIT_" + exit_cand["reason"].split()[0],
                            spread=exit_cand.get("spread", 0),
                            slippage=result.get("slippage", 0),
                            is_maker=result.get("is_maker", False),
                            fee=result.get("fee"),
                            exit_reason=exit_cand["reason"]
                        )
                        self.risk.update_after_trade(success=True, token_id=tok_id, pnl=pnl, strategy="EXIT_" + exit_cand["reason"].split()[0])

            except Exception as e:
                logger.error(f"Coordinator: Error in resting fills/exits loop: {e}")
            await asyncio.sleep(POLLING_INTERVAL)

    async def telemetry_loop(self):
        """Saves daily snapshots and runs paper validation telemetry logs."""
        while not self.exit_event.is_set():
            try:
                # Update snapshot
                markets = await self.df.fetch_markets()
                enriched = []
                for m in (markets if isinstance(markets, list) else []):
                    for tok in m.get("tokens", []):
                        tid = tok.get("token_id")
                        if tid:
                            nm = m.copy()
                            nm["token_id"] = str(tid)
                            enriched.append(nm)

                self.pm.update_inventory_metrics(enriched)
                
                # In paper trading, trigger telemetry
                if self.tracker:
                    self.tracker.update_telemetry(enriched)
                    
            except Exception as e:
                logger.error(f"Coordinator: Error in telemetry loop: {e}")
            await asyncio.sleep(60)


def run_coordinator_process(exit_event):
    """Coordinator Process: runs WS connection, pops signals, runs safety & executions."""
    coord = CoordinatorProcess(exit_event)
    asyncio.run(coord.run())


# ----------------------------------------------------------------------
# PROCESS MANAGER (SUPERVISOR & MONITOR)
# ----------------------------------------------------------------------

class MultiprocessSupervisor:
    def __init__(self):
        self.exit_event = Event()
        self.processes = {}  # name: (target_func, process_obj)

    def start_all(self):
        logger.info("Initializing Multiprocess Supervisor...")
        targets = {
            "Coordinator": run_coordinator_process,
            "PMM_Engine": run_pmm_process,
            "News_Engine": run_news_process,
            "OBI_Momentum_Engine": run_obi_momentum_process
        }

        for name, func in targets.items():
            p = Process(target=func, args=(self.exit_event,), name=name)
            p.daemon = True
            p.start()
            self.processes[name] = (func, p)
            logger.info(f"Supervisor: Started {name} (PID: {p.pid})")

    def monitor_loop(self):
        """Monitor child processes, restart crashed ones, handle shutdowns."""
        try:
            while not self.exit_event.is_set():
                time.sleep(5)
                for name, (func, p) in list(self.processes.items()):
                    if not p.is_alive():
                        logger.error(f"Supervisor: CRITICAL - Process {name} (PID: {p.pid}) has stopped. Exit code: {p.exitcode}")
                        
                        # Trigger auto-recovery restart
                        logger.warning(f"Supervisor: Auto-recovering {name}...")
                        new_p = Process(target=func, args=(self.exit_event,), name=name)
                        new_p.daemon = True
                        new_p.start()
                        self.processes[name] = (func, new_p)
                        logger.info(f"Supervisor: Restarted {name} (New PID: {new_p.pid})")

        except (KeyboardInterrupt, SystemExit):
            self.shutdown()

    def shutdown(self):
        logger.warning("Supervisor: Shutdown signal received. Terminating all processes...")
        self.exit_event.set()
        
        # Give processes a few seconds to exit gracefully
        time.sleep(2)
        for name, (_, p) in self.processes.items():
            if p.is_alive():
                logger.warning(f"Supervisor: Force-killing {name} (PID: {p.pid})")
                p.terminate()
                p.join()
                
        logger.info("Supervisor: All processes terminated successfully. Goodbye.")


def main():
    supervisor = MultiprocessSupervisor()
    
    # Register shutdown handlers
    def handle_signal(sig, frame):
        supervisor.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    supervisor.start_all()
    supervisor.monitor_loop()


if __name__ == "__main__":
    main()
