from datetime import datetime, timedelta, timezone
from .config import (
    MIN_LIQUIDITY, MAX_SPREAD, TEST_MODE, REALISTIC_PAPER_MODE, MIN_DEPTH_USD, MAX_CANDIDATES,
    GLOBAL_MIN_VOLUME, GLOBAL_MAX_SPREAD, GLOBAL_MIN_PRICE, GLOBAL_MAX_PRICE, GLOBAL_MIN_LIQUIDITY,
    ZOMBIE_BID_FLOOR, ZOMBIE_PRICE_CAP, ZOMBIE_MAX_SPREAD
)
from .logger import logger

class MarketFilter:
    def __init__(self, min_liquidity=MIN_LIQUIDITY, max_spread=MAX_SPREAD):
        self.min_liquidity = min_liquidity
        self.max_spread = max_spread
        self.last_prices = {} # For activity filtering: {token_id: last_price}
        self.strict_mode = REALISTIC_PAPER_MODE or not TEST_MODE
        mode_str = "REALISTIC_PAPER" if REALISTIC_PAPER_MODE else ("STRICT_LIVE" if not TEST_MODE else "DEV_TEST")
        logger.info(f"MarketFilter initialized (Mode: {mode_str} | Strict Quality Filters: {self.strict_mode})")

    def filter_markets(self, markets):
        """
        Filter markets based on resolution time and global quality standards.
        """
        market_list = []
        if isinstance(markets, dict):
            if "data" in markets:
                market_list = markets["data"]
            else:
                market_list = list(markets.values())
        elif isinstance(markets, list):
            market_list = markets
        else:
            logger.error(f"Unexpected markets type: {type(markets)}")
            return []

        logger.info(f"Total markets to process: {len(market_list)}")
        
        filtered = []
        now = datetime.now(timezone.utc)
        fourteen_days_later = now + timedelta(days=14)
        
        skipped_stats = {
            "status": 0,
            "time": 0,
            "quality": 0,
            "activity": 0,
            "zombie": 0
        }

        for market in market_list:
            if not isinstance(market, dict):
                continue
            
            # 1. Base Status Check
            if market.get("closed", False) or not market.get("active", True):
                skipped_stats["status"] += 1
                continue

            # 2. Anti-Zombie Pre-Screen (Eliminates penny/dead markets before expensive enrichment)
            tokens = market.get("tokens", [])
            if tokens:
                try:
                    tok_price = float(tokens[0].get("price") or 0.5)
                    rough_spread = float(market.get("spread") or 0.0)
                    if (tok_price < ZOMBIE_BID_FLOOR or tok_price > ZOMBIE_PRICE_CAP or rough_spread > ZOMBIE_MAX_SPREAD) and self.strict_mode:
                        skipped_stats["zombie"] += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # 3. Global Quality Filter (Strict in REALISTIC_PAPER_MODE and LIVE)
            if not self.is_tradeable(market):
                skipped_stats["quality"] += 1
                continue

            # 4. Time Filter (14 days)
            end_date_str = market.get("end_date_iso")
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                    if end_date.tzinfo is None:
                        end_date = end_date.replace(tzinfo=timezone.utc)
                    if end_date > fourteen_days_later and self.strict_mode:
                        skipped_stats["time"] += 1
                        continue
                except Exception as e:
                    logger.warning(f"Error parsing date {end_date_str}: {e}")

            # 5. Activity Filter
            # Verify market has traded recently and isn't dead.
            # In paper/live scanning, Gamma REST prices only update periodically (minutes),
            # so requiring a tick within a single 10s loop would artificially block all markets.
            tokens = market.get("tokens", [])
            if tokens:
                tid = tokens[0].get("token_id")
                curr_price = float(tokens[0].get("price") or 0)
                self.last_prices[tid] = curr_price
                volume_24h = float(market.get("volume24hr") or market.get("volume_24h") or 0)
                if volume_24h <= 0 and self.strict_mode:
                    skipped_stats["activity"] += 1
                    continue

            # Passed all checks
            market['opportunity_score'] = self.calculate_opportunity_score(market)
            filtered.append(market)
            
        # Sort by opportunity score and take top candidates
        filtered.sort(key=lambda x: x.get('opportunity_score', 0), reverse=True)
        top_filtered = filtered[:MAX_CANDIDATES]
        
        logger.info(f"Market Filtering Complete: {len(top_filtered)} candidates selected.")
        logger.info(f"Rejections: {skipped_stats}")
        
        return top_filtered

    def is_tradeable(self, market):
        """
        Senior quantitative filter to ensure we only trade on high-quality markets.
        Enforced strictly in REALISTIC_PAPER_MODE and LIVE mode.
        """
        # If in DEV/TEST mode (and not REALISTIC_PAPER_MODE), relax the strict floors
        if not self.strict_mode:
            return True

        # 1. Volume Check (handles volume24hr, volumeNum, volume_24h, or volume)
        volume_24h = float(market.get("volume24hr") or market.get("volumeNum") or market.get("volume_24h") or market.get("volume") or 0)
        if volume_24h < GLOBAL_MIN_VOLUME:
            return False
            
        # 2. Liquidity Check (handles liquidityNum, liquidity, or liquidityClob)
        liquidity = float(market.get("liquidityNum") or market.get("liquidity") or market.get("liquidityClob") or 0)
        if liquidity < GLOBAL_MIN_LIQUIDITY:
            return False
            
        # 3. Price Range Check
        tokens = market.get("tokens", [])
        if not tokens:
            return False
            
        price = float(tokens[0].get("price") or 0.5)
        if price < GLOBAL_MIN_PRICE or price > GLOBAL_MAX_PRICE:
            return False
            
        # 4. Spread Check (Rough initial check if spread is present, precise check happens after enrichment)
        if "spread" in market and market.get("spread") is not None:
            rough_spread = float(market.get("spread") or 0.0)
            if rough_spread > GLOBAL_MAX_SPREAD + 1e-6:
                return False
            
        return True

    def calculate_opportunity_score(self, market):
        """
        Rank markets by opportunity: volume + volatility + price action.
        """
        score = 0
        
        volume_24h = float(market.get("volume24hr") or market.get("volumeNum") or market.get("volume_24h") or market.get("volume") or 0)
        score += min(10.0, volume_24h / 10000)
        
        change_24h = abs(float(market.get("24hr_change") or market.get("oneDayPriceChange") or 0.0))
        score += change_24h * 20

        liquidity = float(market.get("liquidityNum") or market.get("liquidity") or market.get("liquidityClob") or 0)
        score += min(5.0, liquidity / 5000)
        
        return score
