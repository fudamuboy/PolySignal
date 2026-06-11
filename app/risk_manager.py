import time
from collections import defaultdict
from .config import (
    MAX_CAPITAL_PER_TRADE, MAX_DAILY_LOSS, MAX_OPEN_POSITIONS,
    MAX_DAILY_DRAWDOWN, MAX_STOP_LOSS_PER_TRADE,
    CONSECUTIVE_LOSS_LIMIT, COOLDOWN_DURATION, BASE_ORDER_SIZE,
    ESTIMATED_FEE_BPS, MIN_MOVE_THRESHOLD, TEST_MODE,
    MIN_ENTRY_PRICE, MAX_ENTRY_PRICE, MARKET_COOLDOWN,
    SOLO_MOMENTUM_SIZE_MULTIPLIER, EV_SAFETY_MARGIN,
    MIN_RR_RATIO, MAX_SL_ABSOLUTE, DYNAMIC_SL_FLOOR, MIN_DEPTH_USD,
    MIN_DELTA_FOR_TRADE, RR_ADAPTIVE_SCORE_THRESHOLD, RR_ADAPTIVE_MIN,
    NEAR_MISS_LOG_LIMIT, SELL_SKIP_CACHE_TTL, MAX_SPREAD,
    MIN_DEPTH_USD_TEST, PAPER_TRADING, SOLO_MOMENTUM_MIN_SCORE,
    ZOMBIE_PRICE_CAP, ZOMBIE_BID_FLOOR, ZOMBIE_MAX_SPREAD
)
from .logger import logger

