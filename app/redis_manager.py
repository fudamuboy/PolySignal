import json
from .config import REDIS_URL, MARKET_COOLDOWN, COOLDOWN_DURATION
from .logger import logger

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    logger.warning("Redis client library not found. Falling back to in-memory MockRedis client.")

class MockRedis:
    def __init__(self):
        self.store = {}
        self.lists = {}

    async def connect(self):
        return self

    async def close(self):
        pass

    async def set(self, key, val):
        self.store[key] = str(val)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return int(key in self.store)

    async def setex(self, key, ttl, val):
        self.store[key] = str(val)
        return True

    async def lpush(self, key, val):
        if key not in self.lists:
            self.lists[key] = []
        self.lists[key].insert(0, str(val))
        return True

    async def blpop(self, key, timeout=1):
        # In mock mode, we do a non-blocking pop for simplicity
        if key in self.lists and self.lists[key]:
            return (key, self.lists[key].pop())
        # Simulate blocking timeout
        import asyncio
        await asyncio.sleep(0.01)
        return None

    def pipeline(self):
        return self

    async def incr(self, key):
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key, seconds):
        return True

    async def execute(self):
        return [True, True]

    def time(self):
        import time
        return time.time()


class RedisManager:
    def __init__(self):
        self.redis_url = REDIS_URL
        self.client = None
        self.is_redis_available = HAS_REDIS

    async def connect(self):
        """Lazy connect to Redis or return MockRedis."""
        if self.client is None:
            if self.is_redis_available:
                try:
                    self.client = aioredis.from_url(self.redis_url, decode_responses=True)
                    # Verify connection works
                    await self.client.ping()
                    logger.info(f"RedisManager: Connected to Redis at {self.redis_url}")
                except Exception as e:
                    logger.warning(f"RedisManager: Failed to connect to Redis server: {e}. Falling back to MockRedis.")
                    self.client = MockRedis()
            else:
                self.client = MockRedis()
        return self.client

    async def close(self):
        if self.client:
            await self.client.close()
            self.client = None

    # --- SIGNAL QUEUE ---
    async def push_signal(self, signal: dict):
        """Push a trading signal onto the queue."""
        client = await self.connect()
        payload = json.dumps(signal)
        await client.lpush("trading_signals", payload)

    async def pop_signal(self, timeout: int = 1) -> dict:
        """Block and pop a trading signal from the queue."""
        client = await self.connect()
        res = await client.blpop("trading_signals", timeout=timeout)
        if res:
            return json.loads(res[1])
        return None

    # --- SHARED STATE & CACHING ---
    async def set_token_price(self, token_id: str, price: float):
        client = await self.connect()
        await client.set(f"price:{token_id}", str(price))

    async def get_token_price(self, token_id: str) -> float:
        client = await self.connect()
        val = await client.get(f"price:{token_id}")
        return float(val) if val else None

    async def set_orderbook(self, token_id: str, orderbook: dict):
        client = await self.connect()
        await client.set(f"orderbook:{token_id}", json.dumps(orderbook))

    async def get_orderbook(self, token_id: str) -> dict:
        client = await self.connect()
        val = await client.get(f"orderbook:{token_id}")
        return json.loads(val) if val else None

    # --- SHARED COOLDOWNS ---
    async def set_market_cooldown(self, token_id: str, strategy: str, ttl: int = MARKET_COOLDOWN):
        """Set a strategy-aware cooldown for a specific market token."""
        client = await self.connect()
        key = f"cooldown:market:{token_id}"
        
        # Resolve time method based on client type
        if hasattr(client, "time") and callable(client.time):
            current_time = client.time()
            if asyncio.iscoroutine(current_time) or hasattr(current_time, "__await__"):
                current_time = await current_time
        else:
            current_time = time.time()

        val = json.dumps({
            "timestamp": current_time,
            "strategy": strategy
        })
        await client.setex(key, ttl, val)

    async def get_market_cooldown(self, token_id: str) -> dict:
        """Retrieve active cooldown metadata for a token."""
        client = await self.connect()
        val = await client.get(f"cooldown:market:{token_id}")
        return json.loads(val) if val else None

    async def set_global_cooldown(self, ttl: int = COOLDOWN_DURATION):
        client = await self.connect()
        await client.setex("cooldown:global", ttl, "1")

    async def is_global_cooldown_blocked(self) -> bool:
        client = await self.connect()
        return await client.exists("cooldown:global") > 0

    # --- RATE LIMITER ---
    async def check_rate_limit(self, key: str, limit: int, window: int) -> bool:
        """
        Generic rate limiter using sliding or fixed window counters in Redis.
        Returns True if allowed (under limit), False otherwise.
        """
        client = await self.connect()
        r_key = f"ratelimit:{key}"
        current = await client.get(r_key)
        if current is not None and int(current) >= limit:
            return False
        
        # Increment and set TTL if new
        pipe = client.pipeline()
        pipe.incr(r_key)
        pipe.expire(r_key, window)
        if hasattr(pipe, "execute") and callable(pipe.execute):
            await pipe.execute()
        return True
