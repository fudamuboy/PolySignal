from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from .config import CLOB_HOST, PK, CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE, PAPER_TRADING, CHAIN_ID, ESTIMATED_FEE_BPS
from .logger import logger

class ExecutionEngine:
    def __init__(self, paper_trading=PAPER_TRADING):
        self.paper_trading = paper_trading
        self.pending_orders = []  # List of dicts representing active resting orders
        self.tracker = None       # Optional stateful paper trading tracker (Week 2B)
        if not self.paper_trading:
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
        logger.info(f"ExecutionEngine initialized (Paper Trading: {self.paper_trading})")

    def set_tracker(self, tracker):
        """Set stateful paper trading validation tracker."""
        self.tracker = tracker

    async def place_limit_order(self, token_id, price, size, side, market_data=None, is_aggressive=False, is_emergency=False, signal_delta=0, signal_spread=0, strategy=None):
        """
        Place a limit order.
        side: 'BUY' or 'SELL'
        """
        if self.paper_trading:
            import time
            order_id = f"paper_{token_id}_{side}_{time.time()}"
            
            # If is_emergency or is_aggressive, we bypass resting order ledger and force immediate fill
            if is_emergency or is_aggressive:
                fill_price = price if not (side == 'SELL' and price == 0.001) else 0.001
                log_type = "EMERGENCY FORCE" if is_emergency else "AGGRESSIVE TAKER"
                logger.warning(f"{log_type} FILL: {side} {token_id} size {size} executed at {fill_price:.4f} to clear position.")
                if self.tracker:
                    self.tracker._update_virtual_position(token_id, size, fill_price, side, signal_spread)
                return {
                    "status": "SUCCESS", 
                    "order_id": order_id,
                    "fill_price": fill_price,
                    "fill_size": size,
                    "slippage": 0.0,
                    "partial": False
                }

            # Register as passive maker resting order
            new_order = {
                "order_id": order_id,
                "token_id": token_id,
                "price": float(price),
                "size": float(size),
                "side": side,
                "timestamp": time.time(),
                "strategy": strategy or "PMM"
            }
            self.pending_orders.append(new_order)
            logger.info(f"PLACED PASSIVE LIMIT ORDER [PENDING]: {order_id} | {side} {size:.2f} @ {price:.4f}")
            
            # Hook into paper trading tracker to estimate queue position depth
            if self.tracker:
                self.tracker.record_order_placement(order_id, token_id, price, size, side, market_data)
                
            return {
                "status": "PENDING",
                "order_id": order_id,
                "price": price,
                "size": size
            }
        
        try:
            logger.info(f"Placing {side} order: {size} @ {price} for {token_id}")
            # Real execution logic would go here
            return {"status": "SUCCESS"} # Placeholder
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {"status": "FAILED", "error": str(e)}

    def check_resting_fills(self, market_data_map):
        """
        Check if any pending resting limit orders can be filled based on latest market data.
        market_data_map: {token_id: token_market_info}
        Returns: list of fill results (dicts with fill status, price, size, slippage, etc.)
        """
        if self.paper_trading and self.tracker:
            # Delegate to our stateful Queue-Priority simulation tracker!
            fills = self.tracker.process_resting_tick(self.pending_orders, market_data_map)
            # Make sure we clean up filled orders from self.pending_orders
            filled_ids = {f["order_id"] for f in fills}
            orders_map = {o["order_id"]: o for o in self.pending_orders if o["order_id"] in filled_ids}
            
            self.pending_orders = [o for o in self.pending_orders if o["order_id"] not in filled_ids]
            
            # Convert tracker's fills format to match ExecutionEngine's standard format
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
                    "spread": market_data_map[f["token_id"]].get("spread", 0.0),
                    "slippage": 0.0
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
                
            best_bid = float(market_info.get("best_bid") or 0.0)
            best_ask = float(market_info.get("best_ask") or 0.0)
            last_price = float(market_info.get("last_price") or 0.0)
            
            filled = False
            fill_price = price
            
            # Conservative Maker Fill Check:
            # - For a BUY limit order at price P: it is filled if the best ask drops to P or below,
            #   or if the market trades at or below P.
            # - For a SELL limit order at price P: it is filled if the best bid rises to P or above,
            #   or if the market trades at or above P.
            if side == 'BUY':
                if 0.0 < best_ask <= price:
                    filled = True
                    fill_price = best_ask
                elif 0.0 < last_price <= price:
                    filled = True
                    fill_price = last_price
            elif side == 'SELL':
                if best_bid >= price > 0.0:
                    filled = True
                    fill_price = best_bid
                elif 0.0 < last_price >= price:
                    filled = True
                    fill_price = last_price
                    
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
                    "slippage": 0.0  # Limit maker orders have no slippage
                })
            else:
                remaining_orders.append(order)
                
        self.pending_orders = remaining_orders
        return fills

    async def cancel_order(self, order_id):
        """Cancel a pending resting order by ID."""
        if self.paper_trading:
            initial_count = len(self.pending_orders)
            self.pending_orders = [o for o in self.pending_orders if o['order_id'] != order_id]
            found = len(self.pending_orders) < initial_count
            if found:
                logger.info(f"[PAPER] Cancelled pending resting order {order_id}")
            return found
        return True

