import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
LOGS_DIR = STORAGE_DIR / "logs"

# API Settings
PK = os.getenv("PK")
CLOB_API_KEY = os.getenv("CLOB_API_KEY")
CLOB_API_SECRET = os.getenv("CLOB_API_SECRET")
CLOB_API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE")
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = int(os.getenv("CHAIN_ID", 137)) # Default to Polygon Mainnet

# Bot Settings
PAPER_TRADING = os.getenv("PAPER_TRADING", "True").lower() == "true"
TEST_MODE = os.getenv("TEST_MODE", "False").lower() == "true"
MIN_DEPTH_USD      = float(os.getenv("MIN_DEPTH_USD", 300.0))   # Production depth floor
MIN_DEPTH_USD_TEST = 200.0   # Test/paper mode only — allows $200–$299 with warning log
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", 10))

# Filtering Settings
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", 100))
MAX_SPREAD = 0.20 # 20% max spread — safety filter before any BUY
MIN_ENTRY_PRICE = float(os.getenv("MIN_ENTRY_PRICE", 0.20))
MAX_ENTRY_PRICE = float(os.getenv("MAX_ENTRY_PRICE", 0.80))
MARKET_COOLDOWN = int(os.getenv("MARKET_COOLDOWN", 600)) # 10 minutes

# Global Market Quality Filters
GLOBAL_MIN_VOLUME = 50000
GLOBAL_MAX_SPREAD = 0.02
GLOBAL_MIN_PRICE = 0.10
GLOBAL_MAX_PRICE = 0.90
GLOBAL_MIN_LIQUIDITY = 5000
GLOBAL_MIN_SCORE = 70

# Risk Settings
MAX_CAPITAL_PER_TRADE = float(os.getenv("MAX_CAPITAL_PER_TRADE", 1.0))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", 5.0))
MAX_DAILY_DRAWDOWN = float(os.getenv("MAX_DAILY_DRAWDOWN", 10.0)) # Absolute amount
MAX_STOP_LOSS_PER_TRADE = float(os.getenv("MAX_STOP_LOSS_PER_TRADE", 0.5)) # Absolute amount
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 8))  # Reduced: quality over quantity
CONSECUTIVE_LOSS_LIMIT = int(os.getenv("CONSECUTIVE_LOSS_LIMIT", 3))
COOLDOWN_DURATION = int(os.getenv("COOLDOWN_DURATION", 3600)) # 1 hour
BASE_ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", 0.5))
AGGRESSIVE_SLIPPAGE = 0.03 # 3% slippage allowed for aggressive orders (Exits)
MOMENTUM_THRESHOLD = 0.025 # 2.5 cent default move required
MOMENTUM_MIN_LIQUIDITY = 500 # $500 combined depth
DYNAMIC_SL_SPREAD_MULT = 0.8 # SL = spread * 0.8 (spread-aware but capped)
DYNAMIC_SL_FLOOR = 0.04 # 4% minimum SL
MAX_SL_ABSOLUTE = 0.05  # 5% hard cap — never risk more than 5% adverse move

# WebSocket stability
WS_STALE_TIMEOUT = 30          # seconds before forcing reconnect (was 60)
DATA_AGE_BLOCK_SECONDS = 20    # block trading if WS data is older than this

# Hybrid data fallback (REST when WS is stale)
REST_FRESHNESS_SECONDS = 10    # REST data must be fetched within 10s to be trusted
PRE_TRADE_REVALIDATE = True    # always re-fetch price via REST before executing
MAX_SIGNAL_DELTA = 0.15        # reject signal if abs(delta) > 15% (data spike/anomaly)
MAX_PRICE_DRIFT = 0.05         # cancel trade if REST price differs > 5% from signal price

# Quality & Profitability Settings
ESTIMATED_FEE_BPS = int(os.getenv("ESTIMATED_FEE_BPS", 20)) # 0.20%
MIN_MOVE_THRESHOLD = 0.015 # 1.5 cent minimum price edge for V2 Profit Mode
SOLO_MOMENTUM_MIN_SCORE = 0.75 # Minimum score for solo momentum trade
SOLO_MOMENTUM_SIZE_MULTIPLIER = 0.5 # Buy 50% size for solo momentum

