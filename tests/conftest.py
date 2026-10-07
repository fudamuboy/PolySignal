import sys, os, tempfile
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Isolate tests from the bot's real data: must be set before any app module is imported.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="polysignal-tests-")
os.environ["DB_PATH"] = os.path.join(_TEST_DB_DIR, "test_database.db")
os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["DB_PATH"]  # never touch the PostgreSQL instance

import pytest

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
def book():
    """Factory for paper-trading market data with a real-looking order book."""
    def make(bid=0.49, ask=0.51, bid_levels=None, ask_levels=None, **extra):
        bid_levels = bid_levels or [(bid, 1000.0)]
        ask_levels = ask_levels or [(ask, 1000.0)]
        md = {
            "best_bid": bid_levels[0][0],
            "best_ask": ask_levels[0][0],
            "last_price": (bid_levels[0][0] + ask_levels[0][0]) / 2,
            "spread": 0.04,
            "orderbook": {
                "bids": [{"price": str(p), "size": str(s)} for p, s in bid_levels],
                "asks": [{"price": str(p), "size": str(s)} for p, s in ask_levels],
            },
            "orderPriceMinTickSize": 0.01,
            "orderMinSize": 5,
        }
        md.update(extra)
        return md
    return make
