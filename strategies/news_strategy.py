import asyncio
import httpx
import xml.etree.ElementTree as ET
import time
import json
import urllib.parse
from app.signal_engine import BaseStrategy
from app.logger import logger

class NewsStrategy(BaseStrategy):
    def __init__(self, rss_urls=None, poll_interval=15, data_fetcher=None):
        super().__init__("NewsStrategy")
        # CNBC & BBC News RSS Feeds
        self.rss_urls = rss_urls or [
            "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # CNBC Breaking News
            "http://feeds.bbci.co.uk/news/rss.xml"                   # BBC News
        ]
        self.poll_interval = poll_interval
        self.data_fetcher = data_fetcher
        self.news_queue = []       # Queued news articles to process on next evaluate
        self.seen_guids = set()    # De-duplication set
        self.injected_news = []    # Mock news injected by validation runner
        self.active = True
        
        # Observability stats dictionary
        self.stats = {
            "articles_received": 0,
            "twitter_received": 0,
            "rss_received": 0,
            "mock_received": 0,
            "events_matched": 0,
            "signals_generated": 0,
            "signals_rejected_score": 0,
            "twitter_available": False,
            "rss_available": True
        }
        
        # Twitter/X RSS Integration via Nitter pool (Primary Alpha Engine)
        self.twitter_handles = ["DeitaOne", "Unusual_Whales", "spectatorindex", "BleacherReport"]
        self.nitter_instances = [
            "https://nitter.cz",
            "https://nitter.privacydev.net",
            "https://nitter.moomoo.me",
            "https://nitter.net"
        ]
        
        # Start background polling tasks
        self.polling_task = asyncio.create_task(self._poll_loop())
        self.twitter_polling_task = asyncio.create_task(self._poll_twitter_loop())
        logger.info("NewsStrategy initialized with RSS and real-time Twitter/X sentiment polling.")

    def get_stats(self):
        """Return a snapshot of News Engine observability metrics."""
        return dict(self.stats)

    async def _poll_loop(self):
        """Asynchronously poll RSS feeds in the background."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        while self.active:
            try:
                async with httpx.AsyncClient(timeout=4.0, verify=False, headers=headers, follow_redirects=True) as client:
                    for url in self.rss_urls:
                        try:
                            response = await client.get(url)
                            if response.status_code != 200:
                                continue
                            
                            root = ET.fromstring(response.content)
                            new_count = 0
                            for item in root.findall(".//item"):
                                title = item.find("title")
                                title_text = title.text if title is not None else ""
                                
                                guid = item.find("guid")
                                guid_text = guid.text if guid is not None else title_text
                                
                                if guid_text in self.seen_guids:
                                    continue
                                self.seen_guids.add(guid_text)
                                
                                description = item.find("description")
                                desc_text = description.text if description is not None else ""
                                
                                self.news_queue.append({
                                    "title": title_text,
                                    "description": desc_text,
                                    "timestamp": time.time(),
                                    "source": "RSS"
                                })
                                new_count += 1
                            if new_count > 0:
                                self.stats["rss_received"] += new_count
                                self.stats["articles_received"] += new_count
                                logger.info(f"NewsStrategy: Ingested {new_count} new articles from {url}")
                        except Exception as e:
                            logger.error(f"NewsStrategy error fetching feed {url}: {e}")
            except Exception as e:
                logger.error(f"NewsStrategy error in global poll loop: {e}")
            
            await asyncio.sleep(self.poll_interval)

    async def _poll_twitter_loop(self):
        """Poll Twitter/X feeds using official TwitterClient with fallback to Nitter RSS mirrors."""
        from app.twitter_client import TwitterClient
        twitter_client = TwitterClient()
        self.stats["twitter_available"] = twitter_client.is_configured
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        nitter_idx = 0
        
        while self.active:
            try:
                # 1. Try official Twitter API v2 timeline polling first
                if twitter_client.is_configured:
                    new_tweets = await twitter_client.fetch_new_tweets()
                    for tweet in new_tweets:
                        guid_text = tweet["title"]
                        if guid_text in self.seen_guids:
                            continue
                        self.seen_guids.add(guid_text)
                        self.news_queue.append(tweet)
                    
                    if new_tweets:
                        self.stats["twitter_received"] += len(new_tweets)
                        self.stats["articles_received"] += len(new_tweets)
                        logger.info(f"NewsStrategy: Ingested {len(new_tweets)} tweets via official Twitter API v2.")
                else:
                    # 2. Fallback to Nitter RSS mirror rotation
                    async with httpx.AsyncClient(timeout=4.0, verify=False, headers=headers, follow_redirects=True) as client:
                        for handle in self.twitter_handles:
                            if not self.active:
                                break
                            success = False
                            # Try up to length of instances to find a working mirror
                            for _ in range(len(self.nitter_instances)):
                                instance = self.nitter_instances[nitter_idx]
                                url = f"{instance}/{handle}/rss"
                                try:
                                    response = await client.get(url)
                                    if response.status_code == 200:
                                        root = ET.fromstring(response.content)
                                        new_count = 0
                                        for item in root.findall(".//item"):
                                            title = item.find("title")
                                            title_text = title.text if title is not None else ""
                                            
                                            guid = item.find("guid")
                                            guid_text = guid.text if guid is not None else title_text
                                            
                                            if guid_text in self.seen_guids:
                                                continue
                                            self.seen_guids.add(guid_text)
                                            
                                            description = item.find("description")
                                            desc_text = description.text if description is not None else ""
                                            
                                            self.news_queue.append({
                                                "title": f"TWEET (@{handle}): {title_text}",
                                                "description": desc_text,
                                                "timestamp": time.time(),
                                                "source": "Twitter"
                                            })
                                            new_count += 1
                                        if new_count > 0:
                                            logger.info(f"NewsStrategy: Ingested {new_count} new tweets from @{handle} using Nitter fallback {instance}")
                                        success = True
                                        break
                                    else:
                                        # Cycle to next mirror on non-200
                                        nitter_idx = (nitter_idx + 1) % len(self.nitter_instances)
                                except Exception:
                                    # Cycle to next mirror on network errors
                                    nitter_idx = (nitter_idx + 1) % len(self.nitter_instances)
                                    await asyncio.sleep(0.5)
                            
                            if not success:
                                logger.debug(f"NewsStrategy: Failed to fetch tweets for @{handle} from all Nitter instances fallback.")
            except Exception as e:
                logger.error(f"NewsStrategy error in Twitter loop: {e}")
            
            await asyncio.sleep(self.poll_interval * 2)

    def inject_mock_news(self, headline, description=""):
        """Utility for test suite to inject mock news articles."""
        item = {
            "title": headline,
            "description": description,
            "timestamp": time.time(),
            "source": "MOCK_INJECTION"
        }
        self.injected_news.append(item)
        logger.info(f"NewsStrategy: Injected mock news item: '{headline}'")

    def analyze_sentiment(self, text):
        """Analyze sentiment of headline to determine POSITIVE, NEGATIVE, or NEUTRAL."""
        text_upper = text.upper()
        # Words indicating confirmation, wins, rate cuts, success, bullish
        pos_words = [
            "SUCCESS", "SUCCESSFUL", "WIN", "WINS", "WON", "CHAMPION", "CHAMPIONS",
            "APPROVED", "PASSES", "RISE", "SURGE", "SURGES", "ATH", "HIGHER", "CUT",
            "CUTS", "CONFIRMED", "SOFT LANDING", "TOUCHES", "REACHES", "HIT", "HITS",
            "ACQUIRE", "ACQUIRES"
        ]
        # Words indicating failure, crash, rejection, withdrawals, stay in/no change
        neg_words = [
            "FAIL", "FAILED", "FAILURE", "CRASH", "CRASHES", "LOSE", "LOST", "DEFEAT",
            "DEFEATED", "REJECT", "REJECTED", "BLOCK", "BLOCKS", "DROP", "DROPS",
            "WITHDRAW", "WITHDRAWS", "CANCEL", "CANCELLED", "DROPS OUT", "ENDS",
            "NO PRISON", "AVOIDS"
        ]
        
        pos_score = sum(1 for w in pos_words if w in text_upper)
        neg_score = sum(1 for w in neg_words if w in text_upper)
        
        if pos_score > neg_score:
            return "POSITIVE", pos_score - neg_score
        elif neg_score > pos_score:
            return "NEGATIVE", neg_score - pos_score
        else:
            return "NEUTRAL", 0

    def calculate_alpha_score(self, headline, rule, matched_kws, source):
        """Calculate alpha score (0-100) based on keyword match, sentiment, and reliability."""
        # 1. Base Score
        score = 65.0
        
        # 2. Keyword Match Ratio Bonus (up to +15 points)
        total_kws = len(rule.get("news_keywords", []))
        match_ratio = len(matched_kws) / total_kws if total_kws > 0 else 0
        score += 15.0 * match_ratio
        
        # 3. Urgency / Breaking News Indicator Bonus (up to +10 points)
        headline_upper = headline.upper()
        urgency_indicators = ["BREAKING", "JUST IN", "URGENT", "ALERT", "OFFICIAL", "TWEET"]
        if any(ind in headline_upper for ind in urgency_indicators):
            score += 10.0
            
        # 4. Source Reliability Bonus (up to +10 points)
        if source == "Twitter":
            score += 10.0
        elif source == "MOCK_INJECTION":
            score += 10.0
        else:
            score += 5.0
            
        # Cap final score between 0 and 100
        return min(100.0, max(0.0, score))

    async def evaluate(self, market_data):
        """
        Evaluate candidate Polymarket tokens against incoming news events with scoring and sentiment.
        """
        signals = []
        
        # Drain news queues
        articles = list(self.news_queue)
        self.news_queue.clear()
        
        articles.extend(self.injected_news)
        self.injected_news.clear()
        
        if not articles:
            return signals

        # Default rules covering all 4 priority areas
        rules = getattr(self, "rules", None)
        if not rules:
            rules = [
                # 1. Macroeconomic data releases
                {
                    "category": "FED_50BPS",
                    "priority": "Macro",
                    "news_keywords": ["FED", "RATE", "CUT", "50"],
                    "market_keywords": ["FED", "RATE", "50BPS", "50 BASIS"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },
                {
                    "category": "FED_25BPS",
                    "priority": "Macro",
                    "news_keywords": ["FED", "RATE", "CUT", "25"],
                    "market_keywords": ["FED", "RATE", "25BPS", "25 BASIS"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },
                {
                    "category": "CPI_INFLATION",
                    "priority": "Macro",
                    "news_keywords": ["CPI", "INFLATION", "PERCENT", "%"],
                    "market_keywords": ["CPI", "INFLATION", "PERCENT", "RATE"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },
                
                # 2. Crypto price movements
                {
                    "category": "BITCOIN_1M",
                    "priority": "Crypto",
                    "news_keywords": ["BITCOIN", "1 MILLION", "BTC TO 1M", "BTC HITS 1 MILLION"],
                    "market_keywords": ["BITCOIN", "1M", "1 MILLION"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },
                {
                    "category": "CRYPTO_PRICE_MOVE",
                    "priority": "Crypto",
                    "news_keywords": ["BITCOIN", "SURGES", "BTC HITS", "BTC ATH"],
                    "market_keywords": ["BITCOIN", "PRICE", "ATH", "HIGHER"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },
                {
                    "category": "MEGAETH_AIRDROP",
                    "priority": "Crypto",
                    "news_keywords": ["MEGAETH", "AIRDROP", "ANNOUNCED", "CONFIRMED"],
                    "market_keywords": ["MEGAETH", "AIRDROP"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },
                {
                    "category": "STARSHIP_FLIGHT",
                    "priority": "Space",
                    "news_keywords": ["STARSHIP", "SPLASHDOWN", "FLIGHT", "SUCCESSFUL"],
                    "market_keywords": ["STARSHIP", "SPLASHDOWN", "FLIGHT"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },

                # 3. Political announcements
                {
                    "category": "BIDEN_WITHDRAWAL",
                    "priority": "Politics",
                    "news_keywords": ["BIDEN", "WITHDRAWS", "DROPS OUT", "ENDS presidential", "END CAMPAIGN"],
                    "market_keywords": ["BIDEN", "NOMINEE", "presidential"],
                    "sentiment_map": {"POSITIVE": "No", "NEGATIVE": "No"},
                    "default_outcome": "No",
                    "side": "BUY"
                },
                {
                    "category": "CHINA_TAIWAN",
                    "priority": "Politics",
                    "news_keywords": ["CHINA", "TAIWAN", "INVASION", "INVADES", "ATTACK"],
                    "market_keywords": ["CHINA", "TAIWAN", "MILITARY"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },

                # 4. Sports results (live scores)
                {
                    "category": "SPAIN_WORLD_CUP",
                    "priority": "Sports",
                    "news_keywords": ["SPAIN", "WORLD CUP", "CHAMPIONS", "WINS"],
                    "market_keywords": ["SPAIN", "WORLD CUP", "FIFA"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },
                {
                    "category": "KNICKS_NBA",
                    "priority": "Sports",
                    "news_keywords": ["KNICKS", "NBA", "FINALS", "CHAMPIONS", "WINS"],
                    "market_keywords": ["KNICKS", "NBA", "FINALS"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                },
                {
                    "category": "HURRICANES_NHL",
                    "priority": "Sports",
                    "news_keywords": ["HURRICANES", "NHL", "STANLEY CUP", "CHAMPIONS", "WINS"],
                    "market_keywords": ["HURRICANES", "NHL", "STANLEY CUP"],
                    "sentiment_map": {"POSITIVE": "Yes", "NEGATIVE": "No"},
                    "default_outcome": "Yes",
                    "side": "BUY"
                }
            ]

        for article in articles:
            title = article["title"].upper()
            logger.info(f"NewsStrategy: Processing headline: '{article['title']}'")

            for rule in rules:
                # 1. Keyword detection in headline
                matched_kws = [kw for kw in rule["news_keywords"] if kw in title]
                if not matched_kws:
                    continue

                # Sentiment evaluation
                sentiment_direction, intensity = self.analyze_sentiment(title)
                
                # Determine target outcome based on sentiment direction
                target_outcome = None
                if "sentiment_map" in rule:
                    target_outcome = rule["sentiment_map"].get(sentiment_direction)
                # Backwards compatibility fallbacks
                if not target_outcome:
                    target_outcome = rule.get("target_outcome") or rule.get("default_outcome") or "Yes"

                # Calculate alpha score
                alpha_score = self.calculate_alpha_score(article["title"], rule, matched_kws, article.get("source"))
                
                # Trade score gating (Only trade scores above 70)
                if alpha_score < 70.0:
                    self.stats["signals_rejected_score"] += 1
                    logger.info(f"NewsStrategy: Rejected event '{article['title']}' | Alpha Score {alpha_score:.1f} < 70")
                    continue

                self.stats["events_matched"] += 1

                # 2. Local Market Mapping (First check passed market_data for matches)
                best_market = None
                best_token = None
                confidence = 0.0
                
                local_scored_candidates = []
                for market in market_data:
                    question = str(market.get("question", "")).upper()
                    kw_matches = [kw for kw in rule["market_keywords"] if kw in question]
                    if not kw_matches:
                        continue
                        
                    tokens = market.get("tokens", [])
                    target_token = None
                    for token in tokens:
                        token_outcome = token.get("outcome") or market.get("outcome")
                        if token_outcome and token_outcome.upper() == target_outcome.upper():
                            target_token = token
                            break
                    if not target_token:
                        continue
                        
                    match_ratio = len(kw_matches) / len(rule["market_keywords"])
                    score_m = match_ratio * 50
                    vol = float(market.get("volume_24h") or market.get("volume") or 0.0)
                    liq = float(market.get("liquidity") or 0.0)
                    score_m += min(25.0, vol / 10000.0)
                    score_m += min(25.0, liq / 5000.0)
                    
                    local_scored_candidates.append({
                        "market": market,
                        "token": target_token,
                        "score": score_m,
                        "confidence": match_ratio,
                        "kw_matches": kw_matches
                    })
                    
                if local_scored_candidates:
                    local_scored_candidates.sort(key=lambda x: x["score"], reverse=True)
                    best_match = local_scored_candidates[0]
                    best_market = best_match["market"]
                    best_token = best_match["token"]
                    confidence = best_match["confidence"]
                    logger.info(f"NewsStrategy: Mapped local market match: '{best_market.get('question')}' with outcome: {target_outcome}")
                
                # If no local match, query the public search API dynamically
                if not best_market:
                    search_query = matched_kws[0]
                    url = f"https://gamma-api.polymarket.com/public-search?q={urllib.parse.quote(search_query)}"
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                    }
                    
                    candidate_markets = []
                    try:
                        async with httpx.AsyncClient(timeout=1.5, verify=False, headers=headers, follow_redirects=True) as client:
                            response = await client.get(url)
                            if response.status_code == 200:
                                search_results = response.json()
                                if isinstance(search_results, dict) and "events" in search_results:
                                    events = search_results["events"] or []
                                    for event in events:
                                        event_markets = event.get("markets", []) or []
                                        for m in event_markets:
                                            if m.get("active") and not m.get("closed") and m.get("acceptingOrders"):
                                                candidate_markets.append(m)
                    except Exception as ex:
                        logger.error(f"NewsStrategy: Search query error for keyword '{search_query}': {ex}")
                        continue
                        
                    if not candidate_markets:
                        logger.info(
                            f"NEWS_MAPPING_AUDIT | Headline: '{article['title']}' | "
                            f"Keyword Extracted: '{search_query}' | Markets Returned: [] | "
                            f"Selected Market: None | Confidence: 0.00"
                        )
                        continue
                        
                    # Normalize retrieved markets from Gamma format
                    normalized_markets = []
                    for m in candidate_markets:
                        if not isinstance(m, dict): continue
                        nm = m.copy()
                        if "tokens" not in nm and "clobTokenIds" in nm:
                            clob_ids = nm.get("clobTokenIds") or []
                            if isinstance(clob_ids, str):
                                try: clob_ids = json.loads(clob_ids)
                                except: clob_ids = []
                            outcomes = nm.get("outcomes") or []
                            if isinstance(outcomes, str):
                                try: outcomes = json.loads(outcomes)
                                except: outcomes = []
                            prices = nm.get("outcomePrices") or []
                            if isinstance(prices, str):
                                try: prices = json.loads(prices)
                                except: prices = []
                            tokens = []
                            for i, tid in enumerate(clob_ids):
                                outcome = outcomes[i] if i < len(outcomes) else ("Yes" if i == 0 else "No")
                                price = prices[i] if i < len(prices) else "0.5"
                                tokens.append({"token_id": str(tid), "outcome": outcome, "price": price})
                            nm["tokens"] = tokens
                        nm["volume_24h"] = float(nm.get("volume24hr") or nm.get("volumeNum") or nm.get("volume_24h") or 0)
                        nm["liquidity"] = float(nm.get("liquidityNum") or nm.get("liquidity") or nm.get("liquidityClob") or 0)
                        nm["spread"] = float(nm.get("spread") or 1.0)
                        nm["end_date_iso"] = nm.get("endDateIso") or nm.get("endDate")
                        nm["active"] = nm.get("active", True)
                        nm["closed"] = nm.get("closed", False)
                        normalized_markets.append(nm)
                        
                    # Rank candidate markets
                    scored_candidates = []
                    for market in normalized_markets:
                        question = str(market.get("question", "")).upper()
                        kw_matches = [kw for kw in rule["market_keywords"] if kw in question]
                        if not kw_matches: continue
                        
                        tokens = market.get("tokens", [])
                        target_token = None
                        for token in tokens:
                            if token.get("outcome", "").upper() == target_outcome.upper():
                                target_token = token
                                break
                        if not target_token: continue
                        
                        match_ratio = len(kw_matches) / len(rule["market_keywords"])
                        score_m = match_ratio * 50
                        vol = market.get("volume_24h", 0.0)
                        liq = market.get("liquidity", 0.0)
                        score_m += min(25.0, vol / 10000.0)
                        score_m += min(25.0, liq / 5000.0)
                        
                        scored_candidates.append({
                            "market": market,
                            "token": target_token,
                            "score": score_m,
                            "confidence": match_ratio,
                            "kw_matches": kw_matches
                        })
                        
                    if not scored_candidates:
                        logger.info(
                            f"NEWS_MAPPING_AUDIT | Headline: '{article['title']}' | "
                            f"Keyword Extracted: '{search_query}' | "
                            f"Markets Returned: {[m.get('slug') for m in normalized_markets[:5]]} | "
                            f"Selected Market: None | Confidence: 0.00"
                        )
                        continue
                        
                    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
                    best_match = scored_candidates[0]
                    best_market = best_match["market"]
                    best_token = best_match["token"]
                    confidence = best_match["confidence"]
                    
                    logger.info(
                        f"NEWS_MAPPING_AUDIT | Headline: '{article['title']}' | "
                        f"Keyword Extracted: '{search_query}' | "
                        f"Markets Returned: {[m.get('slug') for m in normalized_markets[:5]]} | "
                        f"Selected Market: '{best_market.get('slug')}' | "
                        f"Confidence: {confidence:.2f}"
                    )
                
                # Fetch order book and enrich the market data
                t_id = best_token.get("token_id")
                if not t_id:
                    continue
                    
                best_bid, best_ask, mid_price, spread = 0.50, 0.50, 0.50, 0.02
                
                if self.data_fetcher:
                    orderbook = await self.data_fetcher.get_orderbook(t_id)
                    if not orderbook:
                        logger.warning(f"NewsStrategy: Failed to fetch orderbook for selected token {t_id[:20]}")
                        continue
                        
                    try:
                        raw_bids = orderbook.get("bids", []) if isinstance(orderbook, dict) else getattr(orderbook, "bids", [])
                        raw_asks = orderbook.get("asks", []) if isinstance(orderbook, dict) else getattr(orderbook, "asks", [])
                        
                        if not raw_bids or not raw_asks:
                            continue
                        
                        bids = sorted(raw_bids, key=lambda x: float(x.price if hasattr(x, "price") else x.get("price", 0)), reverse=True)
                        asks = sorted(raw_asks, key=lambda x: float(x.price if hasattr(x, "price") else x.get("price", 0)))
                        
                        def get_price(level):
                            if hasattr(level, "price"): return float(level.price)
                            if isinstance(level, dict): return float(level.get("price") or level.get("p") or 0)
                            return 0.0
                            
                        best_bid, best_ask = get_price(bids[0]), get_price(asks[0])
                        mid_price = (best_bid + best_ask) / 2
                        spread = (best_ask - best_bid) / mid_price if mid_price > 0 else 1.0
                    except Exception as enrichment_err:
                        logger.error(f"NewsStrategy: Error enriching best market token {t_id[:20]}: {enrichment_err}")
                        continue
                else:
                    # Fallback when data_fetcher is not passed (e.g. mock markets test)
                    best_bid = float(best_market.get("best_bid") or best_market.get("last_price") or 0.50)
                    best_ask = float(best_market.get("best_ask") or best_market.get("last_price") or 0.50)
                    mid_price = (best_bid + best_ask) / 2
                    spread = float(best_market.get("spread") or 0.02)
                    
                # Calculate expected directional price movement (delta) from alpha score, sentiment, and conviction
                # In prediction markets, breaking news typically moves fair value by 3% to 10%
                sentiment_mag = float(intensity) if 'intensity' in locals() else 1.0
                base_delta = 0.03 + ((alpha_score - 70.0) / 30.0) * 0.05 + min(0.02, abs(sentiment_mag) * 0.005)
                expected_delta = min(0.12, max(0.025, base_delta))
                
                self.stats["signals_generated"] += 1
                # Generate signal with dynamic alpha_score and realistic directional delta
                signals.append({
                    "token_id": t_id,
                    "price": best_ask if rule["side"] == "BUY" else best_bid,
                    "size": 10.0,
                    "side": rule["side"],
                    "score": alpha_score,
                    "strategy": self.name,
                    "delta": round(expected_delta, 4),
                    "spread": spread,
                    "reason": f"News: {article['title']} (Score: {alpha_score:.1f}, Sentiment: {sentiment_direction})"
                })

        return signals

    def stop(self):
        self.active = False
        if self.polling_task:
            self.polling_task.cancel()
        if self.twitter_polling_task:
            self.twitter_polling_task.cancel()
        logger.info("NewsStrategy background polling tasks stopped.")
