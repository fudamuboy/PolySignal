import asyncio
import json
import time
import websockets
from .logger import logger
from .config import WS_STALE_TIMEOUT, DATA_AGE_BLOCK_SECONDS

class WebsocketClient:
    def __init__(self, uri="wss://ws-subscriptions-clob.polymarket.com/ws/market"):
        self.uri = uri
        self.cache = {} # token_id: {orderbook, price, timestamp}
        self.subscriptions = set()
        self.running = False
        self._loop = None
        self.last_message_time = time.time()  # wall-clock time of last message
        self._reconnect_count = 0

        # ── Diagnostic counters ──────────────────────────────────────────
        self._total_messages_received = 0   # lifetime message count
        self._minute_message_count    = 0   # messages in current 1-min window
        self._minute_window_start     = time.time()  # start of current window
        self._last_sub_success        = False  # did last subscribe() send succeed?
        self._msg_type_counts         = {}   # {event_type_str: count} for diagnostics

    @property
    def data_age_seconds(self) -> float:
        """Seconds since the last WS message was received."""
        return time.time() - self.last_message_time

    @property
    def is_data_fresh(self) -> bool:
        """True if last message arrived within DATA_AGE_BLOCK_SECONDS."""
        return self.data_age_seconds <= DATA_AGE_BLOCK_SECONDS

    async def connect(self):
        """Connect to the websocket and start the message loop."""
        self.running = True
        # Fix #5: reduced stale timeout from 60 → WS_STALE_TIMEOUT (30s)
        
        while self.running:
            try:
                async with websockets.connect(self.uri) as websocket:
                    self._ws = websocket
                    self._reconnect_count += 1
                    self.last_message_time = time.time()
                    logger.info(f"WS_RECONNECTED (attempt #{self._reconnect_count}): {self.uri}")
                    
                    # Re-subscribe if we have existing subs
                    if self.subscriptions:
                        await self._subscribe_all(websocket)
                    else:
                        logger.warning(
                            f"WS_RECONNECTED_NO_SUBS (attempt #{self._reconnect_count}): "
                            f"subscriptions set is empty — no tokens registered yet."
                        )
                    
                    while self.running:
                        try:
                            # Wait for message with short recv timeout for responsiveness
                            message = await asyncio.wait_for(websocket.recv(), timeout=10)
                            self.last_message_time = time.time()
                            self._total_messages_received += 1
                            self._minute_message_count    += 1
                            await self._handle_message(message)
                        except asyncio.TimeoutError:
                            elapsed = time.time() - self.last_message_time
                            if elapsed > WS_STALE_TIMEOUT:
                                logger.warning(
                                    f"WS_STALE_TRIGGERED: No message in {elapsed:.0f}s "
                                    f"(threshold={WS_STALE_TIMEOUT}s). "
                                    f"Forcing reconnect to restore data feed."
                                )
                                break  # triggers outer reconnect loop
                            # Send heartbeat ping to keep connection alive
                            try:
                                await websocket.ping()
                            except Exception:
                                pass  # will reconnect on next timeout
                        except Exception as e:
                            logger.error(f"WS Loop error: {e}")
                            break
            except Exception as e:
                logger.error(f"WS Connection failed: {e}. Retrying in 5s...")
                await asyncio.sleep(5)
            finally:
                self._ws = None

    async def subscribe(self, token_ids):
        """Subscribe to specific token updates."""
        new_ids = [str(tid) for tid in token_ids if str(tid) not in self.subscriptions]
        for tid in new_ids:
            self.subscriptions.add(tid)

        if new_ids and getattr(self, "_ws", None) is not None:
            try:
                msg = {
                    "type": "subscribe",
                    "assets_ids": new_ids
                }
                await self._ws.send(json.dumps(msg))
                logger.info(f"WS_DYNAMIC_SUBSCRIBE: Subscribed {len(new_ids)} tokens to active WebSocket.")
            except Exception as e:
                logger.debug(f"WS dynamic subscribe error: {e}")

    async def _subscribe_all(self, websocket):
        """Send subscription messages for all tracked tokens."""
        msg = {
            "type": "subscribe",
            "assets_ids": list(self.subscriptions)
        }
        try:
            await websocket.send(json.dumps(msg))
            self._last_sub_success = True
            logger.info(
                f"WS_SUBSCRIBED (attempt #{self._reconnect_count}): "
                f"{len(self.subscriptions)} tokens | "
                f"msg={json.dumps(msg)[:120]}..."
            )
        except Exception as e:
            self._last_sub_success = False
            logger.error(
                f"WS_SUBSCRIBE_FAILED (attempt #{self._reconnect_count}): "
                f"{e} | tokens={len(self.subscriptions)}"
            )

    async def _handle_message(self, message):
        """Process incoming WS messages and update cache."""
        try:
            raw_data = json.loads(message)

            # Polymarket sometimes sends a list of messages
            data_list = raw_data if isinstance(raw_data, list) else [raw_data]

            for data in data_list:
                # Polymarket WS uses 'event_type' — NOT 'event' or 'type'
                msg_type = (
                    data.get("event_type")
                    or data.get("event")
                    or data.get("type")
                    or "unknown"
                )

                # Track per-type message counts for WS diagnostics
                self._msg_type_counts[msg_type] = self._msg_type_counts.get(msg_type, 0) + 1

                asset_id = data.get("asset_id")

                if not asset_id:
                    # price_changes messages may not have asset_id at top level
                    # They are broadcast-style; skip for now (no per-token update)
                    continue

                if asset_id not in self.cache:
                    self.cache[asset_id] = {"orderbook": {}, "price": 0, "last_update": 0}

                now = asyncio.get_event_loop().time()

                # ── Book snapshot (initial full book sent on subscribe) ──────
                # Polymarket sends full bids/asks on first connect per token
                bids = data.get("bids")
                asks = data.get("asks")
                if bids is not None:
                    self.cache[asset_id]["orderbook"]["bids"] = bids
                    self.cache[asset_id]["last_update"] = now
                if asks is not None:
                    self.cache[asset_id]["orderbook"]["asks"] = asks
                    self.cache[asset_id]["last_update"] = now

                # ── last_trade_price ─────────────────────────────────────────
                ltp = data.get("last_trade_price")
                if ltp:
                    try:
                        self.cache[asset_id]["price"] = float(ltp)
                        self.cache[asset_id]["last_update"] = now
                    except (ValueError, TypeError):
                        pass

                # ── price_changes (incremental book update messages) ─────────
                # Format: [{"price": "0.55", "size": "100", "side": "BUY"}, ...]
                price_changes = data.get("price_changes")
                if price_changes and isinstance(price_changes, list):
                    ob = self.cache[asset_id]["orderbook"]
                    existing_bids = {str(b["price"]): b for b in ob.get("bids", []) if isinstance(b, dict)}
                    existing_asks = {str(a["price"]): a for a in ob.get("asks", []) if isinstance(a, dict)}
                    for change in price_changes:
                        if not isinstance(change, dict):
                            continue
                        price_str = str(change.get("price", ""))
                        size      = change.get("size", "0")
                        side      = change.get("side", "").upper()
                        if side == "BUY":
                            if float(size) == 0:
                                existing_bids.pop(price_str, None)
                            else:
                                existing_bids[price_str] = {"price": price_str, "size": size}
                        elif side == "SELL":
                            if float(size) == 0:
                                existing_asks.pop(price_str, None)
                            else:
                                existing_asks[price_str] = {"price": price_str, "size": size}
                    ob["bids"] = list(existing_bids.values())
                    ob["asks"] = list(existing_asks.values())
                    self.cache[asset_id]["last_update"] = now

                # Sync to shared Redis cache for inter-process access
                try:
                    from .redis_manager import RedisManager
                    rm = RedisManager()
                    ob_data = self.cache[asset_id].get("orderbook")
                    if ob_data:
                        await rm.set_orderbook(asset_id, ob_data)
                    price_val = self.cache[asset_id].get("price")
                    if price_val:
                        await rm.set_token_price(asset_id, price_val)
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error handling WS message: {e}")

    def get_cached_market(self, asset_id):
        """Retrieve data from cache if fresh."""
        return self.cache.get(asset_id)

    def get_ws_diagnostic_snapshot(self) -> dict:
        """
        Returns a dict with current WS health metrics for the
        WS_SUBSCRIPTION_REPORT log line. Resets the per-minute counter.
        """
        now = time.time()
        elapsed = now - self._minute_window_start
        rate = (self._minute_message_count / elapsed * 60) if elapsed > 0 else 0

        top_types = sorted(self._msg_type_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        snapshot = {
            "subscribed_tokens":    len(self.subscriptions),
            "cache_populated":      len(self.cache),
            "reconnects":           self._reconnect_count,
            "last_sub_success":     self._last_sub_success,
            "total_messages":       self._total_messages_received,
            "messages_per_min":     round(rate, 1),
            "last_message_age_s":   round(self.data_age_seconds, 1),
            "msg_types":            dict(top_types),
        }
        # Reset per-minute window
        self._minute_message_count = 0
        self._minute_window_start  = now
        return snapshot

    def stop(self):
        self.running = False
