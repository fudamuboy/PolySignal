import asyncio
import httpx
import time
from .config import (
    TWITTER_BEARER_TOKEN, TWITTER_CONSUMER_KEY, TWITTER_CONSUMER_SECRET,
    TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET
)
from .logger import logger

class TwitterClient:
    def __init__(self):
        self.bearer_token = TWITTER_BEARER_TOKEN
        self.api_url = "https://api.twitter.com/2"
        # Map target accounts to their Twitter User IDs to save API calls
        self.target_accounts = {
            "DeitaOne": "1068832560",
            "Unusual_Whales": "1251341071279181824",
            "spectatorindex": "1626294277",
            "BleacherReport": "890891"
        }
        # Track since_id per account to only fetch new tweets
        self.since_ids = {}
        self.is_configured = bool(self.bearer_token)
        # Log YES/NO only — the token value is NEVER printed or exposed
        logger.info(f"Twitter API configured: {'YES' if self.is_configured else 'NO'}")
        if not self.is_configured:
            logger.warning("TwitterClient: TWITTER_BEARER_TOKEN not set. Official Twitter v2 API features will be unavailable.")

    async def fetch_new_tweets(self) -> list:
        """Poll user timelines of priority accounts and return formatted tweet dicts."""
        if not self.is_configured:
            return []

        tweets_collected = []
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "User-Agent": "PolymarketBotTwitterClient/1.0"
        }

        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            for handle, user_id in self.target_accounts.items():
                params = {
                    "max_results": 5,
                    "tweet.fields": "created_at,id,text"
                }
                if handle in self.since_ids:
                    params["since_id"] = self.since_ids[handle]

                url = f"{self.api_url}/users/{user_id}/tweets"
                try:
                    response = await client.get(url, params=params)
                    if response.status_code == 429:
                        # Rate limit hit: sleep and log warning
                        reset_time = int(response.headers.get("x-rate-limit-reset", time.time() + 60))
                        sleep_duration = max(10, reset_time - int(time.time()))
                        logger.warning(f"TwitterClient: Rate limited (429) for @{handle}. Backing off for {sleep_duration}s.")
                        await asyncio.sleep(5) # short sleep inside loop
                        continue
                    
                    if response.status_code != 200:
                        logger.debug(f"TwitterClient: Non-200 response ({response.status_code}) fetching @{handle}: {response.text}")
                        continue

                    data = response.json()
                    tweets_list = data.get("data", [])
                    meta = data.get("meta", {})
                    
                    if tweets_list:
                        # Update since_id to prevent duplicates on next poll
                        newest_id = meta.get("newest_id")
                        if newest_id:
                            self.since_ids[handle] = newest_id
                        
                        for tweet in tweets_list:
                            text = tweet.get("text", "")
                            # Map Twitter created_at string to timestamp
                            try:
                                created_at_str = tweet.get("created_at")
                                if created_at_str:
                                    import datetime
                                    dt = datetime.datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                                    tweet_ts = dt.timestamp()
                                else:
                                    tweet_ts = time.time()
                            except Exception:
                                tweet_ts = time.time()

                            tweets_collected.append({
                                "title": f"TWEET (@{handle}): {text}",
                                "description": text,
                                "timestamp": tweet_ts,
                                "source": "Twitter"
                            })
                        
                        logger.info(f"TwitterClient: Ingested {len(tweets_list)} new tweets from @{handle}")

                except Exception as e:
                    logger.error(f"TwitterClient: Error polling user timeline for @{handle}: {e}")
                    await asyncio.sleep(1)

        return tweets_collected