class RiskManager:
    def __init__(self, position_manager=None):
        self.max_capital_per_trade = MAX_CAPITAL_PER_TRADE
        self.max_daily_loss = MAX_DAILY_LOSS
        self.max_daily_drawdown = MAX_DAILY_DRAWDOWN
        self.max_stop_loss_per_trade = MAX_STOP_LOSS_PER_TRADE
        self.max_open_positions = MAX_OPEN_POSITIONS
        self.consecutive_loss_limit = CONSECUTIVE_LOSS_LIMIT
        self.cooldown_duration = COOLDOWN_DURATION
        self.base_order_size = BASE_ORDER_SIZE
        
        self.daily_total_loss = 0
        self.consecutive_losses = 0
        self.cooldown_until = 0
        self.market_cooldowns = {} # {token_id: last_trade_time}
        
        # Dependency on position_manager for accurate counting
        self.pm = position_manager

        # Fix #1: Short-lived cache to suppress repeated SELL signals for tokens
        # with no open position. Avoids re-processing every loop.
        self._sell_skip_cache = {}  # {token_id: timestamp_when_skipped}

        # Fix #4: Near-miss tracking — top rejected signals per hour
        self._near_miss_hour = -1          # current hour bucket
        self._near_miss_count = 0          # count of near-misses logged this hour
        self._near_miss_buffer = []        # list of (rr_ratio, log_str) for sorting
        self.last_rejection_reason = None

    def validate_trade(self, signal, market_data=None, is_fallback=False):
        """
        Check if a trade signal satisfies all risk rules and quality filters.
        """
        self.last_rejection_reason = None
        current_time = time.time()
        token_id = signal.get('token_id', 'Unknown')
        score = signal.get('score', 0.5)
        side = signal.get('side', 'BUY')
        delta = abs(signal.get('delta', 0))

        # Pre-compute calibration context fields (used in all CALIBRATION log lines)
        def _calib_reject(reason, market_data=market_data):
            spread  = signal.get('spread', market_data.get('spread', 0) if market_data else 0)
            rr      = round(delta / MAX_SL_ABSOLUTE, 3) if MAX_SL_ABSOLUTE > 0 else 0
            e_price = float(signal.get('price', 0))
            zombie_ok = e_price <= ZOMBIE_PRICE_CAP if side == 'BUY' else True
            logger.warning(
                f"CALIBRATION_REJECTED | token={token_id[:22]}... | "
                f"side={side} | delta={delta:.4f} | RR={rr:.3f} | "
                f"spread={spread:.4f} | score={score:.2f} | "
                f"zombie_ok={zombie_ok} | reason={reason}"
            )

        def _calib_accept(market_data=market_data):
            spread  = signal.get('spread', market_data.get('spread', 0) if market_data else 0)
            rr      = round(delta / MAX_SL_ABSOLUTE, 3) if MAX_SL_ABSOLUTE > 0 else 0
            e_price = float(signal.get('price', 0))
            zombie_ok = e_price <= ZOMBIE_PRICE_CAP if side == 'BUY' else True
            logger.info(
                f"CALIBRATION_ACCEPTED | token={token_id[:22]}... | "
                f"side={side} | delta={delta:.4f} | RR={rr:.3f} | "
                f"spread={spread:.4f} | score={score:.2f} | "
                f"zombie_ok={zombie_ok}"
            )

        # ----------------------------------------------------------------
        # Fix #1: SELL Guard — skip immediately if no open position
        # Also maintains a short-lived skip cache (SELL_SKIP_CACHE_TTL)
        # to avoid re-logging / re-processing the same token every loop.
        # ----------------------------------------------------------------
        if side == 'SELL':
            # Evict expired entries from cache
            expired = [k for k, t in self._sell_skip_cache.items()
                       if current_time - t > SELL_SKIP_CACHE_TTL]
            for k in expired:
                del self._sell_skip_cache[k]

            if token_id in self._sell_skip_cache:
                # Still in cooldown — silently skip (already logged)
                return False

            if self.pm:
                open_size = self.pm.positions.get(token_id, {}).get('size', 0)
                if open_size <= 0 and not signal.get('test_mode') and not is_fallback:
                    logger.warning(
                        f"SELL_SKIPPED_NO_POSITION: {token_id} | "
                        f"No open position to sell. "
                        f"Caching skip for {SELL_SKIP_CACHE_TTL}s."
                    )
                    self._sell_skip_cache[token_id] = current_time
                    _calib_reject("SELL_SKIPPED_NO_POSITION")
                    self.last_rejection_reason = "SELL_SKIPPED_NO_POSITION"
                    return False

        # 0.1 Block Duplicate BUY entries
        if side == 'BUY' and self.pm:
            open_pos = self.pm.positions.get(token_id, {})
            open_size = open_pos.get('size', 0)
            if open_size > 0 and not signal.get("test_mode") and not is_fallback:
                logger.warning(f"RISK REJECTED: {token_id} | Reason: Duplicate BUY entry not allowed (position already open)")
                _calib_reject("DUPLICATE_BUY")
                self.last_rejection_reason = "DUPLICATE_BUY"
                return False

        # --- STRICT CAPITAL LIMIT CHECK (Priority 2) ---
        if side == 'BUY' and self.pm:
            current_prices = {tid: pos['avg_price'] for tid, pos in self.pm.positions.items()}
            total_exposure = sum(pos['size'] * current_prices.get(tid, pos['avg_price']) for tid, pos in self.pm.positions.items())
            
            order_price = float(signal.get('price', 0.5))
            order_size = float(signal.get('size', self.base_order_size))
            if market_data:
                order_size = self.calculate_position_size(signal, market_data)
                
            order_value = order_size * order_price
            
            from .config import TOTAL_CAPITAL_POOL
            
            # 1. Strategy-Specific Capital Limit Check
            strategy_name = signal.get('strategy', 'Unknown')
            strategy_exposure = sum(
                pos['size'] * current_prices.get(tid, pos['avg_price'])
                for tid, pos in self.pm.positions.items()
                if pos.get('strategy') == strategy_name
            )
            strategy_capital_limit = float(signal.get('capital_limit', 100.0))
            
            if strategy_exposure + order_value > strategy_capital_limit and not signal.get("test_mode") and not is_fallback:
                logger.warning(
                    f"STRATEGY CAPITAL LIMIT REJECTED: {token_id} | Strategy: {strategy_name} | "
                    f"Order Value ${order_value:.2f} + Strategy Exposure ${strategy_exposure:.2f} = "
                    f"${strategy_exposure + order_value:.2f} > Strategy Capital Limit ${strategy_capital_limit:.2f}. "
                    f"Rejecting to enforce dynamic allocation."
                )
                _calib_reject("EXCEEDS_STRATEGY_CAPITAL")
                self.last_rejection_reason = "EXCEEDS_STRATEGY_CAPITAL"
                return False

            # 2. Global Capital Pool Limit Check
            if total_exposure + order_value > TOTAL_CAPITAL_POOL and not signal.get("test_mode") and not is_fallback:
                logger.warning(
                    f"GLOBAL CAPITAL LIMIT REJECTED: {token_id} | "
                    f"Order Value ${order_value:.2f} + Current Exposure ${total_exposure:.2f} = "
                    f"${total_exposure + order_value:.2f} > Global Capital Limit ${TOTAL_CAPITAL_POOL:.2f}. "
                    f"Rejecting to prevent negative cash / virtual leverage."
                )
                _calib_reject("EXCEEDS_GLOBAL_CAPITAL")
                self.last_rejection_reason = "EXCEEDS_GLOBAL_CAPITAL"
                return False
        
        # 0.1 Block Max Capital Accumulation per Market
        if side == 'BUY' and self.pm:
            open_pos = self.pm.positions.get(token_id, {})
            open_size = open_pos.get('size', 0)
            avg_price = open_pos.get('avg_price', 0)
            exposure = open_size * avg_price
            if exposure >= self.max_capital_per_trade and not signal.get("test_mode") and not is_fallback:
                logger.warning(f"RISK REJECTED: {token_id} | Reason: Max capital per market hit (${exposure:.2f} >= ${self.max_capital_per_trade})")
                _calib_reject("MAX_CAPITAL_PER_MARKET")
                self.last_rejection_reason = "MAX_CAPITAL_PER_MARKET"
                return False
        
        # ----------------------------------------------------------------
        # 0.2 Noise Filter
        # ----------------------------------------------------------------
        is_two_sided = signal.get("strategy") == "TwoSidedMMStrategy"
        is_news = signal.get("strategy") == "NewsStrategy"
        bypass_filters = is_two_sided or is_news or signal.get("test_mode") or is_fallback
        
        if delta < MIN_MOVE_THRESHOLD and not bypass_filters:
            logger.warning(f"V2 QUALITY REJECT: Noise detected for {token_id} | Move: {delta:.4f} < {MIN_MOVE_THRESHOLD}")
            _calib_reject(f"NOISE_MOVE_TOO_SMALL({delta:.4f}<{MIN_MOVE_THRESHOLD})")
            self.last_rejection_reason = "NOISE_MOVE_TOO_SMALL"
            return False

        # ----------------------------------------------------------------
        # Fix #3: Revised RR gate
        # Requires BOTH: abs(delta) >= 0.025 AND rr_ratio >= MIN_RR_RATIO
        # Adaptive: score > 0.85 allows rr_ratio >= RR_ADAPTIVE_MIN (0.45)
        # ----------------------------------------------------------------
        if not bypass_filters:
            rr_ratio = delta / MAX_SL_ABSOLUTE if MAX_SL_ABSOLUTE > 0 else 0

            # Determine effective RR threshold
            if score > RR_ADAPTIVE_SCORE_THRESHOLD:
                effective_min_rr = RR_ADAPTIVE_MIN       # 0.45 for high-conviction
            else:
                effective_min_rr = MIN_RR_RATIO          # 0.50 base

            # Both conditions must hold
            delta_ok = delta >= MIN_DELTA_FOR_TRADE
            rr_ok    = rr_ratio >= effective_min_rr

            if not delta_ok or not rr_ok:
                rejection_reason = []
                if not delta_ok:
                    rejection_reason.append(f"delta {delta:.4f} < min {MIN_DELTA_FOR_TRADE:.4f}")
                if not rr_ok:
                    rejection_reason.append(f"RR {rr_ratio:.3f} < min {effective_min_rr:.2f}")

                rejection_str = " | ".join(rejection_reason)
                spread = signal.get('spread', market_data.get('spread', 0) if market_data else 0)

                logger.warning(
                    f"RR REJECT: {token_id} | Side={side} | "
                    f"Delta={delta:.4f} | RR={rr_ratio:.3f} | "
                    f"Spread={spread:.4f} | Reason: {rejection_str} | score={score:.2f}"
                )

                # Fix #4: Buffer near-miss signals (just below threshold)
                self._buffer_near_miss(token_id, side, delta, rr_ratio, spread,
                                       f"RR_BELOW_THRESHOLD: {rejection_str}")
                _calib_reject(f"RR_REJECT({rejection_str})")
                self.last_rejection_reason = "RR_REJECT"
                return False

            logger.info(
                f"RR OK: {token_id} | RR={rr_ratio:.3f} | delta={delta:.4f} | "
                f"min_rr={effective_min_rr:.2f} | score={score:.2f}"
            )
        # ----------------------------------------------------------------
        # Fix #6: Spread safety filter — skip BUY if spread > 20%
        # ----------------------------------------------------------------
        if side == 'BUY' and market_data and not signal.get("test_mode") and not is_fallback:
            spread = market_data.get('spread', 0)
            if spread > MAX_SPREAD:
                logger.warning(
                    f"SPREAD_TOO_WIDE: {token_id} | "
                    f"Spread={spread:.4f} ({spread*100:.1f}%) > max {MAX_SPREAD*100:.0f}%"
                )
                _calib_reject(f"SPREAD_TOO_WIDE({spread:.4f}>{MAX_SPREAD})")
                self.last_rejection_reason = "SPREAD_TOO_WIDE"
                return False



        if market_data:
            bid_depth   = market_data.get('bid_depth', 0)
            ask_depth   = market_data.get('ask_depth', 0)
            total_depth = bid_depth + ask_depth

            # In paper/test mode, apply a relaxed floor of $200 instead of $300
            in_test = PAPER_TRADING or TEST_MODE or signal.get("test_mode") or is_fallback
            effective_floor = MIN_DEPTH_USD_TEST if in_test else MIN_DEPTH_USD

            if total_depth < effective_floor:
                logger.warning(
                    f"V2 LIQUIDITY REJECT: {token_id} | "
                    f"Total Depth: ${total_depth:.0f} < ${effective_floor:.0f}"
                )
                _calib_reject(f"LOW_LIQUIDITY(${total_depth:.0f}<${effective_floor:.0f})")
                self.last_rejection_reason = "LOW_LIQUIDITY"
                return False

            # Warn (but allow) when depth is in the 200–300 grey zone (test mode only)
            if in_test and total_depth < MIN_DEPTH_USD:
                logger.warning(
                    f"LOW_LIQUIDITY_TEST_MODE_ALLOWED: {token_id} | "
                    f"Depth: ${total_depth:.0f} (${MIN_DEPTH_USD_TEST:.0f}–${MIN_DEPTH_USD:.0f} grey zone) | "
                    f"Allowed in PAPER/TEST mode only"
                )

        # 0.3 Solo Momentum Quality Filter
        if signal.get('strategy') == "SOLO_MOMENTUM":
            # SOLO_MOMENTUM_MIN_SCORE imported at module level — no local import needed.
            if score < SOLO_MOMENTUM_MIN_SCORE and not is_fallback:
                logger.warning(f"RISK REJECTED: {token_id} | Reason: Solo Momentum score {score:.2f} < {SOLO_MOMENTUM_MIN_SCORE}")
                self.last_rejection_reason = "SOLO_MOMENTUM_LOW_SCORE"
                return False


        # 0.1 EV+ Check
        if market_data:
            # --- NEW ANTI-ZOMBIE PROTECTION (Pre-Trade) ---
            # Note: ZOMBIE_PRICE_CAP/BID_FLOOR/MAX_SPREAD imported at module level (line 14).
            # Do NOT re-import here — a local import makes Python treat these as locals
            # throughout the entire function, breaking the _calib_reject closure above.

            entry_price = float(signal.get('price', 0.5))
            best_bid = market_data.get('best_bid', 0)
            spread = market_data.get('spread', 1.0)
            
            # 1. Price Cap Filter
            if side == 'BUY' and entry_price > ZOMBIE_PRICE_CAP:
                logger.warning(f"ZOMBIE REJECT: {token_id} | Price {entry_price:.4f} > {ZOMBIE_PRICE_CAP} cap (high risk of resolution)")
                _calib_reject(f"ZOMBIE_PRICE_CAP({entry_price:.4f}>{ZOMBIE_PRICE_CAP})")
                self.last_rejection_reason = "ZOMBIE_PRICE_CAP"
                return False
                
            # 2. Bid Floor Protection
            if best_bid < ZOMBIE_BID_FLOOR:
                logger.warning(f"BID TOO LOW: {token_id} | Best Bid {best_bid:.4f} < {ZOMBIE_BID_FLOOR} (exit risk)")
                _calib_reject(f"ZOMBIE_BID_FLOOR({best_bid:.4f}<{ZOMBIE_BID_FLOOR})")
                self.last_rejection_reason = "ZOMBIE_BID_FLOOR"
                return False
                
            # 3. Zombie Spread Filter
            if spread > ZOMBIE_MAX_SPREAD:
                logger.warning(f"ZOMBIE SPREAD: {token_id} | Spread {spread*100:.1f}% > {ZOMBIE_MAX_SPREAD*100:.1f}% limit")
                _calib_reject(f"ZOMBIE_SPREAD({spread*100:.1f}%>{ZOMBIE_MAX_SPREAD*100:.1f}%)")
                self.last_rejection_reason = "ZOMBIE_SPREAD"
                return False

            if not self.evaluate_ev(signal, market_data, is_fallback=is_fallback):
                # NO BYPASS in V2 unless fallback
                if not is_fallback:
                    logger.warning(f"RISK REJECTED: {token_id} | Reason: Negative EV")
                    _calib_reject("NEGATIVE_EV")
                    self.last_rejection_reason = "NEGATIVE_EV"
                    return False
                else:
                    logger.info(f"EV BYPASS: Accepting low/neg EV signal due to fallback mode for {token_id}")
        
        # 1. Cooldown Check (Global)
        if current_time < self.cooldown_until and not signal.get("test_mode") and not is_fallback:
            wait_time = int(self.cooldown_until - current_time)
            logger.warning(f"RISK REJECTED: Global Cooldown active for {wait_time}s more")
            _calib_reject(f"GLOBAL_COOLDOWN({wait_time}s)")
            self.last_rejection_reason = "GLOBAL_COOLDOWN"
            return False

        # 1.1 Per-Market Cooldown (Strategy-Aware)
        cooldown_info = self.market_cooldowns.get(token_id, 0)
        if isinstance(cooldown_info, dict):
            last_market_trade = cooldown_info.get("timestamp", 0)
            last_strategy = cooldown_info.get("strategy", "PMM")
        else:
            last_market_trade = cooldown_info
            last_strategy = "PMM"

        incoming_strategy = signal.get("strategy", "Unknown")

        if (current_time - last_market_trade) < MARKET_COOLDOWN and not is_fallback:
            remaining = int(MARKET_COOLDOWN - (current_time - last_market_trade))
            
            # Cooldown bypass evaluation
            bypass_cooldown = False
            bypass_reason = ""

            if incoming_strategy == "NewsStrategy":
                # Emergency override: News fully bypasses cooldowns
                bypass_cooldown = True
                bypass_reason = "NewsStrategy emergency override"

            elif incoming_strategy == "OrderBookImbalanceStrategy":
                # OBI: partial cooldown bypass
                # 1. Prevent OBI from repeating its own trades (last trade not OBI)
                # 2. Strong EV edge (expected profit >= 1.0%)
                # 3. Spread quality (spread <= 0.5%)
                # 4. Score quality (score >= 85)
                # 5. Inventory check (exposure <= 10 USD)
                if last_strategy != "OrderBookImbalanceStrategy":
                    spread = market_data.get('spread', 1.0) if market_data else 1.0
                    delta = abs(signal.get('delta', 0))
                    price = float(signal.get('price', 1.0))
                    score = signal.get('score', 50.0)
                    
                    delta_pct = delta / price if price > 0 else 0
                    fee_pct = ESTIMATED_FEE_BPS / 10000
                    margin = EV_SAFETY_MARGIN
                    
                    required_edge = spread + (fee_pct * 2) + margin
                    expected_profit = delta_pct - required_edge
                    
                    current_exposure = 0.0
                    if self.pm:
                        open_pos = self.pm.positions.get(token_id, {})
                        current_exposure = open_pos.get('size', 0.0) * open_pos.get('avg_price', price)
                    
                    if expected_profit >= 0.01 and spread <= 0.005 and score >= 85 and current_exposure <= 10.0:
                        bypass_cooldown = True
                        bypass_reason = f"OBI high-conviction bypass (EV={expected_profit*100:.2f}%, spread={spread*100:.2f}%)"

            elif incoming_strategy == "MomentumStrategy":
                # Momentum: conditional cooldown
                # 1. Prevent Momentum from repeating its own trades (last trade not Momentum)
                # 2. Strong EV edge (expected profit >= 1.0%)
                # 3. Spread quality (spread <= 1.5%)
                # 4. Score quality (score >= 80)
                # 5. Inventory check (exposure <= 10 USD)
                if last_strategy != "MomentumStrategy":
                    spread = market_data.get('spread', 1.0) if market_data else 1.0
                    delta = abs(signal.get('delta', 0))
                    price = float(signal.get('price', 1.0))
                    score = signal.get('score', 50.0)
                    
                    delta_pct = delta / price if price > 0 else 0
                    fee_pct = ESTIMATED_FEE_BPS / 10000
                    margin = EV_SAFETY_MARGIN
                    
                    required_edge = spread + (fee_pct * 2) + margin
                    expected_profit = delta_pct - required_edge
                    
                    current_exposure = 0.0
                    if self.pm:
                        open_pos = self.pm.positions.get(token_id, {})
                        current_exposure = open_pos.get('size', 0.0) * open_pos.get('avg_price', price)
                    
                    if expected_profit >= 0.01 and spread <= 0.015 and score >= 80 and current_exposure <= 10.0:
                        bypass_cooldown = True
                        bypass_reason = f"Momentum high-conviction bypass (EV={expected_profit*100:.2f}%, spread={spread*100:.2f}%)"

            if bypass_cooldown:
                logger.info(f"COOLDOWN BYPASS: Strategy {incoming_strategy} allowed to bypass cooldown on {token_id} | Reason: {bypass_reason}")
            else:
                logger.warning(
                    f"RISK REJECTED: Market Cooldown for {token_id} | {remaining}s left | "
                    f"Last Strategy: {last_strategy} | Incoming: {incoming_strategy}"
                )
                _calib_reject(f"MARKET_COOLDOWN({remaining}s)")
                self.last_rejection_reason = "MARKET_COOLDOWN"
                return False

        # 1.2 Entry Price Quality (0.2 - 0.8 range)
        entry_price = float(signal.get('price', 0.5))
        if (entry_price < MIN_ENTRY_PRICE or entry_price > MAX_ENTRY_PRICE) and not is_fallback:
            logger.warning(f"QUALITY REJECT: Price {entry_price:.2f} too extreme (Range: {MIN_ENTRY_PRICE}-{MAX_ENTRY_PRICE})")
            _calib_reject(f"PRICE_EXTREME({entry_price:.2f} outside [{MIN_ENTRY_PRICE},{MAX_ENTRY_PRICE}])")
            self.last_rejection_reason = "PRICE_EXTREME"
            return False

        # 2. Max Open Positions (Dynamic Check)
        current_count = self.pm.get_open_positions_count() if self.pm else 0
        
        # Log current state clearly
        pos_summary = self.pm.get_open_positions_summary() if self.pm else "No PositionManager"
        logger.info(f"RISK CHECK | Market: {token_id} | Open Positions: {current_count}/{self.max_open_positions} | {pos_summary}")

        if current_count >= self.max_open_positions and not signal.get("test_mode") and not is_fallback:
            logger.warning(
                f"RISK REJECTED: {token_id} | "
                f"Reason: Max open positions reached | "
                f"Current: {current_count} | "
                f"Max Allowed: {self.max_open_positions}"
            )
            _calib_reject("MAX_OPEN_POSITIONS")
            self.last_rejection_reason = "MAX_OPEN_POSITIONS"
            return False
        
        if signal.get("test_mode") or is_fallback:
            reason = "TEST_SIGNAL" if signal.get("test_mode") else "FALLBACK_MODE"
            logger.info(f"RISK BYPASS: {reason} for {token_id} approved despite constraints.")
            return True
        
        # 3. Max Daily Loss / Drawdown
        if self.daily_total_loss >= self.max_daily_loss:
            logger.warning(
                f"RISK REJECTED: {token_id} | "
                f"Reason: Daily loss limit hit | "
                f"Current Loss: ${self.daily_total_loss:.2f} | "
                f"Limit: ${self.max_daily_loss:.2f}"
            )
            _calib_reject(f"DAILY_LOSS_LIMIT(${self.daily_total_loss:.2f}>=${self.max_daily_loss:.2f})")
            self.last_rejection_reason = "DAILY_LOSS_LIMIT"
            return False
            
        logger.info(f"RISK APPROVED: Trade signal for {token_id} satisfies all constraints.")
        _calib_accept()
        return True

    # ----------------------------------------------------------------
    # Fix #4: Near-miss logging helpers
    # ----------------------------------------------------------------
    def _buffer_near_miss(self, token_id, side, delta, rr_ratio, spread, reason):
        """
        Buffer a rejected near-miss signal for hourly top-10 reporting.
        Prioritises signals with the highest RR (closest to the threshold).
        """
        import datetime
        current_hour = datetime.datetime.now().hour

        # Reset buffer on hour change
        if current_hour != self._near_miss_hour:
            self._flush_near_misses()
            self._near_miss_hour = current_hour
            self._near_miss_buffer = []
            self._near_miss_count = 0

        entry = (rr_ratio, {
            "token_id": token_id,
            "side": side,
            "delta": delta,
            "rr_ratio": rr_ratio,
            "spread": spread,
            "rejection_reason": reason,
        })
        self._near_miss_buffer.append(entry)

    def _flush_near_misses(self):
        """
        Log the top NEAR_MISS_LOG_LIMIT near-misses (highest RR first).
        Called at the start of each new hour.
        """
        if not self._near_miss_buffer:
            return
        top = sorted(self._near_miss_buffer, key=lambda x: x[0], reverse=True)[:NEAR_MISS_LOG_LIMIT]
        logger.info(f"NEAR_MISS_REPORT: Top {len(top)} near-misses this hour:")
        for rr, nm in top:
            logger.info(
                f"  NEAR_MISS | token={nm['token_id'][:20]}... | "
                f"side={nm['side']} | delta={nm['delta']:.4f} | "
                f"rr={nm['rr_ratio']:.3f} | spread={nm['spread']:.4f} | "
                f"reason={nm['rejection_reason']}"
            )

    def evaluate_ev(self, signal, market_data, is_fallback=False):

        """
        Calculate Expected Value (EV) considering spread and fees.
        Units: All converted to percentage of mid-price.
        """
        token_id = signal.get('token_id')
        spread = market_data.get('spread', 1.0)
        delta = abs(signal.get('delta', 0))
        price = float(signal.get('price', 1.0))
        score = signal.get('score', 50.0)
        
        # 1. Delta Percentage (Price edge / Current price)
        delta_pct = delta / price if price > 0 else 0
        
        # 2. Estimated Fees (BPS to Decimal)
        fee_pct = ESTIMATED_FEE_BPS / 10000
        
        # 3. Required Edge (Spread cost + Fee cost + Safety Margin)
        # Margin is smaller in fallback mode
        margin = EV_SAFETY_MARGIN if not is_fallback else 0.0002
        
        # Determine if the signal will be executed as a passive maker order
        # (Passive limit orders are those with score < 85, or from TwoSidedMMStrategy)
        is_passive = ((score < 85) if score > 1.0 else (score < 0.85)) or signal.get("strategy") == "TwoSidedMMStrategy"
        
        # For passive maker orders, we do not pay the spread cost on entry (it is captured / zero spread cost)
        if is_passive:
            required_edge = (fee_pct * 2) + margin
            logger.info(f"EV_CALC: Treating order for {token_id} as PASSIVE maker. Removing spread penalty ({spread*100:.3f}%).")
        else:
            required_edge = spread + (fee_pct * 2) + margin
            logger.info(f"EV_CALC: Treating order for {token_id} as AGGRESSIVE taker. Retaining spread penalty ({spread*100:.3f}%).")
        
        expected_profit = delta_pct - required_edge
        
        if expected_profit <= 0 and not signal.get("test_mode") and not is_fallback:
            logger.warning(f"EV REJECT: {token_id} | Move: {delta_pct*100:.2f}% | Req: {required_edge*100:.2f}% | Profit: {expected_profit*100:.2f}% | Score: {score:.2f}")
            return False
        
        logger.info(f"EV APPROVED: {token_id} | Signal Edge: {expected_profit*100:+.2f}% (Spread: {spread*100:.2f}%)")
        return True

    def calculate_position_size(self, signal, market_data):
        """
        Dynamically scale position size based on signal score and liquidity.
        """
        score = signal.get('score', 0.5)
        price = float(signal.get('price', 0.5))
        token_id = signal.get('token_id')
        
        # Quadratic scaling: score squared
        # Score 0.8 is the baseline (scale_factor = 1.0)
        scale_factor = (score / 0.8) ** 2
        
        # Scale DOWN for lower confidence trades
        if score < 0.45:
            scale_factor *= 0.5
            logger.info(f"SIZE SCALING: Low confidence ({score:.2f}) -> applying 0.5x reduction")
            
        # --- Solo Momentum Sizing ---
        if signal.get('strategy') == "SOLO_MOMENTUM":
            scale_factor *= SOLO_MOMENTUM_SIZE_MULTIPLIER
            logger.info(f"SIZE SCALING: Solo Momentum detected -> applying {SOLO_MOMENTUM_SIZE_MULTIPLIER}x reduction")

        size = self.base_order_size * scale_factor
        
        orderbook = market_data.get('orderbook')
        if orderbook:
            side = signal.get('side', 'BUY')
            if side == 'BUY':
                asks = getattr(orderbook, 'asks', []) if not isinstance(orderbook, dict) else orderbook.get('asks', [])
                depth = sum(float(a.size if hasattr(a, 'size') else a.get('size', 0)) for a in asks[:5])
            else:
                bids = getattr(orderbook, 'bids', []) if not isinstance(orderbook, dict) else orderbook.get('bids', [])
                depth = sum(float(b.size if hasattr(b, 'size') else b.get('size', 0)) for b in bids[:5])
            
            if depth > 0:
                # Allow taking up to 15% of top-5 depth for higher conviction
                size = min(size, depth * 0.15)

        # Cap size to not exceed maximum allowed capital taking into account existing open position
        current_exposure = 0
        if self.pm and token_id:
            open_pos = self.pm.positions.get(token_id, {})
            current_exposure = open_pos.get('size', 0) * open_pos.get('avg_price', price)
            
        remaining_capital = max(0, self.max_capital_per_trade - current_exposure)
        trade_value = size * price
        
        if trade_value > remaining_capital:
            size = remaining_capital / price
            
        return round(size, 2)

    def update_after_trade(self, success, token_id=None, pnl=0, strategy=None):
        if token_id:
            self.market_cooldowns[token_id] = {
                "timestamp": time.time(),
                "strategy": strategy or "PMM"
            }
            
        if success:
            if pnl < 0:
                self.daily_total_loss += abs(pnl)
                self.consecutive_losses += 1
                if abs(pnl) >= self.max_stop_loss_per_trade:
                    logger.error(f"Stop Loss Triggered! Trade loss {pnl} hit limit {self.max_stop_loss_per_trade}")
                if self.consecutive_losses >= self.consecutive_loss_limit:
                    self.cooldown_until = time.time() + self.cooldown_duration
                    logger.warning(f"Cooldown Triggered for {self.cooldown_duration}s due to {self.consecutive_losses} consecutive losses")
            else:
                self.consecutive_losses = 0

    def is_cooldown_blocked(self, strategy_name, token_id, signal, market_data):
        """
        Check if a given strategy is blocked by market cooldowns for a token.
        Matches the bypass logic in validate_trade exactly.
        """
        current_time = time.time()
        
        # 1. Global Cooldown Check
        if current_time < self.cooldown_until:
            return True
            
        # 2. Per-Market Cooldown Check
        cooldown_info = self.market_cooldowns.get(token_id, 0)
        if isinstance(cooldown_info, dict):
            last_market_trade = cooldown_info.get("timestamp", 0)
            last_strategy = cooldown_info.get("strategy", "PMM")
        else:
            last_market_trade = cooldown_info
            last_strategy = "PMM"

        if (current_time - last_market_trade) < MARKET_COOLDOWN:
            # Cooldown is active. Check if strategy is allowed to bypass.
            bypass_cooldown = False
            
            if strategy_name == "NewsStrategy":
                bypass_cooldown = True
                
            elif strategy_name == "OrderBookImbalanceStrategy":
                if last_strategy != "OrderBookImbalanceStrategy":
                    spread = market_data.get('spread', 1.0) if market_data else 1.0
                    delta = abs(signal.get('delta', 0))
                    price = float(signal.get('price', 1.0))
                    score = signal.get('score', 50.0)
                    
                    delta_pct = delta / price if price > 0 else 0
                    fee_pct = ESTIMATED_FEE_BPS / 10000
                    margin = EV_SAFETY_MARGIN
                    
                    required_edge = spread + (fee_pct * 2) + margin
                    expected_profit = delta_pct - required_edge
                    
                    current_exposure = 0.0
                    if self.pm:
                        open_pos = self.pm.positions.get(token_id, {})
                        current_exposure = open_pos.get('size', 0.0) * open_pos.get('avg_price', price)
                    
                    if expected_profit >= 0.01 and spread <= 0.005 and score >= 85 and current_exposure <= 10.0:
                        bypass_cooldown = True
                        
            elif strategy_name == "MomentumStrategy":
                if last_strategy != "MomentumStrategy":
                    spread = market_data.get('spread', 1.0) if market_data else 1.0
                    delta = abs(signal.get('delta', 0))
                    price = float(signal.get('price', 1.0))
                    score = signal.get('score', 50.0)
                    
                    delta_pct = delta / price if price > 0 else 0
                    fee_pct = ESTIMATED_FEE_BPS / 10000
                    margin = EV_SAFETY_MARGIN
                    
                    required_edge = spread + (fee_pct * 2) + margin
                    expected_profit = delta_pct - required_edge
                    
                    current_exposure = 0.0
                    if self.pm:
                        open_pos = self.pm.positions.get(token_id, {})
                        current_exposure = open_pos.get('size', 0.0) * open_pos.get('avg_price', price)
                    
                    if expected_profit >= 0.01 and spread <= 0.015 and score >= 80 and current_exposure <= 10.0:
                        bypass_cooldown = True
            
            return not bypass_cooldown
            
        return False

    def calculate_inventory_skew(self, token_id, mid_price, side, total_capital=None, gamma_bid=None, gamma_ask=None, market_data=None):
        """
        Calculate asymmetric inventory skew offset for a given market token and side.
        
        If long inventory:
          - BUY orders (entry): skew bid down heavily by gamma_bid * (exposure / total_capital)
          - SELL orders (exit): skew ask down lightly by gamma_ask * (exposure / total_capital)
          
        If opposing leg is long:
          - BUY orders on the unowned leg: skew bid UP by -gamma_ask * (opposing_exposure / total_capital)
            to force a passive execution and close the hedge.
        """
        from .config import GAMMA_BID, GAMMA_ASK, PMM_TOTAL_CAPITAL
        
        cap = total_capital if total_capital is not None else PMM_TOTAL_CAPITAL
        g_bid = gamma_bid if gamma_bid is not None else GAMMA_BID
        g_ask = gamma_ask if gamma_ask is not None else GAMMA_ASK
        
        if not self.pm or not token_id:
            return 0.0
            
        # 1. Standard Skew (Owned token)
        open_pos = self.pm.positions.get(token_id, {})
        pos_size = open_pos.get("size", 0.0)
        
        if pos_size > 0.0:
            # Calculate exposure (YES token position value using mid_price)
            pos_exposure = pos_size * mid_price
            
            # Calculate inventory mismatch ratio I_i relative to our capital pool
            I_i = pos_exposure / cap
            
            # Determine which gamma coefficient to use based on transaction side
            if side == "BUY":
                skew = g_bid * I_i
            else:
                skew = g_ask * I_i
                
            logger.info(
                f"INVENTORY_SKEW (Owned) | token={token_id[:20]} | side={side} | "
                f"exposure={pos_exposure:.2f} USD | ratio={I_i*100:.1f}% | skew={skew:.4f}"
            )
            return skew
            
        # 2. Cross-Leg Skew (Unowned leg, when we hold the opposing outcome leg)
        if market_data and side == "BUY":
            opposing_token_id = None
            for tok in market_data.get("tokens", []):
                tid = tok.get("token_id")
                if tid and str(tid) != str(token_id):
                    opposing_token_id = str(tid)
                    break
                    
            if opposing_token_id:
                opp_pos = self.pm.positions.get(opposing_token_id, {})
                opp_size = opp_pos.get("size", 0.0)
                if opp_size > 0.0:
                    opp_exposure = opp_size * mid_price
                    I_opp = opp_exposure / cap
                    # Skew bid price UP (negative offset since price is signal['price'] - skew)
                    skew = - g_ask * I_opp
                    logger.info(
                        f"INVENTORY_SKEW (Cross-Leg UP) | token={token_id[:20]} | side={side} | "
                        f"opposing_exposure={opp_exposure:.2f} USD | ratio={I_opp*100:.1f}% | skew={skew:.4f}"
                    )
                    return skew
            
        return 0.0

