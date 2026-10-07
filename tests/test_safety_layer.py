import pytest
import os
import time
from unittest.mock import MagicMock, AsyncMock, patch
from app.safety_layer import SafetyLayer
from app.config import DAILY_MAX_LOSS_LIMIT, MAX_EXPOSURE_CAP, STALE_ORDER_TIMEOUT_SECONDS, DATA_AGE_BLOCK_SECONDS

@pytest.mark.asyncio
async def test_safety_layer_kill_switch_file():
    safety = SafetyLayer()
    safety.kill_switch_file = "storage/test_killswitch.signal"
    
    # Not active
    if os.path.exists(safety.kill_switch_file):
        os.remove(safety.kill_switch_file)
    assert not await safety.is_kill_switch_active()
    
    # Active via file
    with open(safety.kill_switch_file, "w") as f:
        f.write("1")
    assert await safety.is_kill_switch_active()
    
    # Clean up
    os.remove(safety.kill_switch_file)
    assert not await safety.is_kill_switch_active()

@pytest.mark.asyncio
async def test_safety_layer_emergency_stop():
    mock_ee = MagicMock()
    mock_ee.pending_orders = [{"order_id": "order_123"}]
    mock_ee.cancel_order = AsyncMock(return_value=True)
    mock_ee.place_limit_order = AsyncMock(return_value={"status": "SUCCESS"})
    
    mock_pm = MagicMock()
    mock_pm.positions = {"token_abc": {"size": 10.0, "avg_price": 0.5}}
    mock_pm.update_position = MagicMock()
    
    safety = SafetyLayer(position_manager=mock_pm, execution_engine=mock_ee)
    await safety.trigger_emergency_stop()
    
    mock_ee.cancel_order.assert_called_with("order_123")
    mock_ee.place_limit_order.assert_called_once()
    mock_pm.update_position.assert_called_once()

@pytest.mark.asyncio
async def test_safety_layer_exposure_cap():
    mock_pm = MagicMock()
    mock_pm.positions = {
        "tok_1": {"size": 100.0, "avg_price": 0.5},  # $50
        "tok_2": {"size": 200.0, "avg_price": 0.5},  # $100 -> Total $150
    }
    safety = SafetyLayer(position_manager=mock_pm)
    
    # MAX_EXPOSURE_CAP is 200. $150 + $30 = $180 <= 200 -> OK
    assert await safety.check_exposure_cap(order_value=30.0) is True
    
    # $150 + $60 = $210 > 200 -> Blocked
    assert await safety.check_exposure_cap(order_value=60.0) is False

@pytest.mark.asyncio
async def test_safety_layer_stale_order_watchdog():
    mock_ee = MagicMock()
    now = time.time()
    mock_ee.pending_orders = [
        {"order_id": "fresh_1", "token_id": "tok_1", "timestamp": now - 10},
        {"order_id": "stale_1", "token_id": "tok_2", "timestamp": now - (STALE_ORDER_TIMEOUT_SECONDS + 10)}
    ]
    mock_ee.cancel_order = AsyncMock(return_value=True)
    
    safety = SafetyLayer(execution_engine=mock_ee)
    await safety.run_stale_order_watchdog()
    
    mock_ee.cancel_order.assert_called_once_with("stale_1")

@pytest.mark.asyncio
async def test_safety_layer_ws_health():
    mock_ws = MagicMock()
    
    # Fresh WS
    mock_ws.data_age_seconds = 5.0
    safety = SafetyLayer()
    assert await safety.check_ws_health(mock_ws) is True
    
    # Critically stale WS (> 3x DATA_AGE_BLOCK_SECONDS)
    mock_ws.data_age_seconds = DATA_AGE_BLOCK_SECONDS * 3 + 1
    assert await safety.check_ws_health(mock_ws) is False
