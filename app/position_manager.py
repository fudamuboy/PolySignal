import time
import collections
from .logger import logger
from .database import Database

class PositionManager:
    def __init__(self):
        self.db = Database()
        self.positions = self.db.load_positions()
        self.realized_pnl = 0
        self.trade_history = [] # Last 20 trades for live performance tracking
        self.strategy_stats = collections.defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0})
        self.market_stats = collections.defaultdict(lambda: {'pnl': 0, 'count': 0})
        # Staleness tracking: counts consecutive loops where a position has no market data
        self._market_miss_count = collections.defaultdict(int)  # {token_id: loops_unseen}
        
        # Week 2A: Inventory Tracking & Exposure variables
        self.max_exposure_observed = 0.0
        self.total_exposure_sum = 0.0
        self.ticks_count = 0

    def update_inventory_metrics(self, enriched_markets):
        """
        Calculate total exposure, concentration (HH-like index/ratio),
        rolling average exposure, and peak exposure. Persist to DB.
        """
        # Create a map of current prices from enriched tokens
        current_prices = {}
        for m in enriched_markets:
            tid = m.get("token_id")
            if tid:
                current_prices[tid] = float(m.get("last_price") or 0.0)
                
        total_exposure = 0.0
        max_pos_exposure = 0.0
        
        for token_id, pos in self.positions.items():
            curr_price = current_prices.get(token_id, pos['avg_price'])
            exposure = pos['size'] * curr_price
            total_exposure += exposure
            if exposure > max_pos_exposure:
                max_pos_exposure = exposure
                
        concentration = (max_pos_exposure / total_exposure) if total_exposure > 0.0 else 0.0
        
        # Update running session stats
        self.max_exposure_observed = max(self.max_exposure_observed, total_exposure)
        self.total_exposure_sum += total_exposure
        self.ticks_count += 1
        
        # Persist to database
        self.db.save_inventory_snapshot(total_exposure, max_pos_exposure, concentration)
        
        metrics = {
            "total_exposure": round(total_exposure, 4),
            "max_position_exposure": round(max_pos_exposure, 4),
            "concentration": round(concentration, 4),
            "max_exposure_observed": round(self.max_exposure_observed, 4),
            "avg_exposure": round(self.total_exposure_sum / self.ticks_count if self.ticks_count > 0 else 0.0, 4)
        }
        
        logger.info(
            f"INVENTORY_METRICS | exposure={metrics['total_exposure']:.2f} USD | "
            f"concentration={metrics['concentration']*100:.1f}% | "
            f"avg_exposure={metrics['avg_exposure']:.2f} USD | "
            f"max_exposure={metrics['max_exposure_observed']:.2f} USD"
        )
        return metrics

    def update_position(self, token_id, size, price, side, strategy='Unknown', spread=0, slippage=0):
        """Update position and return realized PnL for this transaction."""
        transaction_pnl = 0
        
        if side == 'BUY':
            if token_id not in self.positions:
                self.positions[token_id] = {
                    'size': size, 
                    'avg_price': price, 
                    'strategy': strategy,
                    'entry_time': time.time()
                }
            else:
                old_size = self.positions[token_id]['size']
                old_price = self.positions[token_id]['avg_price']
                old_time = self.positions[token_id].get('entry_time', time.time())
                new_size = old_size + size
                new_price = ((old_price * old_size) + (price * size)) / new_size
                self.positions[token_id] = {
                    'size': new_size, 
                    'avg_price': new_price, 
                    'strategy': strategy,
                    'entry_time': old_time
                }
        elif side == 'SELL':
            if token_id in self.positions:
                old_size = self.positions[token_id]['size']
                old_price = self.positions[token_id]['avg_price']
                opening_strategy = self.positions[token_id].get('strategy', 'Unknown')
                
                # Realized PnL = (Sell Price - Buy Price) * Sold Size
                transaction_pnl = (price - old_price) * size
                self.realized_pnl += transaction_pnl
                
                new_size = old_size - size
                if new_size <= 1e-8: # floating point epsilon
                    del self.positions[token_id]
                else:
                    self.positions[token_id]['size'] = new_size
                
                # Override the strategy name with the opening strategy for correct database tracking
                strategy = opening_strategy
        
        # Track history (last 20)
        trade_record = {
            'token_id': token_id,
            'side': side,
            'pnl': transaction_pnl,
            'strategy': strategy,
            'price': price,
            'spread': spread,
            'slippage': slippage
        }
        self.trade_history.append(trade_record)
        if len(self.trade_history) > 20:
            self.trade_history.pop(0)

        # Persist trade and updated position
        self.db.save_trade(token_id, side, size, price, strategy, transaction_pnl, spread, slippage)
        self.db.update_position(token_id, 
                               self.positions[token_id]['size'] if token_id in self.positions else 0,
                               self.positions[token_id]['avg_price'] if token_id in self.positions else 0)
        
        # Update categorized stats
        if side == 'SELL' or transaction_pnl != 0:
            self.strategy_stats[strategy]['pnl'] += transaction_pnl
            if transaction_pnl > 0: self.strategy_stats[strategy]['wins'] += 1
            elif transaction_pnl < 0: self.strategy_stats[strategy]['losses'] += 1
            
            self.market_stats[token_id]['pnl'] += transaction_pnl
            self.market_stats[token_id]['count'] += 1

        logger.info(f"Updated position for {token_id} | Side: {side} | PnL: {transaction_pnl:+.4f} | Total Realized: {self.realized_pnl:+.4f}")
        return transaction_pnl

    def get_live_performance(self):
        """Calculate metrics for the last 20 trades."""
        if not self.trade_history:
            return {"win_rate": 0, "net_pnl": 0, "count": 0}
        
        completed_trades = [t for t in self.trade_history if t['pnl'] != 0 or t['side'] == 'SELL']
        if not completed_trades:
            return {"win_rate": 0, "net_pnl": 0, "count": 0}

        wins = len([t for t in completed_trades if t['pnl'] > 0])
        net_pnl = sum(t['pnl'] for t in completed_trades)
        win_rate = (wins / len(completed_trades)) * 100
        
        return {"win_rate": win_rate, "net_pnl": net_pnl, "count": len(completed_trades)}

    def calculate_pnl_report(self, enriched_markets):
        """Calculate realized and unrealized PnL report."""
        unrealized_pnl = 0
        report = []
        
        # Create map of current prices
        current_prices = {m.get("condition_id"): float(m.get("last_price", 0)) for m in enriched_markets if m.get("last_price")}

        for token_id, pos in self.positions.items():
            current_price = current_prices.get(token_id, pos['avg_price'])
            u_pnl = (current_price - pos['avg_price']) * pos['size']
            unrealized_pnl += u_pnl
            report.append({
                "token_id": token_id,
                "size": pos['size'],
                "avg_price": pos['avg_price'],
                "current_price": current_price,
                "u_pnl": u_pnl
            })
            
        return {
            "realized": self.realized_pnl,
            "unrealized": unrealized_pnl,
            "total": self.realized_pnl + unrealized_pnl,
            "positions_count": len(self.positions),
            "positions": report
        }

    def get_all_positions(self):
        return self.positions

    def clear_all_positions(self):
        self.db.clear_all_positions()
        self.positions = {}
        self.realized_pnl = 0
        self.trade_history = []
        logger.info("PositionManager: All positions cleared")

    def get_open_positions_count(self):
        return len(self.positions)

    def get_open_positions_summary(self):
        if not self.positions:
            return "No open positions"
        details = []
        for token_id, pos in self.positions.items():
            details.append(f"{token_id} ({pos['size']:.2f} @ {pos['avg_price']:.4f})")
        return f"Total: {len(self.positions)} | Markets: [{', '.join(details)}]"

    def get_positions_for_exit(self, market_map):
        """Determine positions that meet exit criteria (TP, SL, Time).
        market_map: {token_id: market_info_dict} from main.py enrichment.
        Returns list of dicts with token_id and reason.
        """
        exits = []
        now = time.time()
        
        # Pull threshold constants from config (imported at top or here)
        from .config import (
            TP_HIGH_CONFIDENCE, TP_STRONG_TREND, 
            DYNAMIC_SL_SPREAD_MULT, DYNAMIC_SL_FLOOR, MAX_SL_ABSOLUTE,
            MAX_SPREAD
        )

        for token_id, pos in list(self.positions.items()):
            entry_price = pos.get('avg_price')
            size = pos.get('size')
            if entry_price is None or size is None:
                continue

            market_info = market_map.get(token_id)

            # --- Two-Sided MM Matched Pair Exit ---
            opp_token_id = None
            if market_info:
                for tok in market_info.get("tokens", []):
                    tid = tok.get("token_id")
                    if tid and str(tid) != str(token_id):
                        opp_token_id = str(tid)
                        break
            if opp_token_id and opp_token_id in self.positions:
                best_bid = market_info.get("best_bid") or market_info.get("last_price") or entry_price
                best_ask = market_info.get("best_ask") or market_info.get("last_price") or entry_price
                
                # Quoting inside the spread: under-cut the best ask slightly
                # Decrement by 0.0005 (or 0.0001 if spread is tight)
                epsilon = 0.0005
                if best_ask - best_bid <= 0.0010:
                    epsilon = 0.0001
                
                exit_price = max(best_bid, best_ask - epsilon)
                exits.append({
                    'token_id': token_id,
                    'reason': 'TWO_SIDED_MM_EXIT',
                    'side': 'SELL',
                    'size': size,
                    'price': round(exit_price, 4),
                    'spread': market_info.get('spread', 0.0)
                })
                continue

            # ----------------------------------------------------------------
            # BUG FIX: Stale / filtered-out position handling
            # When a token's spread widens past MAX_SPREAD it disappears from
            # market_map (enrichment filter). Without this guard the SL is
            # silently skipped every loop, allowing unlimited losses.
            # ----------------------------------------------------------------
            if not market_info:
                self._market_miss_count[token_id] += 1
                # After 3 consecutive misses, force an emergency exit using the
                # last known price stored in the position itself.
                STALE_LOOPS_LIMIT = 3
                if self._market_miss_count[token_id] >= STALE_LOOPS_LIMIT:
                    last_known = pos.get('last_known_price', entry_price)
                    stale_change = (last_known - entry_price) / entry_price if entry_price else 0
                    logger.warning(
                        f"STALE POSITION: {token_id} | Not in market_map for "
                        f"{self._market_miss_count[token_id]} loops | "
                        f"Last known price: {last_known:.4f} | "
                        f"Approx move: {stale_change*100:.1f}% | Forcing exit."
                    )
                    exits.append({
                        'token_id': token_id,
                        'reason': f"SL_STALE (no market data for {self._market_miss_count[token_id]} loops)",
                        'side': 'SELL',
                        'size': size,
                        'price': last_known,
                        'spread': MAX_SPREAD  # worst-case spread assumption
                    })
                else:
                    logger.warning(
                        f"POSITION INVISIBLE: {token_id} | Missing from market_map "
                        f"(loop {self._market_miss_count[token_id]}/{STALE_LOOPS_LIMIT}) — "
                        f"token likely filtered by spread. Monitoring."
                    )
                continue

            # Token is visible this loop — reset staleness counter
            self._market_miss_count[token_id] = 0

            # HIGH IMPACT CHANGE: Evaluate SL/TP exit using MID price instead of BID price
            curr_mid_price = market_info.get("mid_price") or market_info.get("last_price")
            curr_exit_price = market_info.get("best_bid") or market_info.get("last_price")
            if curr_mid_price is None or curr_exit_price is None:
                continue

            # Store last known price for use in stale-position fallback above
            pos['last_known_price'] = curr_mid_price

            change = (curr_mid_price - entry_price) / entry_price

            # 1. Dynamic Stop Loss — hard-capped at MAX_SL_ABSOLUTE (5%)
            current_spread = market_info.get("spread", MAX_SPREAD)
            dynamic_sl = min(
                MAX_SL_ABSOLUTE,
                max(DYNAMIC_SL_FLOOR, current_spread * DYNAMIC_SL_SPREAD_MULT)
            )

            if change <= -dynamic_sl:
                exits.append({
                    'token_id': token_id,
                    'reason': f"SL_DYNAMIC ({change*100:.1f}% vs SL {dynamic_sl*100:.1f}%)",
                    'side': 'SELL',
                    'size': size,
                    # We still exit at the BID price when selling (retaining transaction cost realism)
                    'price': curr_exit_price,
                    'spread': current_spread
                })
                continue

            # 2. Dynamic Take Profit — ALL momentum trades use TP_HIGH_CONFIDENCE (7%) floor
            tp_target = TP_HIGH_CONFIDENCE  # 7% default (raised from 3%)
            strategy = pos.get('strategy', 'Unknown')
            if 'Trend' in strategy:
                tp_target = TP_STRONG_TREND  # 10% for strong trend trades
            
            if change >= tp_target:
                exits.append({
                    'token_id': token_id, 
                    'reason': f"TP_DYNAMIC ({change*100:.1f}% vs TP {tp_target*100:.1f}%)",
                    'side': 'SELL',
                    'size': size,
                    # We still exit at the BID price when selling
                    'price': curr_exit_price,
                    'spread': current_spread
                })
                continue

            # NOTE: Time-stop removed intentionally.
            # Rationale: Time-stop exits were producing uncontrolled losses.
            # Positions now exit via TP or SL only.
            # A separate emergency close (close_all.py) exists for manual intervention.
                
        return exits

    def cleanup_positions(self, active_token_ids=None):
        if active_token_ids is None:
            return
        stale_tokens = [t_id for t_id in self.positions if t_id not in active_token_ids]
        for t_id in stale_tokens:
            logger.info(f"PositionManager: Removing stale position for {t_id}")
            del self.positions[t_id]
            self.db.update_position(t_id, 0, 0)
    def get_performance_analytics(self):
        """Return best/worst stats for reporting."""
        best_strat = max(self.strategy_stats.items(), key=lambda x: x[1]['pnl'], default=(None, {'pnl': 0}))
        worst_market = min(self.market_stats.items(), key=lambda x: x[1]['pnl'], default=(None, {'pnl': 0}))
        
        return {
            "best_strategy": best_strat[0] if best_strat[0] else "N/A",
            "best_strat_pnl": best_strat[1]['pnl'],
            "worst_market": worst_market[0] if worst_market[0] else "N/A",
            "worst_market_pnl": worst_market[1]['pnl']
        }
