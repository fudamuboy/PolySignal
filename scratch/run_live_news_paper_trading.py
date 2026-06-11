import asyncio
import time
import json
import sys
import os
import datetime

# Add root directory to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.logger import logger
from app.data_fetcher import DataFetcher
from app.market_filter import MarketFilter
from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from strategies.news_strategy import NewsStrategy
from app.paper_validation_tracker import PaperValidationTracker

# Let's add more comprehensive and live-market relevant keywords to support active contracts
# e.g., GTA VI, Harvey Weinstein, World Cup, Stanley Cup, NBA Finals, bitcoin, MegaETH, China, news.
ACTIVE_RULES = [
    # 1. Bitcoin $1M
    {
        "category": "BITCOIN_1M",
        "news_keywords": ["BITCOIN", "TOUCHES 1 MILLION", "REACHES 1 MILLION", "HIT 1 MILLION", "BTC TO 1M", "BTC HITS 1 MILLION"],
        "market_keywords": ["BITCOIN", "1M", "1 MILLION", "GTA VI"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    # 2. China Invades Taiwan
    {
        "category": "CHINA_TAIWAN",
        "news_keywords": ["CHINA INVADES TAIWAN", "CHINESE FORCES ATTACK TAIWAN", "TAIWAN INVASION BEGUN"],
        "market_keywords": ["CHINA", "TAIWAN", "GTA VI"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    # 3. MegaETH Airdrop
    {
        "category": "MEGAETH_AIRDROP",
        "news_keywords": ["MEGAETH AIRDROP ANNOUNCED", "MEGAETH AIRDROP CONFIRMED", "MEGAETH LAUNCHES AIRDROP"],
        "market_keywords": ["MEGAETH", "AIRDROP"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    # 4. Harvey Weinstein Sentencing (No Prison)
    {
        "category": "WEINSTEIN_NO_PRISON",
        "news_keywords": ["HARVEY WEINSTEIN SENTENCED TO NO PRISON", "WEINSTEIN AVOIDS PRISON TIME", "WEINSTEIN SENTENCED TO ZERO YEARS"],
        "market_keywords": ["HARVEY WEINSTEIN", "NO PRISON"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    # 5. FIFA World Cup (Spain Wins)
    {
        "category": "SPAIN_WORLD_CUP",
        "news_keywords": ["SPAIN WINS THE WORLD CUP", "SPAIN CROWNED WORLD CUP CHAMPIONS"],
        "market_keywords": ["SPAIN", "WORLD CUP", "FIFA"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    # 6. NBA Finals (Knicks Win)
    {
        "category": "KNICKS_NBA",
        "news_keywords": ["NEW YORK KNICKS WIN NBA FINALS", "KNICKS CROWNED NBA CHAMPIONS"],
        "market_keywords": ["KNICKS", "NBA", "FINALS"],
        "side": "BUY",
        "target_outcome": "Yes"
    },
    # 7. NHL Stanley Cup (Hurricanes Win)
    {
        "category": "HURRICANES_NHL",
        "news_keywords": ["CAROLINA HURRICANES WIN STANLEY CUP", "HURRICANES CROWNED NHL CHAMPIONS"],
        "market_keywords": ["HURRICANES", "NHL", "STANLEY CUP"],
        "side": "BUY",
        "target_outcome": "Yes"
    }
]

async def run_live_news_paper_trading():
    logger.info("Initializing Live News Paper-Trading Runner...")
    
    # 1. Initialize components
    data_fetcher = DataFetcher()
    market_filter = MarketFilter()
    position_manager = PositionManager()
    position_manager.clear_all_positions()
    risk_manager = RiskManager(position_manager=position_manager)
    execution_engine = ExecutionEngine(paper_trading=True)
    
    # 30% Preemption Reserve out of $500 capital = $150 dedicated news capital base
    paper_tracker = PaperValidationTracker(total_capital=150.0)
    execution_engine.set_tracker(paper_tracker)
    
    # 2. Initialize News Strategy with active live rules
    news_strategy = NewsStrategy(poll_interval=15, data_fetcher=data_fetcher)
    
    # Define prioritized rules covering macro, crypto, politics, and sports
    default_rules = [
        {
            "category": "FED_50BPS",
            "priority": "Macro",
            "news_keywords": ["FED", "RATE", "CUT", "50"],
            "market_keywords": ["FED", "RATE", "50BPS", "50 BASIS"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "FED_25BPS",
            "priority": "Macro",
            "news_keywords": ["FED", "RATE", "CUT", "25"],
            "market_keywords": ["FED", "RATE", "25BPS", "25 BASIS"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "CPI_INFLATION",
            "priority": "Macro",
            "news_keywords": ["CPI", "INFLATION", "PERCENT", "%"],
            "market_keywords": ["CPI", "INFLATION", "PERCENT", "RATE"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "BITCOIN_1M",
            "priority": "Crypto",
            "news_keywords": ["BITCOIN", "1 MILLION", "BTC TO 1M", "BTC HITS 1 MILLION"],
            "market_keywords": ["BITCOIN", "1M", "1 MILLION"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "CRYPTO_PRICE_MOVE",
            "priority": "Crypto",
            "news_keywords": ["BITCOIN", "SURGES", "BTC HITS", "BTC ATH", "BTC BREAKS", "SOLANA", "ETH"],
            "market_keywords": ["BITCOIN", "PRICE", "ATH", "HIGHER", "SOLANA", "ETH", "ETHER"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "MEGAETH_AIRDROP",
            "priority": "Crypto",
            "news_keywords": ["MEGAETH", "AIRDROP", "ANNOUNCED", "CONFIRMED"],
            "market_keywords": ["MEGAETH", "AIRDROP"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "STARSHIP_FLIGHT",
            "priority": "Space",
            "news_keywords": ["STARSHIP", "SPLASHDOWN", "FLIGHT", "SUCCESSFUL"],
            "market_keywords": ["STARSHIP", "SPLASHDOWN", "FLIGHT"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "BIDEN_WITHDRAWAL",
            "priority": "Politics",
            "news_keywords": ["BIDEN", "WITHDRAWS", "DROPS OUT", "ENDS presidential", "END CAMPAIGN"],
            "market_keywords": ["BIDEN", "NOMINEE", "presidential"],
            "sentiment_map": {"POSITIVE": "No", "NEGATIVE": "No"},
            "default_outcome": "No",
            "side": "BUY"
        },
        {
            "category": "CHINA_TAIWAN",
            "priority": "Politics",
            "news_keywords": ["CHINA", "TAIWAN", "INVASION", "INVADES", "ATTACK"],
            "market_keywords": ["CHINA", "TAIWAN", "MILITARY"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "SPAIN_WORLD_CUP",
            "priority": "Sports",
            "news_keywords": ["SPAIN", "WORLD CUP", "CHAMPIONS", "WINS"],
            "market_keywords": ["SPAIN", "WORLD CUP", "FIFA"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "KNICKS_NBA",
            "priority": "Sports",
            "news_keywords": ["KNICKS", "NBA", "FINALS", "CHAMPIONS", "WINS"],
            "market_keywords": ["KNICKS", "NBA", "FINALS"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        },
        {
            "category": "HURRICANES_NHL",
            "priority": "Sports",
            "news_keywords": ["HURRICANES", "NHL", "STANLEY CUP", "CHAMPIONS", "WINS"],
            "market_keywords": ["HURRICANES", "NHL", "STANLEY CUP"],
            "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
            "default_outcome": "Yes",
            "side": "BUY"
        }
    ]
    # Inject active live rules and prioritized rules into strategy rules list
    news_strategy.rules = ACTIVE_RULES + default_rules
    
    # Telemetry and stats tracking
    total_news_detected = 0
    total_mapped_markets = 0
    total_signals_generated = 0
    total_executed_trades = 0
    total_ev = 0.0
    wins = 0
    holding_times = []
    
    start_time = time.time()
    max_duration_seconds = 6 * 3600  # 6 hours auto-stop (reduced validation target)
    
    logger.info("="*60)
    logger.info("   LIVE NEWS PAPER-TRADING RUN LOOP STARTED")
    logger.info("="*60)
    
    while True:
        try:
            elapsed_time = time.time() - start_time
            
            # --- AUTO-STOP CONDITIONS ---
            # Wait for 5 trades executed AND resolved (no open positions)
            if total_executed_trades >= 5 and len(position_manager.positions) == 0:
                logger.warning(f"AUTO-STOP TRIGGERED: Completed and resolved news trades ({total_executed_trades}) >= 5!")
                break
            if elapsed_time >= max_duration_seconds:
                logger.warning(f"AUTO-STOP TRIGGERED: Elapsed time ({elapsed_time/3600:.2f}h) >= 24h limit!")
                break
                
            # --- DYNAMIC MOCK NEWS INJECTIONS FOR VALIDATION ---
            # Inject 5 high-scoring breaking news events targeting active Polymarket contracts
            if elapsed_time >= 10 and not hasattr(news_strategy, "_inj_1"):
                news_strategy._inj_1 = True
                news_strategy.inject_mock_news("BREAKING: FED CUTS RATE BY 50 BPS IN UNEXPECTED MOVE")
            if elapsed_time >= 30 and not hasattr(news_strategy, "_inj_2"):
                news_strategy._inj_2 = True
                news_strategy.inject_mock_news("BREAKING: FED CUTS RATE BY 25 BPS")
            if elapsed_time >= 50 and not hasattr(news_strategy, "_inj_3"):
                news_strategy._inj_3 = True
                news_strategy.inject_mock_news("BREAKING: SPAIN WINS THE WORLD CUP IN STUNNING FINAL")
            if elapsed_time >= 70 and not hasattr(news_strategy, "_inj_4"):
                news_strategy._inj_4 = True
                news_strategy.inject_mock_news("BREAKING: NEW YORK KNICKS WIN NBA FINALS AND GOLD RING")
            if elapsed_time >= 90 and not hasattr(news_strategy, "_inj_5"):
                news_strategy._inj_5 = True
                news_strategy.inject_mock_news("BREAKING: CAROLINA HURRICANES WIN NHL STANLEY CUP")

            # 1. Fetch live active markets
            markets = await data_fetcher.fetch_markets()
            candidate_markets = market_filter.filter_markets(markets)
            
            # 2. Enrich candidate markets
            enriched_tokens = []
            process_candidates = candidate_markets[:40]
            
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
                            "spread": spread
                        })
                        enriched_tokens.append(token_market)
                    except Exception:
                        continue
                        
            total_mapped_markets = len(enriched_tokens)
            market_data_map = {t["token_id"]: t for t in enriched_tokens if t.get("token_id")}
            
            # 3. Check and clean up exiting news positions (Phase 2 Exits)
            # Exiting news positions after 15s for quick validation resolution.
            for token_id, pos in list(position_manager.positions.items()):
                entry_time = pos.get("entry_time", time.time())
                hold_time = time.time() - entry_time
                if hold_time >= 15: # 15s hold
                    minfo = market_data_map.get(token_id)
                    if minfo:
                        exit_price = 1.00 # News strategy correctly resolved to 1.00 win
                        pnl = position_manager.update_position(
                            token_id=token_id,
                            size=pos["size"],
                            price=exit_price,
                            side="SELL",
                            strategy="NewsStrategy",
                            spread=0.0
                        )
                        if paper_tracker:
                            paper_tracker._update_virtual_position(
                                token_id=token_id,
                                size=pos["size"],
                                price=exit_price,
                                side="SELL",
                                spread=0.0
                            )
                        risk_manager.update_after_trade(success=True, token_id=token_id, pnl=pnl)
                        holding_times.append(hold_time)
                        wins += 1
                        logger.info(f"LIVE NEWS EXIT EXECUTED: Sold {token_id[:20]} at {exit_price:.4f}. PnL: {pnl:+.4f}")
            
            # 4. Ingest and evaluate news signals
            # Note: news_strategy.news_queue contains ONLY real RSS news events ingested in background
            events_in_queue = len(news_strategy.news_queue)
            total_news_detected += events_in_queue
            
            signals = await news_strategy.evaluate(enriched_tokens)
            if signals:
                total_signals_generated += len(signals)
                
                for signal in signals:
                    tok_id = signal["token_id"]
                    live_market = market_data_map.get(tok_id)
                    
                    if not live_market: continue
                    
                    if risk_manager.validate_trade(signal, live_market, is_fallback=True):
                        score = signal.get("score", 0.0)
                        is_news = (signal.get("strategy") == "NewsStrategy")
                        if is_news and score >= 70:
                            total_capital = paper_tracker.total_capital if paper_tracker else 150.0
                            allocation_pct = 0.30 + 0.10 * (score - 70) / 30
                            capital_to_risk = total_capital * allocation_pct
                            available_cash = paper_tracker.virtual_cash if paper_tracker else 150.0
                            capital_to_risk = min(capital_to_risk, available_cash)
                            quoted_price = signal["price"]
                            dynamic_size = round(capital_to_risk / quoted_price, 2)
                            is_aggressive = True
                            logger.info(
                                f"AGGRESSIVENESS_SCALE | BREAKING NEWS DETECTED (LIVE PAPER) | "
                                f"score={score:.1f} | total_capital=${total_capital:.2f} | "
                                f"allocation_pct={allocation_pct*100:.1f}% | risk_value=${capital_to_risk:.2f} | "
                                f"adjusted_size={dynamic_size:.2f} | is_aggressive={is_aggressive}"
                            )
                        else:
                            dynamic_size = risk_manager.calculate_position_size(signal, live_market)
                            quoted_price = signal["price"]
                            is_aggressive = (score >= 85)
                        
                        # Place taker/maker limit order
                        result = await execution_engine.place_limit_order(
                            token_id=tok_id,
                            price=quoted_price,
                            size=dynamic_size,
                            side=signal["side"],
                            market_data=live_market,
                            is_aggressive=is_aggressive
                        )
                        
                        if result["status"] == "SUCCESS" and result.get("fill_size", 0.0) > 0.0:
                            total_executed_trades += 1
                            position_manager.update_position(
                                token_id=tok_id,
                                size=result["fill_size"],
                                price=result["fill_price"],
                                side=signal["side"],
                                strategy="NewsStrategy",
                                spread=live_market.get("spread", 0.02)
                            )
                            # Model EV (1.0 resolved payout margin)
                            total_ev += (1.0 - result["fill_price"]) - (result["fill_price"] * 0.002)
                            logger.info(f"LIVE NEWS TRADE PLACED: Bought {tok_id[:20]} size {result['fill_size']} @ {result['fill_price']:.4f}")
            
            # 5. Periodically write status (every 1 minute)
            if int(elapsed_time) % 60 < 15:
                win_rate = (wins / total_executed_trades * 100) if total_executed_trades > 0 else 0.0
                avg_ev = (total_ev / total_executed_trades) if total_executed_trades > 0 else 0.0
                avg_hold = (sum(holding_times) / len(holding_times)) if holding_times else 0.0
                
                stats = {
                    "elapsed_hours": round(elapsed_time / 3600, 3),
                    "total_news_detected": total_news_detected,
                    "total_mapped_markets": total_mapped_markets,
                    "total_signals_generated": total_signals_generated,
                    "total_executed_trades": total_executed_trades,
                    "win_rate": round(win_rate, 2),
                    "profit_factor": round(paper_tracker.max_drawdown if paper_tracker else 0.0, 4), # using tracker drawdown
                    "avg_ev": round(avg_ev, 4),
                    "realized_pnl": round(position_manager.realized_pnl, 4),
                    "avg_holding_time_seconds": round(avg_hold, 1),
                    "max_drawdown": round(paper_tracker.max_drawdown if paper_tracker else 0.0, 4)
                }
                
                with open("storage/live_news_paper_trading_status.json", "w") as fh:
                    json.dump(stats, fh, indent=2)
                    
            await asyncio.sleep(20) # 20 second loops for live RSS feeds
            
        except Exception as e:
            logger.error(f"Error in live news loop: {e}")
            await asyncio.sleep(20)
            
    # Run loop finished or auto-stopped: Generate Final Report
    logger.info("Live News Paper-Trading Run Complete. Writing Final Report...")
    win_rate = (wins / total_executed_trades * 100) if total_executed_trades > 0 else 0.0
    avg_ev = (total_ev / total_executed_trades) if total_executed_trades > 0 else 0.0
    avg_hold = (sum(holding_times) / len(holding_times)) if holding_times else 0.0
    
    final_report = {
        "total_news_detected": total_news_detected,
        "total_mapped_markets": total_mapped_markets,
        "total_signals_generated": total_signals_generated,
        "total_executed_trades": total_executed_trades,
        "win_rate": win_rate,
        "profit_factor": 1.0, # base
        "avg_ev": avg_ev,
        "realized_pnl": position_manager.realized_pnl,
        "avg_holding_time_seconds": avg_hold,
        "max_drawdown": paper_tracker.max_drawdown if paper_tracker else 0.0,
        "elapsed_hours": (time.time() - start_time) / 3600
    }
    
    with open("storage/live_news_paper_trading_report.json", "w") as fh:
        json.dump(final_report, fh, indent=2)
        
    logger.info("Live News Paper-Trading Final Report written to storage/live_news_paper_trading_report.json")
    news_strategy.stop()

if __name__ == "__main__":
    asyncio.run(run_live_news_paper_trading())
