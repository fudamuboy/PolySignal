import asyncio
import sys
import os
import sqlite3

# Add parent directory to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.logger import logger
from app.database import Database
from app.signal_engine import SignalEngine
from app.risk_manager import RiskManager
from app.position_manager import PositionManager

# Import strategies
from strategies.momentum_strategy import MomentumStrategy
from strategies.volume_spike_strategy import VolumeSpikeStrategy
from strategies.order_book_imbalance_strategy import OrderBookImbalanceStrategy
from strategies.two_sided_mm_strategy import TwoSidedMMStrategy
from strategies.news_strategy import NewsStrategy

# Inject total capital pool config parameter if not imported
from app.config import TOTAL_CAPITAL_POOL

def setup_mock_db_with_trades():
    db = Database()
    db.clear_all_positions()
    
    conn = sqlite3.connect(db.db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trades;")
    
    # Mock trades schema: (token_id, side, size, price, strategy, realized_pnl, spread, slippage)
    # Let's insert mock trades representing different performance profiles:
    
    # 1. NewsStrategy: High performance (Profits, High EV, High win rate)
    # 5 trades: 4 wins ($10 each), 1 loss (-$5). Net PnL = +$35. Win Rate = 80%. EV = 7.0. PF = 40/5 = 8.0. DD = $5.
    news_trades = [
        ("tok_news_1", "BUY", 10.0, 0.50, "NewsStrategy", 10.0, 0.01, 0.0),
        ("tok_news_2", "BUY", 10.0, 0.50, "NewsStrategy", 10.0, 0.01, 0.0),
        ("tok_news_3", "BUY", 10.0, 0.50, "NewsStrategy", -5.0, 0.01, 0.0),
        ("tok_news_4", "BUY", 10.0, 0.50, "NewsStrategy", 10.0, 0.01, 0.0),
        ("tok_news_5", "BUY", 10.0, 0.50, "NewsStrategy", 10.0, 0.01, 0.0),
    ]
    
    # 2. TwoSidedMMStrategy: Steady, low drawdown, moderate performance
    # 6 trades: 4 wins ($3 each), 2 losses (-$2 each). Net PnL = +$8. Win Rate = 66.7%. EV = 1.33. PF = 12/4 = 3.0. DD = $2.
    pmm_trades = [
        ("tok_pmm_1", "BUY", 10.0, 0.50, "TwoSidedMMStrategy", 3.0, 0.01, 0.0),
        ("tok_pmm_2", "BUY", 10.0, 0.50, "TwoSidedMMStrategy", 3.0, 0.01, 0.0),
        ("tok_pmm_3", "BUY", 10.0, 0.50, "TwoSidedMMStrategy", -2.0, 0.01, 0.0),
        ("tok_pmm_4", "BUY", 10.0, 0.50, "TwoSidedMMStrategy", 3.0, 0.01, 0.0),
        ("tok_pmm_5", "BUY", 10.0, 0.50, "TwoSidedMMStrategy", -2.0, 0.01, 0.0),
        ("tok_pmm_6", "BUY", 10.0, 0.50, "TwoSidedMMStrategy", 3.0, 0.01, 0.0),
    ]
    
    # 3. OrderBookImbalanceStrategy: Good performance, high win rate, low drawdown
    # 4 trades: 3 wins ($6 each), 1 loss (-$1). Net PnL = +$17. Win Rate = 75%. EV = 4.25. PF = 18/1 = 18.0. DD = $1.
    obi_trades = [
        ("tok_obi_1", "BUY", 10.0, 0.50, "OrderBookImbalanceStrategy", 6.0, 0.01, 0.0),
        ("tok_obi_2", "BUY", 10.0, 0.50, "OrderBookImbalanceStrategy", 6.0, 0.01, 0.0),
        ("tok_obi_3", "BUY", 10.0, 0.50, "OrderBookImbalanceStrategy", -1.0, 0.01, 0.0),
        ("tok_obi_4", "BUY", 10.0, 0.50, "OrderBookImbalanceStrategy", 6.0, 0.01, 0.0),
    ]
    
    # 4. VolumeSpikeStrategy: Moderate performance, high drawdown
    # 4 trades: 2 wins ($8 each), 2 losses (-$5 each). Net PnL = +$6. Win Rate = 50%. EV = 1.5. PF = 16/10 = 1.6. DD = $10.
    vol_trades = [
        ("tok_vol_1", "BUY", 10.0, 0.50, "VolumeSpikeStrategy", 8.0, 0.01, 0.0),
        ("tok_vol_2", "BUY", 10.0, 0.50, "VolumeSpikeStrategy", -5.0, 0.01, 0.0),
        ("tok_vol_3", "BUY", 10.0, 0.50, "VolumeSpikeStrategy", -5.0, 0.01, 0.0),
        ("tok_vol_4", "BUY", 10.0, 0.50, "VolumeSpikeStrategy", 8.0, 0.01, 0.0),
    ]
    
    # 5. MomentumStrategy: Underperforming (Losses, negative EV, high drawdown)
    # 5 trades: 1 win ($2), 4 losses (-$3 each). Net PnL = -$10. Win Rate = 20%. EV = -2.0. PF = 2/12 = 0.17. DD = $12.
    mom_trades = [
        ("tok_mom_1", "BUY", 10.0, 0.50, "MomentumStrategy", -3.0, 0.01, 0.0),
        ("tok_mom_2", "BUY", 10.0, 0.50, "MomentumStrategy", 2.0, 0.01, 0.0),
        ("tok_mom_3", "BUY", 10.0, 0.50, "MomentumStrategy", -3.0, 0.01, 0.0),
        ("tok_mom_4", "BUY", 10.0, 0.50, "MomentumStrategy", -3.0, 0.01, 0.0),
        ("tok_mom_5", "BUY", 10.0, 0.50, "MomentumStrategy", -3.0, 0.01, 0.0),
    ]
    
    for t in news_trades + pmm_trades + obi_trades + vol_trades + mom_trades:
        cursor.execute("""
            INSERT INTO trades (token_id, side, size, price, strategy, realized_pnl, spread, slippage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, t)
        
    conn.commit()
    conn.close()
    logger.info("Mock trades successfully populated in storage/database.db")

async def run_validation():
    logger.info("Starting Multi-Engine Coordinator Dry-Run Validation...")
    
    # 1. Instantiate the 5 strategies
    strategies = [
        TwoSidedMMStrategy(),
        NewsStrategy(poll_interval=15),
        MomentumStrategy(window_size=8),
        VolumeSpikeStrategy(window_size=8),
        OrderBookImbalanceStrategy(imbalance_threshold=0.65)
    ]
    
    # Stop background polling task in NewsStrategy to keep test synchronous/clean
    for strat in strategies:
        if isinstance(strat, NewsStrategy):
            strat.active = False
            if hasattr(strat, "polling_task"):
                strat.polling_task.cancel()
                
    # 2. Test cold-start (no trades in database)
    logger.info("--- TEST 1: Cold Start (No Trades in Database) ---")
    db = Database()
    db.clear_all_positions()
    conn = sqlite3.connect(db.db_path)
    conn.execute("DELETE FROM trades;")
    conn.commit()
    conn.close()
    
    coordinator = SignalEngine(strategies, confidence_threshold=70, total_capital_pool=TOTAL_CAPITAL_POOL)
    
    # Verify equal split ($100 each for 5 strategies)
    for name in coordinator.strategy_weights:
        weight = coordinator.strategy_weights[name]
        limit = coordinator.capital_limits[name]
        assert abs(weight - 0.20) < 1e-4, f"Expected 0.20 weight for {name}, got {weight}"
        assert abs(limit - 100.0) < 1e-2, f"Expected $100 capital limit for {name}, got {limit}"
    logger.info("Test 1 Passed: Cold start rankings successfully assigned equal 20% weights and $100 capital limits.")
    
    # 3. Test dynamic performance ranking
    logger.info("--- TEST 2: Dynamic Performance Ranking with Trades ---")
    setup_mock_db_with_trades()
    
    coordinator.update_rankings()
    
    # Print rankings and assert correct order
    # Ranked by score descending:
    # 1. NewsStrategy / OrderBookImbalanceStrategy (high win rate, high EV, high pnl, low drawdown)
    # 5. MomentumStrategy (negative pnl, low win rate, high drawdown)
    sorted_strats = sorted(coordinator.strategy_scores.items(), key=lambda x: x[1], reverse=True)
    logger.info(f"Sorted scores: {sorted_strats}")
    
    best_strat = sorted_strats[0][0]
    worst_strat = sorted_strats[-1][0]
    
    assert best_strat in ["NewsStrategy", "OrderBookImbalanceStrategy"], f"Expected News or OBI as best strategy, got {best_strat}"
    assert worst_strat == "MomentumStrategy", f"Expected Momentum as worst strategy, got {worst_strat}"
    
    # Check that best strategy receives more capital than worst
    best_limit = coordinator.capital_limits[best_strat]
    worst_limit = coordinator.capital_limits[worst_strat]
    logger.info(f"Best Strategy ({best_strat}) Capital Limit: ${best_limit:.2f}")
    logger.info(f"Worst Strategy ({worst_strat}) Capital Limit: ${worst_limit:.2f}")
    assert best_limit > worst_limit, f"Best limit {best_limit} must be > worst limit {worst_limit}"
    logger.info("Test 2 Passed: Dynamic performance ranking and capital limits successfully calculated.")
    
    # 4. Test conflict resolution (Priority selection based on ranking score)
    logger.info("--- TEST 3: Performance-Based Conflict Resolution ---")
    
    # Mock signals on the same token (YES token) for both NewsStrategy (high rank) and MomentumStrategy (low rank)
    mock_market = [
        {
            "token_id": "tok_conflict_1",
            "best_bid": 0.50,
            "best_ask": 0.52,
            "last_price": 0.51,
            "spread": 0.02 / 0.51,
            "bid_depth": 500.0,
            "ask_depth": 500.0,
            "outcome": "Yes"
        }
    ]
    
    # Directly inject evaluated signals returning from strategies
    # NewsStrategy signal:
    sig_news = {
        "token_id": "tok_conflict_1",
        "price": 0.52,
        "size": 10.0,
        "side": "BUY",
        "delta": 0.05,
        "spread": 0.02,
        "liquidity": 1000.0,
        "score": 75.0,
        "strategy": "NewsStrategy",
        "reason": "Breaking news match"
    }
    # MomentumStrategy signal (with higher raw score, but lower strategy rank):
    sig_mom = {
        "token_id": "tok_conflict_1",
        "price": 0.52,
        "size": 10.0,
        "side": "BUY",
        "delta": 0.04,
        "spread": 0.02,
        "liquidity": 1000.0,
        "score": 90.0,
        "strategy": "MomentumStrategy",
        "reason": "Trend continuation breakout"
    }
    
    # We override the evaluate methods of the strategy instances to return our mock signals
    coordinator.strategies[0].evaluate = lambda m: asyncio.sleep(0, []) # TwoSidedMM
    coordinator.strategies[1].evaluate = lambda m: asyncio.sleep(0, [sig_news]) # News
    coordinator.strategies[2].evaluate = lambda m: asyncio.sleep(0, [sig_mom]) # Momentum
    coordinator.strategies[3].evaluate = lambda m: asyncio.sleep(0, []) # VolSpike
    coordinator.strategies[4].evaluate = lambda m: asyncio.sleep(0, []) # OBI
    
    final_signals, stats = await coordinator.generate_signals(mock_market)
    logger.info(f"Final resolved signals: {final_signals}")
    
    assert len(final_signals) == 1, f"Expected 1 signal after conflict resolution, got {len(final_signals)}"
    resolved_sig = final_signals[0]
    
    # The NewsStrategy signal has higher strategy performance rank (News score = 24 vs Momentum score = 6),
    # so NewsStrategy must win the conflict even though MomentumStrategy had a higher raw signal score (90 vs 75).
    assert resolved_sig["strategy"] == "NewsStrategy", f"Expected resolved signal strategy to be NewsStrategy, got {resolved_sig['strategy']}"
    # Score should receive a confirmation boost since 2 strategies agreed: 75 + 15 = 90
    assert resolved_sig["score"] == 90.0, f"Expected confirmation boosted score of 90.0, got {resolved_sig['score']}"
    logger.info("Test 3 Passed: Conflict resolution correctly prioritized the best performing engine signal.")

    # 5. Test Risk Manager dynamic capital check
    logger.info("--- TEST 4: Risk Manager Dynamic Capital Checks ---")
    pm = PositionManager()
    rm = RiskManager(position_manager=pm)
    
    # NewsStrategy signal has capital limit:
    sig_news_exceed = {
        "token_id": "tok_risk_1",
        "price": 0.50,
        "size": 400.0, # 400 * 0.5 = $200 order value. Exceeds News limit (max limit around $180)
        "side": "BUY",
        "delta": 0.05,
        "spread": 0.01,
        "liquidity": 1000.0,
        "score": 90.0,
        "strategy": "NewsStrategy",
        "capital_limit": coordinator.capital_limits["NewsStrategy"]
    }
    
    # Set a large base order size and trade cap to force the sizer to return a size exceeding limit
    rm.base_order_size = 1000.0
    rm.max_capital_per_trade = 1000.0
    
    market_data = {
        "token_id": "tok_risk_1",
        "best_bid": 0.49,
        "best_ask": 0.51,
        "last_price": 0.50,
        "spread": 0.02,
        "bid_depth": 5000.0,
        "ask_depth": 5000.0,
        "orderbook": {"bids": [{"price": 0.49, "size": 10000}], "asks": [{"price": 0.51, "size": 10000}]}
    }
    
    # NewsStrategy trade exceeding limit should be rejected
    is_valid = rm.validate_trade(sig_news_exceed, market_data)
    assert not is_valid, "Expected large order exceeding strategy limit to be rejected"
    assert rm.last_rejection_reason == "EXCEEDS_STRATEGY_CAPITAL", f"Expected rejection reason EXCEEDS_STRATEGY_CAPITAL, got {rm.last_rejection_reason}"
    logger.info("Test 4 Passed: Risk manager successfully enforced the dynamic strategy-specific capital limit.")
    
    logger.info("="*60)
    logger.info("   ALL MULTI-ENGINE COORDINATOR VALIDATION TESTS PASSED!")
    logger.info("="*60)

if __name__ == "__main__":
    asyncio.run(run_validation())
