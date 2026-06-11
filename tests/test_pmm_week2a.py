import pytest
import sqlite3
from app.position_manager import PositionManager
from app.database import Database

def test_inventory_metrics_calculation():
    print("Testing Inventory Metrics Calculation...")
    pm = PositionManager()
    
    # Clean DB and memory for test
    pm.clear_all_positions()
    pm.max_exposure_observed = 0.0
    pm.total_exposure_sum = 0.0
    pm.ticks_count = 0
    
    # 1. Setup mock positions in memory
    # Position A: 100 shares @ 0.50
    # Position B: 50 shares @ 0.60
    pm.positions = {
        "token_x": {"size": 100.0, "avg_price": 0.50, "strategy": "PMM"},
        "token_y": {"size": 50.0, "avg_price": 0.60, "strategy": "PMM"}
    }
    
    # Mock enriched markets with prices
    enriched_markets = [
        {"token_id": "token_x", "last_price": 0.52}, # value = 100 * 0.52 = 52.0 USD
        {"token_id": "token_y", "last_price": 0.60}  # value = 50 * 0.60 = 30.0 USD
    ]
    
    # 2. Run metrics update
    metrics = pm.update_inventory_metrics(enriched_markets)
    
    # Expected Exposure: 52.0 + 30.0 = 82.0 USD
    assert metrics["total_exposure"] == 82.0
    
    # Expected Concentration: Max single exposure is 52.0 on token_x.
    # Concentration ratio = 52.0 / 82.0 = 0.6341
    assert metrics["max_position_exposure"] == 52.0
    assert abs(metrics["concentration"] - (52.0 / 82.0)) < 1e-4
    
    # Expected session states
    assert metrics["max_exposure_observed"] == 82.0
    assert metrics["avg_exposure"] == 82.0
    
    # 3. Add another tick with different prices to verify running average/max
    enriched_markets_2 = [
        {"token_id": "token_x", "last_price": 0.50}, # value = 100 * 0.50 = 50.0 USD
        {"token_id": "token_y", "last_price": 0.56}  # value = 50 * 0.56 = 28.0 USD
    ]
    # Expected Exposure 2: 50.0 + 28.0 = 78.0 USD
    # Expected Avg Exposure: (82.0 + 78.0) / 2 = 80.0 USD
    # Expected Max Exposure Observed: 82.0 USD
    metrics_2 = pm.update_inventory_metrics(enriched_markets_2)
    assert metrics_2["total_exposure"] == 78.0
    assert metrics_2["max_exposure_observed"] == 82.0
    assert metrics_2["avg_exposure"] == 80.0
    
    # 4. Verify database persistence
    db = Database()
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT total_exposure, max_position_exposure, concentration FROM inventory_history ORDER BY id DESC LIMIT 2")
        rows = cursor.fetchall()
        assert len(rows) == 2
        # Last entry in database should match metrics_2
        assert abs(rows[0][0] - 78.0) < 1e-4
        assert abs(rows[0][1] - 50.0) < 1e-4
        # Second to last entry should match metrics
        assert abs(rows[1][0] - 82.0) < 1e-4
        assert abs(rows[1][1] - 52.0) < 1e-4

    print("✅ Inventory metrics successfully verified and persisted!")