# Profitability Targets — Positive RR Mode
# TP must be significantly larger than SL (5% cap) to achieve RR >= 1.2
TP_HIGH_CONFIDENCE = 0.07   # 7% — raised from 3% to fix inverted RR
TP_STRONG_TREND = 0.10      # 10% — raised from 7% to allow bigger winners
MIN_RR_RATIO = 0.45         # Base RR gate (recalibrated for higher opportunity capture)
MIN_DELTA_FOR_TRADE = 0.020 # abs(delta) must be >= 2.0% to trade (calibration: relaxed from 0.025)
RR_ADAPTIVE_SCORE_THRESHOLD = 0.85  # signals above this score relax RR to 0.45
RR_ADAPTIVE_MIN = 0.45      # relaxed minimum RR for high-conviction signals

# --- ANTI-ZOMBIE PROTECTION SETTINGS ---
ZOMBIE_PRICE_CAP = 0.80       # Reject BUY if price > 0.80 (calibration: relaxed from 0.65)
ZOMBIE_BID_FLOOR = 0.05       # Reject if best_bid < 0.05
ZOMBIE_MAX_SPREAD = 0.20      # Hard reject if spread > 20%
ZOMBIE_MIN_DEPTH_TICKS = 3    # Loops to track for liquidity collapse
ZOMBIE_RESOLUTION_HOURS = 24  # Avoid markets resolving within 24h (if data available)


# DB Settings
DB_PATH = STORAGE_DIR / "database.db"

# Balanced Activity Settings
INACTIVITY_THRESHOLD = 20    # loops before lowering confidence

# Near-miss logging
NEAR_MISS_LOG_LIMIT = 10    # max near-miss logs per hour

# Sell-skip cache
SELL_SKIP_CACHE_TTL = 300   # seconds to suppress SELL signals for tokens with no position

# Token drift blacklist
BLACKLIST_DRIFT_WINDOW   = 600   # seconds — count drift failures within this window (10 min)
BLACKLIST_DRIFT_THRESHOLD = 3    # number of drift cancels before blacklisting
BLACKLIST_DURATION       = 1800  # seconds — blacklist duration (30 min)
LOW_CONFIDENCE_THRESHOLD = 0.35 # lower bound for confidence
MAX_CANDIDATES = 50           # markets to enrich

# Advanced Strategy Params
MOMENTUM_CONSISTENCY_RATIO = 0.8
MOMENTUM_MAGNITUDE_STRONG = 0.03
MOMENTUM_MAGNITUDE_MASSIVE = 0.05
EV_SAFETY_MARGIN = 0.001

# Mean Reversion Strategy
REVERSION_DELTA_THRESHOLD = 0.06  # full-quality threshold (production)
REVERSION_MIN_EDGE        = 0.015
MIN_REVERSION_FLOOR       = 0.020 # hard minimum delta — applies in ALL modes (no TEST_MODE bypass)
                                   # prevents flat-market noise flooding (was 0.01 in TEST_MODE)

# --- PMM INVENTORY SKEW SETTINGS (Week 2B) ---
GAMMA_BID = 0.08          # Asymmetric entry/buy skew multiplier (heavy protection)
GAMMA_ASK = 0.02          # Asymmetric exit/sell skew multiplier (light exit discount)
PMM_TOTAL_CAPITAL = 10.0  # Default capital base for computing inventory mismatch ratio
TOTAL_CAPITAL_POOL = 500.0  # Global virtual capital pool size for dynamic allocation

# --- PRODUCTION INFRASTRUCTURE SETTINGS ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/polysignal")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- OFFICIAL TWITTER/X API v2 CREDENTIALS ---
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
TWITTER_CONSUMER_KEY = os.getenv("TWITTER_CONSUMER_KEY")
TWITTER_CONSUMER_SECRET = os.getenv("TWITTER_CONSUMER_SECRET")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")

# --- PRODUCTION safety & WATCHDOG LIMITS ---
DAILY_MAX_LOSS_LIMIT = float(os.getenv("DAILY_MAX_LOSS_LIMIT", 50.0))
MAX_EXPOSURE_CAP = float(os.getenv("MAX_EXPOSURE_CAP", 200.0))
STALE_ORDER_TIMEOUT_SECONDS = int(os.getenv("STALE_ORDER_TIMEOUT_SECONDS", 60))



