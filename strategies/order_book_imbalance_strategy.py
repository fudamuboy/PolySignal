import math
from app.signal_engine import BaseStrategy
from app.logger import logger
from app.config import (
    TEST_MODE, GLOBAL_MIN_PRICE, GLOBAL_MAX_PRICE,
    GLOBAL_MIN_LIQUIDITY, GLOBAL_MAX_SPREAD
)

# ── OBI Quality Constants ────────────────────────────────────────────────────
# Raised from 0.65: only trade when bids overwhelmingly dominate the book
OBI_IMBALANCE_THRESHOLD      = 0.80   # min OBI ratio required
OBI_DEPTH_RATIO_MIN          = 3.0    # bid_depth must be >= 3x ask_depth
OBI_MAX_SPREAD               = 0.005  # 0.5% — tight book only (filters stale/wide books)
OBI_MIN_DEPTH_USD            = 500.0  # minimum total depth in USD
OBI_MIN_DEPTH_USD_TEST       = 100.0  # relaxed floor for paper/test mode
OBI_SCORE_FLOOR              = 85     # force aggressive classification (honest taker EV cost)
# Delta estimate: log depth ratio scaled to expected price impact
# Polymarket prices move roughly 1–3% per 10x depth imbalance
# We use ln(bid/ask) * 0.04 as a conservative impact estimate
OBI_DELTA_LOG_SCALE          = 0.04


class OrderBookImbalanceStrategy(BaseStrategy):
    """
    Order Book Imbalance (OBI) Strategy — v2 (Tightened)

    Entry logic:
      1. OBI ratio >= 0.80 (strong buy-side dominance)
      2. bid_depth >= 3x ask_depth (depth ratio confirmation)
      3. Spread <= 0.5% (tight, active book — not stale)
      4. Total depth >= $500 USD (sufficient liquidity)
      5. Delta = ln(bid/ask) * scale — realistic expected impact
      6. Score >= 85 — ensures EV calc charges taker spread cost
    """
    def __init__(self):
        super().__init__("OrderBookImbalanceStrategy")
        self.imbalance_threshold = OBI_IMBALANCE_THRESHOLD

    async def evaluate(self, market_data):
        signals = []
        for market in market_data:
            token_id      = market.get("token_id")
            bid_depth     = market.get("bid_depth", 0.0)
            ask_depth     = market.get("ask_depth", 0.0)
            current_price = market.get("last_price")
            best_bid      = market.get("best_bid", 0.0)
            best_ask      = market.get("best_ask", 0.0)

            if token_id is None or current_price is None:
                continue

            # ── Filter 1: Minimum total depth ───────────────────────────
            total_depth = bid_depth + ask_depth
            min_depth   = OBI_MIN_DEPTH_USD_TEST if TEST_MODE else OBI_MIN_DEPTH_USD
            if total_depth < min_depth:
                continue

            # ── Filter 2: OBI ratio ─────────────────────────────────────
            denominator = total_depth
            obi = (bid_depth - ask_depth) / denominator if denominator > 0 else 0.0

            threshold = 0.55 if TEST_MODE else self.imbalance_threshold
            if abs(obi) < threshold:
                continue

            # ── Filter 3: Only trade buy-side pressure ──────────────────
            if obi < 0:
                continue

            # ── Filter 4: Depth ratio (bid >= N× ask) ───────────────────
            min_ratio = 1.5 if TEST_MODE else OBI_DEPTH_RATIO_MIN
            if ask_depth <= 0 or (bid_depth / ask_depth) < min_ratio:
                logger.debug(
                    f"OBI SKIP (depth_ratio): {token_id[:20]} | "
                    f"bid/ask ratio {bid_depth/max(ask_depth,0.01):.1f}x < {min_ratio}x"
                )
                continue

            # ── Filter 5: Spread must be tight ──────────────────────────
            if best_bid > 0 and best_ask > 0:
                abs_spread  = best_ask - best_bid
                mid_price   = (best_bid + best_ask) / 2.0
                spread_pct  = abs_spread / mid_price if mid_price > 0 else 1.0
            else:
                abs_spread  = 0.05
                spread_pct  = 1.0

            max_spread = 0.02 if TEST_MODE else OBI_MAX_SPREAD
            if spread_pct > max_spread:
                logger.debug(
                    f"OBI SKIP (spread): {token_id[:20]} | "
                    f"spread {spread_pct*100:.2f}% > {max_spread*100:.1f}% limit"
                )
                continue

            # ── Filter 6: Price range (0.20 to 0.75) ────────────────────
            # Skip markets priced above 0.75 or below 0.20
            if current_price < 0.20 or current_price > 0.75:
                logger.debug(
                    f"OBI SKIP (price_range): {token_id[:20]} | "
                    f"price {current_price:.4f} outside mid-range [0.20, 0.75]"
                )
                continue

            # ── Delta: log-depth-ratio impact estimate ──────────────────
            # ln(bid/ask) captures exponential depth dominance;
            # scale converts to expected price-move fraction.
            depth_ratio = bid_depth / max(ask_depth, 0.01)
            log_delta   = math.log(depth_ratio) * OBI_DELTA_LOG_SCALE
            delta       = min(log_delta, 0.15)  # cap at 15% (anomaly guard)

            # ── Score: force >= OBI_SCORE_FLOOR so EV charges taker cost─
            # OBI fills are market takers; score<85 gives an unfair maker
            # discount. We clamp to floor to make EV evaluation honest.
            raw_score = 50 + int(abs(obi) * 50)
            score     = max(OBI_SCORE_FLOOR, min(100, raw_score))

            logger.info(
                f"OrderBookImbalanceStrategy SIGNAL: {token_id[:20]} | "
                f"OBI: {obi:+.3f} | DepthRatio: {depth_ratio:.1f}x | "
                f"Spread: {spread_pct*100:.3f}% | Delta: {delta:+.4f} | "
                f"BidDepth: {bid_depth:.0f} | AskDepth: {ask_depth:.0f} | Score: {score}"
            )

            signals.append({
                "token_id": token_id,
                "price":    current_price,
                "size":     10,
                "side":     "BUY",
                "delta":    delta,
                "spread":   abs_spread,
                "liquidity":total_depth,
                "score":    score,
                "strategy": self.name,
                "reason":   f"OBI={obi:+.3f} DepthRatio={depth_ratio:.1f}x Spread={spread_pct*100:.2f}%"
            })

        return signals
