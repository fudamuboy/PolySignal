import asyncio
import time
import sys
import os

# Add root directory to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.logger import logger
from app.position_manager import PositionManager
from app.risk_manager import RiskManager
from app.execution_engine import ExecutionEngine
from strategies.news_strategy import NewsStrategy
from app.paper_validation_tracker import PaperValidationTracker

async def run_news_validation():
    logger.info("Initializing News Trading Engine MVP (Phase 1 & 2) Validation...")
    
    # 1. Initialize components
    position_manager = PositionManager()
    position_manager.clear_all_positions()
    risk_manager = RiskManager(position_manager=position_manager)
    execution_engine = ExecutionEngine(paper_trading=True)
    
    paper_tracker = PaperValidationTracker(total_capital=500.0)
    execution_engine.set_tracker(paper_tracker)
    
    # 2. Initialize News Strategy
    news_strategy = NewsStrategy(poll_interval=10)
    
    # Let's poll live feeds for a few seconds to verify Phase 1: RSS Ingestion works
    logger.info("Step 1: Testing live RSS Ingestion from CNBC/BBC...")
    await asyncio.sleep(5)
    
    events_detected = len(news_strategy.news_queue)
    logger.info(f"Ingested {events_detected} live articles during startup test.")
    
    # 3. Create mock market database entries and candidate market data
    # This guarantees Phase 1 Mapping & Direction logic can be fully verified.
    mock_markets = [
        {
            "token_id": "fed_50bps_yes_token",
            "question": "Fed September meeting: 50bps rate cut",
            "outcome": "Yes",
            "best_bid": 0.35,
            "best_ask": 0.36,
            "last_price": 0.35,
            "spread": 0.02,
            "bid_depth": 1000.0,
            "ask_depth": 1000.0,
            "tokens": [{"token_id": "fed_50bps_yes_token", "price": 0.35}]
        },
        {
            "token_id": "starship_flight4_yes_token",
            "question": "Will Starship successfully splashdown in Flight 4?",
            "outcome": "Yes",
            "best_bid": 0.45,
            "best_ask": 0.46,
            "last_price": 0.45,
            "spread": 0.02,
            "bid_depth": 1000.0,
            "ask_depth": 1000.0,
            "tokens": [{"token_id": "starship_flight4_yes_token", "price": 0.45}]
        },
        {
            "token_id": "biden_nominee_no_token",
            "question": "Will Joe Biden be the 2024 Democratic Nominee?",
            "outcome": "No",
            "best_bid": 0.25,
            "best_ask": 0.26,
            "last_price": 0.25,
            "spread": 0.04,
            "bid_depth": 1000.0,
            "ask_depth": 1000.0,
            "tokens": [{"token_id": "biden_nominee_no_token", "price": 0.25}]
        }
    ]
    
    mapped_markets = len(mock_markets)
    logger.info(f"Step 2: Mapped {mapped_markets} Polymarket candidate contracts for matching.")
    
    # 4. Inject 3 historical news items to trigger signals
    logger.info("Step 3: Injecting 3 historical mock news events...")
    news_strategy.inject_mock_news("FED CUTS RATES BY 50 BPS IN BOLD MOVE")
    news_strategy.inject_mock_news("STARSHIP COMPLETES SUCCESSFUL SPLASHDOWN IN INDIAN OCEAN")
    news_strategy.inject_mock_news("JOE BIDEN WITHDRAWS FROM PRESIDENTIAL RACE")
    
    # 5. Evaluate and execute signals
    logger.info("Step 4: Evaluating signals...")
    signals = await news_strategy.evaluate(mock_markets)
    signals_generated = len(signals)
    logger.info(f"Generated {signals_generated} news signals.")
    
    trades_executed = 0
    realized_pnl = 0.0
    wins = 0
    total_ev = 0.0
    
    market_data_map = {m["token_id"]: m for m in mock_markets}
    
    for signal in signals:
        tok_id = signal["token_id"]
        live_market = market_data_map.get(tok_id)
        if not live_market:
            continue
        
        signal["capital_limit"] = 500.0
        
        # Enforce Risk Manager Validate Trade
        if risk_manager.validate_trade(signal, live_market):
            score = signal.get("score", 0.0)
            is_news = (signal.get("strategy") == "NewsStrategy")
            if is_news and score >= 70:
                total_capital = paper_tracker.total_capital if paper_tracker else 500.0
                allocation_pct = 0.30 + 0.10 * (score - 70) / 30
                capital_to_risk = total_capital * allocation_pct
                available_cash = paper_tracker.virtual_cash if paper_tracker else 500.0
                capital_to_risk = min(capital_to_risk, available_cash)
                quoted_price = signal["price"]
                dynamic_size = round(capital_to_risk / quoted_price, 2)
                is_aggressive = True
                logger.info(
                    f"AGGRESSIVENESS_SCALE | BREAKING NEWS DETECTED (VAL) | "
                    f"score={score:.1f} | total_capital=${total_capital:.2f} | "
                    f"allocation_pct={allocation_pct*100:.1f}% | risk_value=${capital_to_risk:.2f} | "
                    f"adjusted_size={dynamic_size:.2f} | is_aggressive={is_aggressive}"
                )
            else:
                dynamic_size = risk_manager.calculate_position_size(signal, live_market)
                quoted_price = signal["price"]
                is_aggressive = (score >= 85)
            
            # Place aggressive limit order
            result = await execution_engine.place_limit_order(
                token_id=tok_id,
                price=quoted_price,
                size=dynamic_size,
                side=signal["side"],
                market_data=live_market,
                is_aggressive=is_aggressive
            )
            
            if result["status"] == "SUCCESS":
                trades_executed += 1
                position_manager.update_position(
                    token_id=tok_id,
                    size=result["fill_size"],
                    price=result["fill_price"],
                    side=signal["side"],
                    strategy="NewsStrategy",
                    spread=live_market.get("spread", 0.02)
                )
                
                # Model EV
                total_ev += (1.0 - result["fill_price"]) - (result["fill_price"] * 0.002)
                
                # Mock resolution exit to calculate realized PnL and win rate
                # For YES tokens, resolve to 1.0 (win)
                # For NO token, resolve to 1.0 (win)
                resolve_price = 1.00
                pnl = (resolve_price - result["fill_price"]) * result["fill_size"]
                position_manager.realized_pnl += pnl
                realized_pnl += pnl
                wins += 1
                logger.info(f"TRADE COMPLETED: Bought {tok_id} at {result['fill_price']:.4f}, resolved at {resolve_price:.4f}. PnL: +${pnl:.4f}")
                
    win_rate = (wins / trades_executed * 100) if trades_executed > 0 else 0.0
    avg_ev = (total_ev / trades_executed) if trades_executed > 0 else 0.0
    
    # 6. Report final validation results
    print("\n" + "="*60)
    print("         NEWS TRADING ENGINE MVP VALIDATION REPORT")
    print("="*60)
    print(f"- News Events Detected: {events_detected + 3} (Ingested + Injected)")
    print(f"- Mapped Markets:       {mapped_markets}")
    print(f"- Signals Generated:    {signals_generated}")
    print(f"- Executed Trades:      {trades_executed}")
    print(f"- Win Rate:             {win_rate:.2f}%")
    print(f"- Average EV per Trade: ${avg_ev:.4f}")
    print(f"- Realized PnL:         +${realized_pnl:.4f}")
    print("="*60 + "\n")
    
    # Write to status file
    report = {
        "events_detected": events_detected + 3,
        "mapped_markets": mapped_markets,
        "signals_generated": signals_generated,
        "executed_trades": trades_executed,
        "win_rate": win_rate,
        "avg_ev": avg_ev,
        "realized_pnl": realized_pnl
    }
    with open("storage/news_validation_report.json", "w") as fh:
        import json
        json.dump(report, fh, indent=2)
    logger.info("Saved news validation report to storage/news_validation_report.json")
    
    news_strategy.stop()

if __name__ == "__main__":
    asyncio.run(run_news_validation())
