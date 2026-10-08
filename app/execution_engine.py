from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, PartialCreateOrderOptions, OrderType, OpenOrderParams
from .config import (
    CLOB_HOST, PK, CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE,
    PAPER_TRADING, LIVE_TRADING, CHAIN_ID, TAKER_FEE_BPS, MAKER_FEE_BPS
)
from .logger import logger
from .paper_fill_model import (
    get_min_order_size, get_tick_size, round_to_tick, is_marketable,
    simulate_taker_fill, resting_order_crossed, maker_fee_rate
)
import asyncio
import time

class ExecutionEngine:
    def __init__(self, paper_trading=PAPER_TRADING, live_trading=LIVE_TRADING):
        self.paper_trading = paper_trading
        self.live_trading = live_trading
        self.pending_orders = []  # List of dicts representing active resting orders
        self.tracker = None       # Optional stateful paper trading tracker (Week 2B)
        self.client = None

        # Double Safety Gate: Live CLOB Client is only initialized if BOTH paper_trading is False AND live_trading is True
        if not self.paper_trading and self.live_trading:
            try:
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
                logger.warning("LIVE TRADING ENABLED: ClobClient initialized for REAL capital execution.")
            except Exception as e:
                logger.error(f"Failed to initialize live ClobClient: {e}")
                self.client = None
        else:
            logger.info(f"ExecutionEngine initialized (Paper Trading: {self.paper_trading} | Live Safety Switch: {self.live_trading})")

    def set_tracker(self, tracker):
        """Set stateful paper trading validation tracker."""
        self.tracker = tracker

    async def place_limit_order(self, token_id, price, size, side, market_data=None, is_aggressive=False, is_emergency=False, signal_delta=0, signal_spread=0, strategy=None):
        """
        Place a limit order.
        side: 'BUY' or 'SELL'
        """
        # Double safety gate: Any time paper_trading is True OR live_trading is False, route to paper simulator
        if self.paper_trading or not self.live_trading:
            return self._paper_place_order(
                token_id, price, size, side, market_data,
                is_aggressive, is_emergency, signal_spread, strategy
            )
        
        # --- REAL CLOB EXECUTION PATH ---
        if not self.client:
            logger.critical("LIVE EXECUTION BLOCKED: ClobClient is not initialized or credentials missing.")
            return {"status": "FAILED", "error": "Client not initialized"}

        try:
            logger.info(f"[LIVE] Placing real CLOB order: {side} {size:.2f} @ {price:.4f} for token {token_id}")
            fee_rate = TAKER_FEE_BPS if is_aggressive else MAKER_FEE_BPS
            
            order_args = OrderArgs(
                token_id=str(token_id),
                price=round(float(price), 4),
                size=round(float(size), 2),
                side=side.upper(),
                fee_rate_bps=fee_rate
            )
            
            # Use partial options for tick size if available
            options = PartialCreateOrderOptions(tick_size="0.01")
            
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None, 
                lambda: self.client.create_and_post_order(order_args, options)
            )
            
            if not resp or not isinstance(resp, dict):
                logger.error(f"[LIVE] Empty or invalid response from CLOB: {resp}")
                return {"status": "FAILED", "error": "Invalid CLOB response"}
            
            order_id = resp.get("orderID") or resp.get("order_id") or resp.get("id")
            success = resp.get("success", False) or bool(order_id)
            
            if success and order_id:
                logger.info(f"[LIVE] Real CLOB order successfully posted! OrderID: {order_id}")
                
                # Check immediate fill for aggressive/market orders
                if is_aggressive or is_emergency:
                    status_info = await self.get_order_status(order_id)
                    matched_size = float(status_info.get("size_matched", size)) if status_info else size
                    return {
                        "status": "SUCCESS",
                        "order_id": order_id,
                        "fill_price": float(price),
                        "fill_size": matched_size,
                        "slippage": 0.0,
                        "partial": False,
                        "is_maker": False
                    }
                else:
                    new_order = {
                        "order_id": order_id,
                        "token_id": token_id,
                        "price": float(price),
                        "size": float(size),
                        "side": side,
                        "timestamp": time.time(),
                        "strategy": strategy or "PMM",
                        "is_maker": True
                    }
                    self.pending_orders.append(new_order)
                    return {
                        "status": "PENDING",
                        "order_id": order_id,
                        "price": price,
                        "size": size,
                        "is_maker": True
                    }
            else:
                error_msg = resp.get("errorMsg") or resp.get("error") or "Order rejected by exchange"
                logger.error(f"[LIVE] Order placement rejected by CLOB: {error_msg}")
                return {"status": "FAILED", "error": error_msg}
                
        except Exception as e:
            logger.error(f"[LIVE] Error placing real order on CLOB: {e}")
            return {"status": "FAILED", "error": str(e)}

    def _paper_place_order(self, token_id, price, size, side, market_data, is_aggressive, is_emergency, signal_spread, strategy):
        """
        Simulate how the CLOB would handle this order against the current book
        snapshot (see app/paper_fill_model.py). Aggressive orders behave as FAK:
        whatever cannot be filled within the limit price is dropped.
        """
        if not market_data:
            logger.warning(f"PAPER_REJECT: {side} {token_id} — no market data to simulate a fill against.")
            return {"status": "FAILED", "error": "No market data for paper fill"}

        size = float(size)
        min_size = get_min_order_size(market_data)
        if side == "BUY" and size < min_size:
            logger.warning(f"PAPER_REJECT: BUY {token_id} size {size:.2f} < market minimum {min_size:.2f} shares.")
            return {"status": "FAILED", "error": f"Order size below market minimum ({min_size:g} shares)"}

        aggressive = is_aggressive or is_emergency
        tick = get_tick_size(market_data)
        price = round_to_tick(float(price), tick, side, aggressive=aggressive)
        order_id = f"paper_{token_id}_{side}_{time.time()}"

        if aggressive or is_marketable(side, price, market_data):
            fill = simulate_taker_fill(side, size, price, market_data)
            filled = fill["filled_size"]

            if filled <= 0:
                logger.info(f"PAPER_NO_FILL: {side} {token_id} size {size:.2f} — no liquidity within limit {price:.4f}.")
                if aggressive:
                    return {"status": "FAILED", "error": "No liquidity within limit price"}
            else:
                cost = filled * fill["avg_price"] + fill["fee"]
                if side == "BUY" and self.tracker and cost > self.tracker.virtual_cash + 1e-9:
                    logger.warning(f"PAPER_REJECT: BUY {token_id} cost ${cost:.2f} exceeds paper cash ${self.tracker.virtual_cash:.2f}.")
                    return {"status": "FAILED", "error": "Insufficient paper cash"}

                if self.tracker:
                    self.tracker._update_virtual_position(token_id, filled, fill["avg_price"], side, signal_spread, fee=fill["fee"])

                remainder = size - filled
                partial = remainder > 1e-6
                logger.warning(
                    f"PAPER_TAKER_FILL: {side} {token_id} | filled {filled:.2f}/{size:.2f} @ avg {fill['avg_price']:.4f} "
                    f"(best {fill['best_price']:.4f}, slippage {fill['slippage']:.4f}, levels {fill['levels_used']}) | "
                    f"fee ${fill['fee']:.4f} | emergency={is_emergency}"
                )

                # A passive order that crossed on arrival rests its remainder, like a GTC order on the CLOB
                if partial and not aggressive:
                    self._paper_rest_order(f"{order_id}_rest", token_id, price, remainder, side, strategy, market_data)

                return {
                    "status": "SUCCESS",
                    "order_id": order_id,
                    "fill_price": fill["avg_price"],
                    "fill_size": filled,
                    "slippage": fill["slippage"],
                    "fee": fill["fee"],
                    "partial": partial,
                    "is_maker": False
                }

        return self._paper_rest_order(order_id, token_id, price, size, side, strategy, market_data)

    def _paper_rest_order(self, order_id, token_id, price, size, side, strategy, market_data):
        """Register a passive maker order in the paper ledger."""
        if self.tracker:
            try:
                self.tracker.record_order_placement(order_id, token_id, price, size, side, market_data)
            except ValueError as e:
                return {"status": "FAILED", "error": str(e)}

        self.pending_orders.append({
            "order_id": order_id,
            "token_id": token_id,
            "price": float(price),
            "size": float(size),
            "side": side,
            "timestamp": time.time(),
            "strategy": strategy or "PMM",
            "is_maker": True
        })
        logger.info(f"PLACED PASSIVE LIMIT ORDER [PENDING]: {order_id} | {side} {size:.2f} @ {price:.4f}")
        return {
            "status": "PENDING",
            "order_id": order_id,
            "price": price,
            "size": size,
            "is_maker": True
        }

    async def get_order_status(self, order_id):
        """Fetch order status from Polymarket CLOB."""
        if self.paper_trading or not self.live_trading or not self.client:
            # In paper mode, look up in self.pending_orders
            for o in self.pending_orders:
                if o["order_id"] == order_id:
                    return {"status": "OPEN", "order_id": order_id, "size": o["size"], "price": o["price"]}
            return {"status": "CLOSED", "order_id": order_id}

        try:
            loop = asyncio.get_event_loop()
            order_data = await loop.run_in_executor(None, lambda: self.client.get_order(order_id))
            return order_data
        except Exception as e:
            logger.error(f"Error fetching order status for {order_id}: {e}")
            return None

    async def reconcile_open_orders(self):
        """Reconcile local pending orders with remote Polymarket open orders."""
        if self.paper_trading or not self.live_trading or not self.client:
            return self.pending_orders

        try:
            loop = asyncio.get_event_loop()
            remote_orders = await loop.run_in_executor(None, lambda: self.client.get_orders(OpenOrderParams()))
            remote_ids = {o.get("id") or o.get("orderID") for o in remote_orders if isinstance(o, dict)}
            
            # Keep only local orders that are still active remotely
            self.pending_orders = [o for o in self.pending_orders if o["order_id"] in remote_ids]
            logger.info(f"[RECONCILE] Synced open orders with CLOB: {len(self.pending_orders)} active orders.")
            return self.pending_orders
        except Exception as e:
            logger.error(f"Error reconciling open orders with CLOB: {e}")
            return self.pending_orders

    def check_resting_fills(self, market_data_map):
        """
        Check if any pending resting limit orders can be filled based on latest market data.
        market_data_map: {token_id: token_market_info}
        Returns: list of fill results (dicts with fill status, price, size, slippage, etc.)
        """
        if self.paper_trading and self.tracker:
            # Delegate to our stateful Queue-Priority simulation tracker!
            fills = self.tracker.process_resting_tick(self.pending_orders, market_data_map)
            orders_map = {o["order_id"]: o for o in self.pending_orders}

            # Partial fills shrink the resting order; full fills remove it
            for f in fills:
                order = orders_map.get(f["order_id"])
                if order is not None:
                    order["size"] = max(0.0, order["size"] - f["fill_size"])
            self.pending_orders = [o for o in self.pending_orders if o["size"] > 1e-9]

            standard_fills = []
            for f in fills:
                orig_order = orders_map.get(f["order_id"], {})
                strat_name = orig_order.get("strategy", "PMM")
                
                standard_fills.append({
                    "status": "SUCCESS",
                    "order_id": f["order_id"],
                    "token_id": f["token_id"],
                    "fill_price": f["fill_price"],
                    "fill_size": f["fill_size"],
                    "side": f["side"],
                    "strategy": strat_name,
                    "spread": (market_data_map.get(f["token_id"]) or {}).get("spread", 0.0),
                    "slippage": 0.0,
                    "fee": f.get("fee", 0.0),
                    "is_maker": True
                })
            return standard_fills

        fills = []
        remaining_orders = []

        for order in self.pending_orders:
            token_id = order['token_id']
            price = order['price']
            size = order['size']
            side = order['side']
            order_id = order['order_id']
            
            market_info = market_data_map.get(token_id)
            if not market_info:
                remaining_orders.append(order)
                continue
                
            # Maker orders only fill once the book trades through our price, at our price
            filled = resting_order_crossed(side, price, market_info)
            fill_price = price

            if filled:
                logger.info(f"RESTING ORDER FILLED: {order_id} | Side: {side} | Token: {token_id} | Size: {size:.2f} @ {fill_price:.4f}")
                fills.append({
                    "status": "SUCCESS",
                    "order_id": order_id,
                    "token_id": token_id,
                    "fill_price": fill_price,
                    "fill_size": size,
                    "side": side,
                    "strategy": order.get("strategy", "Unknown"),
                    "spread": market_info.get("spread", 0.0),
                    "slippage": 0.0,
                    "fee": size * fill_price * maker_fee_rate(fill_price, market_info),
                    "is_maker": True
                })
            else:
                remaining_orders.append(order)
                
        self.pending_orders = remaining_orders
        return fills

    async def cancel_order(self, order_id):
        """Cancel a pending resting order by ID."""
        if self.paper_trading or not self.live_trading:
            initial_count = len(self.pending_orders)
            self.pending_orders = [o for o in self.pending_orders if o['order_id'] != order_id]
            found = len(self.pending_orders) < initial_count
            if found and self.tracker:
                self.tracker.record_cancellation(order_id)
            if found:
                logger.info(f"[PAPER] Cancelled pending resting order {order_id}")
            return found

        if not self.client:
            return False

        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: self.client.cancel(order_id))
            self.pending_orders = [o for o in self.pending_orders if o['order_id'] != order_id]
            logger.info(f"[LIVE] Cancelled CLOB order {order_id}: {resp}")
            return True
        except Exception as e:
            logger.error(f"[LIVE] Error cancelling CLOB order {order_id}: {e}")
            return False


