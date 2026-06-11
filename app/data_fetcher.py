import asyncio
import time
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from .config import (
    CLOB_HOST, PK, CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE, CHAIN_ID,
    DATA_AGE_BLOCK_SECONDS, REST_FRESHNESS_SECONDS, MAX_SIGNAL_DELTA, MAX_PRICE_DRIFT
)
from .logger import logger


class DataFetcher:
    def __init__(self):
        self.client = ClobClient(
            CLOB_HOST,
            chain_id=CHAIN_ID,
            key=PK,
            creds=ApiCreds(
                api_key=CLOB_API_KEY,
                api_secret=CLOB_API_SECRET,
                api_passphrase=CLOB_API_PASSPHRASE
            )
        )
        self.ws = None  # Injected by main
        logger.info("DataFetcher initialized")

    def set_websocket(self, ws_client):
        self.ws = ws_client

    # ------------------------------------------------------------------
    # Market listing
    # ------------------------------------------------------------------
    async def fetch_markets(self):
        """Fetch active markets from the full universe (Gamma API with local fallback)."""
        import urllib.request
        import json
        try:
            logger.info("Scanning full Polymarket active universe via Gamma API...")
            # Fetch top 100 active, open markets
            url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            
            loop = asyncio.get_event_loop()
            def _fetch():
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read().decode())
                    
            data = await loop.run_in_executor(None, _fetch)
            if data:
                # Normalize markets from Gamma format to bot standard format
                normalized_markets = []
                for m in data:
                    if not isinstance(m, dict):
                        continue
                    nm = m.copy()
                    
                    # Reconstruct tokens standard format
                    if "tokens" not in nm and "clobTokenIds" in nm:
                        clob_ids = nm.get("clobTokenIds") or []
                        if isinstance(clob_ids, str):
                            try:
                                clob_ids = json.loads(clob_ids)
                            except Exception:
                                clob_ids = []
                        outcomes = nm.get("outcomes") or []
                        if isinstance(outcomes, str):
                            try:
                                outcomes = json.loads(outcomes)
                            except Exception:
                                outcomes = []
                        prices = nm.get("outcomePrices") or []
                        if isinstance(prices, str):
                            try:
                                prices = json.loads(prices)
                            except Exception:
                                prices = []
                        tokens = []
                        for i, tid in enumerate(clob_ids):
                            outcome = outcomes[i] if i < len(outcomes) else ("Yes" if i == 0 else "No")
                            price = prices[i] if i < len(prices) else "0.5"
                            tokens.append({
                                "token_id": str(tid),
                                "outcome": outcome,
                                "price": price
                            })
                        nm["tokens"] = tokens
                    
                    # Map volume and liquidity keys
                    nm["volume_24h"] = float(nm.get("volume24hr") or nm.get("volumeNum") or nm.get("volume_24h") or 0)
                    nm["liquidity"] = float(nm.get("liquidityNum") or nm.get("liquidity") or nm.get("liquidityClob") or 0)
                    nm["spread"] = float(nm.get("spread") or 1.0)
                    
                    # Compatibility fields
                    nm["end_date_iso"] = nm.get("endDateIso") or nm.get("endDate")
                    nm["active"] = nm.get("active", True)
                    nm["closed"] = nm.get("closed", False)
                    
                    normalized_markets.append(nm)
                
                logger.info(f"Full-Universe Scanner: Successfully normalized {len(normalized_markets)} active markets.")
                return normalized_markets
        except Exception as e:
            logger.warning(f"Gamma API scan failed: {e}. Falling back to local snapshot.")
            
        # Fallback to local snapshot
        try:
            import os
            from .config import BASE_DIR
            local_path = os.path.join(BASE_DIR, "storage", "active_markets_sampling.json")
            if os.path.exists(local_path):
                with open(local_path, "r") as f:
                    local_data = json.load(f)
                    markets = local_data.get("data", [])
                    logger.info(f"Full-Universe Scanner: Loaded {len(markets)} markets from local snapshot.")
                    return markets
        except Exception as local_err:
            logger.error(f"DataFetcher: Fallback local snapshot load failed: {local_err}")
            
        # Standard CLOB API get_sampling_markets fallback
        try:
            logger.info("Falling back to standard CLOB API get_sampling_markets...")
            markets = self.client.get_sampling_markets()
            return markets
        except Exception as clob_err:
            logger.error(f"DataFetcher: All scanner queries failed: {clob_err}")
            return []

    # ------------------------------------------------------------------
    # Orderbook — Redis-first, WS-second, REST fallback
    # ------------------------------------------------------------------
    async def get_orderbook(self, token_id):
        """Get orderbook for a specific token (Redis-first, WebSocket-second, REST fallback)."""
        # Try Redis cache first (populated by WS client in Coordinator/Websocket process)
        try:
            from .redis_manager import RedisManager
            rm = RedisManager()
            cached = await rm.get_orderbook(token_id)
            if cached:
                return cached
        except Exception:
            pass

        # Try WS cache second
        if self.ws:
            cached = self.ws.get_cached_market(token_id)
            if cached and cached.get("orderbook"):
                return cached["orderbook"]

        # WS miss — use REST wrapped in executor to avoid blocking the event loop
        try:
            loop = asyncio.get_event_loop()
            ob = await loop.run_in_executor(None, lambda: self.client.get_order_book(token_id))
            if ob:
                try:
                    from .redis_manager import RedisManager
                    rm = RedisManager()
                    await rm.set_orderbook(token_id, ob)
                except Exception:
                    pass
            return ob
        except Exception as e:
            logger.error(f"Error fetching orderbook for {token_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Price — Redis-first, WS-second, REST fallback
    # ------------------------------------------------------------------
    async def get_market_price(self, token_id):
        """Get current market price for a token (Redis-first, WebSocket-second, REST fallback)."""
        try:
            from .redis_manager import RedisManager
            rm = RedisManager()
            cached = await rm.get_token_price(token_id)
            if cached is not None:
                return cached
        except Exception:
            pass

        if self.ws:
            cached = self.ws.get_cached_market(token_id)
            if cached and cached.get("price"):
                return cached["price"]

        try:
            loop = asyncio.get_event_loop()
            price = await loop.run_in_executor(None, lambda: self.client.get_last_trade_price(token_id))
            p_val = 0.0
            if isinstance(price, dict):
                p_val = float(price.get("price") or price.get("lastPrice") or 0.0)
            else:
                p_val = float(price or 0.0)
            if p_val > 0:
                try:
                    from .redis_manager import RedisManager
                    rm = RedisManager()
                    await rm.set_token_price(token_id, p_val)
                except Exception:
                    pass
            return p_val
        except Exception as e:
            logger.error(f"Error fetching price for {token_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Hybrid orderbook — REST with freshness timestamp
    # Used when WS is stale: returns (orderbook_dict, fetch_time) or None
    # ------------------------------------------------------------------
    async def get_fresh_orderbook_rest(self, token_id):
        """
        Fetch a fresh orderbook directly via REST API.
        Returns a dict with parsed bid/ask/spread/mid + 'fetch_time' timestamp.
        Returns None on failure.
        """
        t0 = time.time()
        try:
            loop = asyncio.get_event_loop()
            ob = await loop.run_in_executor(None, lambda: self.client.get_order_book(token_id))
            fetch_time = time.time()
            elapsed = fetch_time - t0

            raw_bids = ob.get("bids", []) if isinstance(ob, dict) else getattr(ob, "bids", [])
            raw_asks = ob.get("asks", []) if isinstance(ob, dict) else getattr(ob, "asks", [])

            if not raw_bids or not raw_asks:
                return None

            def get_price(level):
                if hasattr(level, "price"):
                    return float(level.price)
                if isinstance(level, dict):
                    return float(level.get("price") or level.get("p") or 0)
                return 0.0

            def get_size(level):
                if hasattr(level, "size"):
                    return float(level.size)
                if isinstance(level, dict):
                    return float(level.get("size") or 0)
                return 0.0

            bids = sorted(raw_bids, key=get_price, reverse=True)
            asks = sorted(raw_asks, key=get_price)

            best_bid = get_price(bids[0])
            best_ask = get_price(asks[0])
            mid_price = (best_bid + best_ask) / 2
            spread = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0

            bid_depth = sum(get_size(b) for b in bids[:2]) * best_bid
            ask_depth = sum(get_size(a) for a in asks[:2]) * best_ask

            return {
                "orderbook":  ob,
                "best_bid":   best_bid,
                "best_ask":   best_ask,
                "mid_price":  mid_price,
                "spread":     spread,
                "bid_depth":  bid_depth,
                "ask_depth":  ask_depth,
                "fetch_time": fetch_time,
                "fetch_latency_ms": elapsed * 1000,
                "source": "REST",
            }
        except Exception as e:
            logger.error(f"REST orderbook fetch failed for {token_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Hybrid data gate — decides WS vs REST, enforces freshness
    # ------------------------------------------------------------------
    async def get_validated_market_data(self, token_id, ws_token_info: dict):
        """
        Returns a validated market snapshot for pre-trade use.

        Logic:
        1. If WS is fresh (< DATA_AGE_BLOCK_SECONDS): use existing enriched data
        2. If WS is stale: attempt REST fallback
           - REST data must arrive within REST_FRESHNESS_SECONDS
           - If REST fails or is stale: return None → block trade
        3. Always logs data source used.

        Returns: dict with market data + 'source' + 'data_age_seconds'
                 or None if no fresh data is available.
        """
        ws_age = self.ws.data_age_seconds if self.ws else float("inf")
        ws_fresh = ws_age <= DATA_AGE_BLOCK_SECONDS

        if ws_fresh:
            # WS is fresh — return existing enriched data augmented with source tag
            result = dict(ws_token_info)
            result["source"] = "WS"
            result["data_age_seconds"] = ws_age
            return result

        # WS is stale — try REST fallback
        logger.info(
            f"WS_STALE_USED_REST: {token_id[:20]}... | "
            f"WS age={ws_age:.1f}s > {DATA_AGE_BLOCK_SECONDS}s | "
            f"Fetching via REST..."
        )

        t_before = time.time()
        rest_data = await self.get_fresh_orderbook_rest(token_id)

        if rest_data is None:
            logger.warning(
                f"REST_FALLBACK_FAILED: {token_id[:20]}... | "
                f"Could not obtain fresh data. Trade blocked."
            )
            return None

        rest_age = time.time() - rest_data["fetch_time"]
        if rest_age > REST_FRESHNESS_SECONDS:
            logger.warning(
                f"REST_DATA_STALE: {token_id[:20]}... | "
                f"REST data age={rest_age:.1f}s > {REST_FRESHNESS_SECONDS}s limit. "
                f"Trade blocked."
            )
            return None

        # Build merged result — REST data takes priority over stale WS
        result = dict(ws_token_info)
        result.update({
            "best_bid":          rest_data["best_bid"],
            "best_ask":          rest_data["best_ask"],
            "spread":            rest_data["spread"],
            "bid_depth":         rest_data["bid_depth"],
            "ask_depth":         rest_data["ask_depth"],
            "orderbook":         rest_data["orderbook"],
            "source":            "REST",
            "data_age_seconds":  rest_age,
            "fetch_latency_ms":  rest_data["fetch_latency_ms"],
        })
        logger.info(
            f"WS_STALE_USED_REST: {token_id[:20]}... | "
            f"REST data fresh ({rest_age:.1f}s old, latency={rest_data['fetch_latency_ms']:.0f}ms) | "
            f"bid={rest_data['best_bid']:.4f} ask={rest_data['best_ask']:.4f} spread={rest_data['spread']:.4f}"
        )
        return result

    # ------------------------------------------------------------------
    # Pre-trade validation — re-fetch REST and compare with signal
    # ------------------------------------------------------------------
    async def pre_trade_validate(self, signal: dict) -> dict:
        """
        Before executing a trade, re-fetch fresh REST data for the token
        and validate the signal is still coherent.

        Returns:
          {'valid': True,  'rest_data': {...}, 'ws_price': float, 'rest_price': float}
          {'valid': False, 'reason': str}
        """
        token_id  = signal.get("token_id")
        sig_price = float(signal.get("price", 0))
        sig_delta = abs(signal.get("delta", 0))

        # ---- Spike filter (Requirement 4) ----
        if sig_delta > MAX_SIGNAL_DELTA:
            reason = (
                f"DELTA_SPIKE_REJECTED: {token_id[:20]}... | "
                f"abs(delta)={sig_delta:.4f} > MAX_SIGNAL_DELTA={MAX_SIGNAL_DELTA} "
                f"(data anomaly / corrupted price)"
            )
            logger.warning(reason)
            return {"valid": False, "reason": reason}

        # ---- Fetch fresh REST data ----
        rest_data = await self.get_fresh_orderbook_rest(token_id)
        if rest_data is None:
            reason = f"REST_VALIDATION_FAIL: {token_id[:20]}... | REST fetch returned nothing"
            logger.warning(reason)
            return {"valid": False, "reason": reason}

        rest_age = time.time() - rest_data["fetch_time"]
        if rest_age > REST_FRESHNESS_SECONDS:
            reason = (
                f"REST_VALIDATION_FAIL: {token_id[:20]}... | "
                f"REST data too old ({rest_age:.1f}s > {REST_FRESHNESS_SECONDS}s)"
            )
            logger.warning(reason)
            return {"valid": False, "reason": reason}

        rest_mid   = rest_data["mid_price"]
        rest_spread = rest_data["spread"]

        # ---- Price drift check ----
        price_drift = abs(rest_mid - sig_price) / sig_price if sig_price > 0 else 1.0
        if price_drift > MAX_PRICE_DRIFT:
            reason = (
                f"REST_VALIDATION_FAIL: {token_id[:20]}... | "
                f"Price drift {price_drift*100:.2f}% > {MAX_PRICE_DRIFT*100:.0f}% | "
                f"signal={sig_price:.4f} REST_mid={rest_mid:.4f}"
            )
            logger.warning(reason)
            return {"valid": False, "reason": reason, "price_drift_pct": price_drift * 100}

        # ---- Recomputed delta ----
        # Delta is the difference between current REST price and signal price
        # (signal price was computed from an earlier snapshot)
        recomputed_delta = abs(rest_mid - sig_price)

        logger.info(
            f"REST_VALIDATION_PASS: {token_id[:20]}... | "
            f"signal_price={sig_price:.4f} REST_mid={rest_mid:.4f} "
            f"drift={price_drift*100:.2f}% | "
            f"spread={rest_spread:.4f} | "
            f"WS_vs_REST_diff={price_drift*100:.3f}%"
        )

        return {
            "valid":             True,
            "rest_data":         rest_data,
            "ws_price":          sig_price,
            "rest_price":        rest_mid,
            "rest_spread":       rest_spread,
            "recomputed_delta":  recomputed_delta,
            "price_drift_pct":   price_drift * 100,
        }
