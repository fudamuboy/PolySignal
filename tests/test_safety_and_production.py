import os
import time
import pytest
import asyncio
from app.database import Database
from app.position_manager import PositionManager
from app.execution_engine import ExecutionEngine
from app.safety_layer import SafetyLayer
from app.redis_manager import RedisManager

def test_safety_layer_killswitch(book):
    """Verify that kill switch file detection works and triggers lockdowns."""
    pm = PositionManager()
    pm.positions.clear()
    ee = ExecutionEngine(paper_trading=True)
    safety = SafetyLayer(pm, ee)
    
    # 1. Initially, killswitch should be inactive
    if os.path.exists(safety.kill_switch_file):
        os.remove(safety.kill_switch_file)
        
    async def run_test():
        assert not await safety.is_kill_switch_active()
        
        # Open a dummy position
        pm.positions["dummy_token"] = {"size": 100.0, "avg_price": 0.5, "entry_time": time.time(), "strategy": "PMM"}
        # Place a dummy pending limit order
        await ee.place_limit_order("dummy_token", 0.45, 10.0, "BUY", market_data=book(0.48, 0.52))
        assert len(ee.pending_orders) == 1
        
        # 2. Trigger kill switch
        with open(safety.kill_switch_file, "w") as f:
            f.write("KILL")
            
        assert await safety.is_kill_switch_active()
        
        # 3. Trigger emergency stop
        await safety.trigger_emergency_stop({"dummy_token": book(0.48, 0.52)})
        
        # Orders must be cancelled
        assert len(ee.pending_orders) == 0
        # Position must be closed (size set to 0 in position manager)
        assert pm.positions.get("dummy_token", {}).get("size", 0) == 0
        
        # Clean up
        if os.path.exists(safety.kill_switch_file):
            os.remove(safety.kill_switch_file)
            
    asyncio.run(run_test())


def test_safety_layer_daily_loss():
    """Verify that daily loss checks successfully query the database and trigger caps."""
    db = Database()
    # Clear any old database trades for testing
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trades")
        conn.commit()
        
    safety = SafetyLayer()
    
    async def run_test():
        # No trades -> daily loss is 0 -> check passes
        assert await safety.check_daily_loss()
        
        # Record a loss that violates the cap ($50.00 default)
        db.save_trade("token_a", "SELL", 100, 0.40, "PMM", realized_pnl=-60.00)
        
        # Cap is now hit -> check should return False
        assert not await safety.check_daily_loss()
        
        # Clean up database
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trades")
            conn.commit()
            
    asyncio.run(run_test())


def test_safety_layer_exposure_cap():
    """Verify that exposure cap rules block trades that exceed portfolio limits."""
    pm = PositionManager()
    pm.positions.clear()
    safety = SafetyLayer(position_manager=pm)
    
    async def run_test():
        # No positions -> exposure is 0 -> check passes
        assert await safety.check_exposure_cap(order_value=50.0)
        
        # Open positions up to $180
        pm.positions["token_b"] = {"size": 360.0, "avg_price": 0.5, "entry_time": time.time(), "strategy": "PMM"}
        
        # Adding $30 order value takes exposure to $210 (above $200 limit) -> should return False
        assert not await safety.check_exposure_cap(order_value=30.0)
        
        # Adding $10 order value takes exposure to $190 (under $200 limit) -> should return True
        assert await safety.check_exposure_cap(order_value=10.0)
        
    asyncio.run(run_test())


def test_stale_order_watchdog(book):
    """Verify that stale limit orders are identified and cancelled by watchdog sweeps."""
    ee = ExecutionEngine(paper_trading=True)
    safety = SafetyLayer(execution_engine=ee)
    
    async def run_test():
        # Place a fresh order
        await ee.place_limit_order("token_c", 0.50, 10.0, "BUY", market_data=book(0.49, 0.51))
        assert len(ee.pending_orders) == 1
        
        # Modify the order timestamp to make it stale (older than 60s)
        ee.pending_orders[0]["timestamp"] = time.time() - 120
        
        # Run watchdog sweep
        await safety.run_stale_order_watchdog()
        
        # Watchdog must have cancelled the order
        assert len(ee.pending_orders) == 0
        
    asyncio.run(run_test())
