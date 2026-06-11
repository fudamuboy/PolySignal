import sqlite3
from .config import TEST_MODE, GLOBAL_MIN_SCORE
from .logger import logger
from .database import Database

class SignalEngine:
    def __init__(self, strategies, confidence_threshold=70, total_capital_pool=500.0):
        self.strategies = strategies
        self.confidence_threshold = confidence_threshold
        self.total_capital_pool = total_capital_pool
        self.db = Database()
        
        # Initialize rankings and weights mapping
        self.strategy_scores = {}
        self.strategy_weights = {}
        self.capital_limits = {}
        
        # Initial call to set defaults
        self.update_rankings()

    def update_rankings(self):
        """Query performance metrics from the database and dynamically rank engines."""
        try:
            perf = self.db.get_engine_performance()
        except Exception as e:
            logger.error(f"Error fetching engine performance: {e}")
            perf = {}

        strategy_names = [s.name for s in self.strategies]
        if not strategy_names:
            return

        # Complete performance data with defaults for any active strategies not yet in the DB
        full_perf = {}
        for name in strategy_names:
            if name in perf:
                full_perf[name] = perf[name]
            else:
                full_perf[name] = {
                    "total_trades": 0,
                    "realized_pnl": 0.0,
                    "win_rate": 0.0,
                    "ev": 0.0,
                    "profit_factor": 1.0,
                    "max_drawdown": 0.0
                }

        # Extracted metrics dicts for sorting
        pnls = {name: full_perf[name]["realized_pnl"] for name in strategy_names}
        evs = {name: full_perf[name]["ev"] for name in strategy_names}
        win_rates = {name: full_perf[name]["win_rate"] for name in strategy_names}
        profit_factors = {name: full_perf[name]["profit_factor"] for name in strategy_names}
        drawdowns = {name: full_perf[name]["max_drawdown"] for name in strategy_names}

        # Rank helper function (higher values = higher rank score, best gets N points, worst gets 1 point)
        def rank_metric(metric_dict, reverse=True):
            sorted_items = sorted(metric_dict.items(), key=lambda x: x[1], reverse=reverse)
            ranks = {}
            for i, (name, val) in enumerate(sorted_items):
                if i > 0 and val == sorted_items[i-1][1]:
                    ranks[name] = ranks[sorted_items[i-1][0]]
                else:
                    ranks[name] = len(sorted_items) - i
            return ranks

        # Rank each metric (drawdown: lower is better, so reverse=False)
        rank_pnl = rank_metric(pnls, reverse=True)
        rank_ev = rank_metric(evs, reverse=True)
        rank_win = rank_metric(win_rates, reverse=True)
        rank_pf = rank_metric(profit_factors, reverse=True)
        rank_dd = rank_metric(drawdowns, reverse=False)

        # Aggregate total scores
        self.strategy_scores = {}
        for name in strategy_names:
            self.strategy_scores[name] = (
                rank_pnl[name] +
                rank_ev[name] +
                rank_win[name] +
                rank_pf[name] +
                rank_dd[name]
            )

        # Calculate dynamic capital weights
        # Base weight: 5% (0.05) per strategy to prevent capital starvation.
        # Remaining 75% distributed proportionally to performance score.
        base_weight = 0.05
        num_strats = len(strategy_names)
        remaining_distribution_weight = 1.0 - (base_weight * num_strats)
        
        sum_scores = sum(self.strategy_scores.values())
        
        self.strategy_weights = {}
        self.capital_limits = {}
        for name in strategy_names:
            score_fraction = (self.strategy_scores[name] / sum_scores) if sum_scores > 0 else (1.0 / num_strats)
            weight = base_weight + (remaining_distribution_weight * score_fraction)
            self.strategy_weights[name] = round(weight, 4)
            self.capital_limits[name] = round(weight * self.total_capital_pool, 2)

        # Log rankings and weights
        logger.info("="*60)
        logger.info("ENGINE COORDINATOR PERFORMANCE RANKINGS:")
        for name in sorted(strategy_names, key=lambda x: self.strategy_scores[x], reverse=True):
            stats = full_perf[name]
            logger.info(
                f"- {name:28} | Score: {self.strategy_scores[name]:2d} | "
                f"Weight: {self.strategy_weights[name]*100:5.2f}% | "
                f"Cap Limit: ${self.capital_limits[name]:6.2f} | "
                f"PnL: {stats['realized_pnl']:+6.2f} | EV: {stats['ev']:+6.3f} | "
                f"Win: {stats['win_rate']*100:5.1f}% | PF: {stats['profit_factor']:.2f} | "
                f"DD: {stats['max_drawdown']:.2f}"
            )
        logger.info("="*60)

    async def generate_signals(self, market_data, inactivity_count=0, risk_manager=None):
        """Evaluate all strategies and combine signals with scoring."""
        # Update rankings from DB first
        self.update_rankings()
        
        all_signals = []
        stats = {"generated": 0, "rejected_score": 0}
        
        # In TEST_MODE, we might lower the threshold for observation
        effective_threshold = self.confidence_threshold if not TEST_MODE else 10

        for strategy in self.strategies:
            try:
                strategy_signals = await strategy.evaluate(market_data)
                stats["generated"] += len(strategy_signals)
                for signal in strategy_signals:
                    # Enrich signal with dynamic coordinator weight and capital limit
                    signal["capital_limit"] = self.capital_limits.get(strategy.name, 100.0)
                    signal["strategy_weight"] = self.strategy_weights.get(strategy.name, 0.20)
                    all_signals.append(signal)
            except Exception as e:
                logger.error(f"Error evaluating strategy {getattr(strategy, 'name', 'Unknown')}: {e}")

        # Group signals by (token_id, side) to check for confirmations
        grouped_signals = {}
        for signal in all_signals:
            key = (signal['token_id'], signal['side'])
            if key not in grouped_signals:
                grouped_signals[key] = []
            grouped_signals[key].append(signal)

        final_signals = []
        for (token_id, side), strats in grouped_signals.items():
            # Resolve conflicts using performance-based ranking:
            # Sort signals descending by their strategy's score, breaking ties with the signal's own score
            # If a strategy is blocked by market cooldowns (and has no bypass), rank it lower.
            def get_resolution_key(x):
                is_blocked = False
                if risk_manager:
                    token_info = next((t for t in market_data if t.get("token_id") == x["token_id"]), {})
                    is_blocked = risk_manager.is_cooldown_blocked(x["strategy"], x["token_id"], x, token_info)
                return (0 if is_blocked else 1, self.strategy_scores.get(x['strategy'], 0), x['score'])

            best_signal = max(
                strats,
                key=get_resolution_key
            )

            base_score = best_signal['score']
            
            # Boost score if multiple strategies agree
            confirmation_boost = (len(strats) - 1) * 15
            final_score = min(100, base_score + confirmation_boost)
            
            best_signal['score'] = final_score
            
            if final_score >= effective_threshold:
                final_signals.append(best_signal)
            else:
                stats["rejected_score"] += 1
        
        return final_signals, stats

class BaseStrategy:
    def __init__(self, name="BaseStrategy"):
        self.name = name

    async def evaluate(self, market_data):
        raise NotImplementedError("Strategies must implement evaluate method")
