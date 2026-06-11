import os
import time
from .config import DAILY_MAX_LOSS_LIMIT, MAX_EXPOSURE_CAP, STALE_ORDER_TIMEOUT_SECONDS
from .logger import logger
from .database import Database
from .redis_manager import RedisManager

class SafetyLayer:
    def __init__(self, position_manager=None, execution_engine=None, redis_manager=None):
        self.pm = position_manager
        self.ee = execution_engine
        self.rm = redis_manager or RedisManager()
        self.db = Database()
        self.kill_switch_file = "storage/killswitch.signal"

    async def is_kill_switch_active(self) -> bool:
        """Check if kill switch is triggered via local file or Redis."""
        if os.path.exists(self.kill_switch_file):
            return True
        try:
            client = await self.rm.connect()
            val = await client.get("killswitch")
            if val == "1":
                return True
        except Exception as e:
            # Don't spam connection logs
            pass
        return False

    async def trigger_emergency_stop(self):
        """Cancel all pending orders and exit/close positions."""
        logger.critical("EMERGENCY KILL SWITCH ACTIVE! Initiating safety lockdown...")
        
        # 1. Cancel all resting limit orders
        if self.ee:
            pending = list(self.ee.pending_orders)
            for order in pending:
                try:
                    await self.ee.cancel_order(order["order_id"])
                    logger.warning(f"SafetyLayer: Cancelled pending order {order['order_id']}")
                except Exception as e:
                    logger.error(f"SafetyLayer: Error cancelling order {order['order_id']}: {e}")

        # 2. Market close all open positions
        if self.pm and self.ee:
            positions = list(self.pm.positions.items())
            for token_id, pos in positions:
                size = pos.get("size", 0.0)
                if size > 0:
                    try:
                        logger.warning(f"SafetyLayer: Liquidating emergency exit for {token_id} (size={size})")
                        # Place immediate aggressive limit order to exit
                        await self.ee.place_limit_order(
                            token_id=token_id,
                            price=0.001,  # Force immediate sell taker execution
                            size=size,
                            side="SELL",
                            is_aggressive=True,
                            is_emergency=True,
                            strategy="EMERGENCY_EXIT"
                        )
                        self.pm.update_position(token_id, size, 0.001, side="SELL", strategy="EMERGENCY_EXIT")
                    except Exception as e:
                        logger.error(f"SafetyLayer: Failed emergency position close for {token_id}: {e}")

    async def check_daily_loss(self) -> bool:
        """Verify that total realized loss today does not exceed the limit."""
        try:
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                if self.db.is_postgresql:
                    cursor.execute("""
                        SELECT SUM(realized_pnl) FROM trades 
                        WHERE realized_pnl < 0 AND timestamp >= CURRENT_DATE
                    """)
                else:
                    cursor.execute("""
                        SELECT SUM(realized_pnl) FROM trades 
                        WHERE realized_pnl < 0 AND timestamp >= date('now')
                    """)
                row = cursor.fetchone()
                daily_loss = abs(row[0]) if row and row[0] is not None else 0.0
                
            if daily_loss >= DAILY_MAX_LOSS_LIMIT:
                logger.critical(f"SafetyLayer: Daily loss limit hit! Today's Loss: ${daily_loss:.2f} >= Limit: ${DAILY_MAX_LOSS_LIMIT:.2f}")
                return False
        except Exception as e:
            logger.error(f"SafetyLayer: Error checking daily loss: {e}")
        return True

    async def check_exposure_cap(self, order_value: float = 0.0) -> bool:
        """Verify that total position exposure doesn't exceed cap."""
        if not self.pm:
            return True
        try:
            current_exposure = 0.0
            for tid, pos in self.pm.positions.items():
                current_exposure += pos.get("size", 0.0) * pos.get("avg_price", 0.5)
            
            if current_exposure + order_value > MAX_EXPOSURE_CAP:
                logger.warning(f"SafetyLayer: Order blocked. Total exposure ${current_exposure + order_value:.2f} exceeds cap ${MAX_EXPOSURE_CAP:.2f}")
                return False
        except Exception as e:
            logger.error(f"SafetyLayer: Error checking exposure cap: {e}")
        return True

    async def run_stale_order_watchdog(self):
        """Cancel passive orders that have been resting for too long."""
        if not self.ee:
            return
        current_time = time.time()
        stale_orders = []
        for order in self.ee.pending_orders:
            elapsed = current_time - order.get("timestamp", current_time)
            if elapsed > STALE_ORDER_TIMEOUT_SECONDS:
                stale_orders.append(order)

        for order in stale_orders:
            logger.warning(
                f"STALE_ORDER_WATCHDOG: Resting order {order['order_id']} "
                f"for token {order['token_id']} has been active for {current_time - order['timestamp']:.1f}s. "
                f"Cancelling stale order."
            )
            await self.ee.cancel_order(order["order_id"])
