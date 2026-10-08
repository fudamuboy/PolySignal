"""
Pair market maker.

On Polymarket one YES share plus one NO share of the same market always pays out
exactly $1 (they can be merged back into $1 at any time). If both legs are bought
as passive maker orders (no taker fees) for a combined price below $1, the pair is
a locked profit of (1 - yes_price - no_price) per share.

Lifecycle of a market:
  flat      -> quote a bid on BOTH outcomes, PMM_PAIR_SHARES each, only if
               best_bid_yes + best_bid_no <= 1 - PMM_MIN_PAIR_EDGE
  one leg   -> stop buying the held leg; keep bidding the missing leg, capped so
               that the completed pair still costs <= 1 - PMM_MIN_PAIR_EDGE
  both legs -> merge the matched shares (realize 1 - cost, no fees)
  stuck     -> if the single leg is held longer than PMM_LEG_TIMEOUT_S or its bid
               falls PMM_MAX_ADVERSE_MOVE below cost, sell it (taker) and put the
               market on cooldown

Positions and resting orders tagged with PAIR_MM_STRATEGY belong to this class;
the generic SL/TP exit engine and stale-order watchdog leave them alone.

NOTE: merging is simulated in paper mode. Live trading would need an on-chain
CTF mergePositions call (not implemented) — until then live pairs are only held.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.config import (
    PAIR_MM_STRATEGY, PMM_PAIR_SHARES, PMM_MIN_PAIR_EDGE, PMM_MAX_MARKETS,
    PMM_MIN_LEG_PRICE, PMM_MAX_LEG_PRICE, PMM_MIN_HOURS_TO_END, PMM_MAX_HOURLY_MOVE,
    PMM_LEG_TIMEOUT_S, PMM_MAX_ADVERSE_MOVE, PMM_COOLDOWN_S
)
from app.logger import logger
from app.paper_fill_model import get_tick_size, get_min_order_size, round_to_tick, get_book_levels

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"


def _token_ids(market):
    tokens = market.get("tokens")
    if tokens:
        return [str(t.get("token_id")) for t in tokens if isinstance(t, dict) and t.get("token_id")]
    ids = market.get("clobTokenIds") or []
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except ValueError:
            ids = []
    return [str(t) for t in ids]


def _market_key(market):
    ids = _token_ids(market)
    return market.get("conditionId") or market.get("condition_id") or "|".join(sorted(ids))


class TwoSidedMMStrategy:
    name = PAIR_MM_STRATEGY

    def __init__(self, execution_engine, position_manager, risk_manager=None, data_fetcher=None, tracker=None):
        self.ee = execution_engine
        self.pm = position_manager
        self.risk = risk_manager
        self.df = data_fetcher
        self.tracker = tracker

        self.market_tokens = {}    # market key -> [token_a, token_b]
        self.token_market = {}     # token id -> market key
        self.books = {}            # token id -> latest market data (metadata + book)
        self.unmatched_since = {}  # market key -> time one leg started being held alone
        self.cooldown_until = {}   # market key -> no new pair before this time
        self._unknown_tokens = set()

    # ------------------------------------------------------------------ helpers

    def _remember(self, md):
        ids = _token_ids(md)
        if len(ids) != 2:
            return
        key = _market_key(md)
        self.market_tokens[key] = ids
        for tid in ids:
            self.token_market[tid] = key
        tid = md.get("token_id")
        if tid:
            self.books[str(tid)] = md

    def _inventory(self, token_id):
        pos = self.pm.positions.get(token_id)
        if not pos or pos.get("strategy") != PAIR_MM_STRATEGY:
            return 0.0, 0.0
        return float(pos.get("size", 0.0)), float(pos.get("avg_price", 0.0))

    def _quotes(self, token_id):
        return [o for o in self.ee.pending_orders
                if o["token_id"] == token_id and o.get("strategy") == PAIR_MM_STRATEGY]

    def _managed_keys(self):
        keys = set()
        for tid, pos in self.pm.positions.items():
            if pos.get("strategy") == PAIR_MM_STRATEGY:
                keys.add(self.token_market.get(tid, ("?", tid)))
        for o in self.ee.pending_orders:
            if o.get("strategy") == PAIR_MM_STRATEGY:
                keys.add(self.token_market.get(o["token_id"], ("?", o["token_id"])))
        return keys

    async def _lookup_market(self, token_id):
        """Find the pair of an inventory token we have no metadata for (e.g. after a restart)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(GAMMA_MARKETS_URL, params={"clob_token_ids": token_id})
                markets = resp.json() if resp.status_code == 200 else []
        except Exception as e:
            logger.warning(f"PMM: market lookup failed for {token_id[:20]}...: {e}")
            return None
        if not markets:
            return None
        market = dict(markets[0])
        self._remember(market)
        return self.token_market.get(token_id)

    async def _fresh_book(self, token_id):
        """Latest book for a token outside this loop's candidate universe."""
        if self.df is None:
            return self.books.get(token_id)
        fresh = await self.df.get_fresh_orderbook_rest(token_id)
        if not fresh:
            return self.books.get(token_id)
        md = dict(self.books.get(token_id, {}))
        md.update(fresh)
        md["token_id"] = token_id
        self.books[token_id] = md
        return md

    def _cash_available(self):
        if self.tracker is None:
            return float("inf")
        committed = sum(o["price"] * o["size"] for o in self.ee.pending_orders if o["side"] == "BUY")
        return self.tracker.virtual_cash - committed

    def _ineligible_reason(self, key, md_a, md_b, now):
        if now < self.cooldown_until.get(key, 0):
            return "cooldown"
        for md in (md_a, md_b):
            bid, ask = float(md.get("best_bid") or 0), float(md.get("best_ask") or 0)
            if bid <= 0 or ask <= 0:
                return "no two-sided book"
            if not PMM_MIN_LEG_PRICE <= bid <= PMM_MAX_LEG_PRICE:
                return f"leg price {bid:.3f} outside [{PMM_MIN_LEG_PRICE}, {PMM_MAX_LEG_PRICE}]"
            if md.get("acceptingOrders") is False or md.get("closed"):
                return "not accepting orders"
            if PMM_PAIR_SHARES < get_min_order_size(md):
                return "pair size below market minimum"
        move = abs(float(md_a.get("oneHourPriceChange") or 0))
        if move > PMM_MAX_HOURLY_MOVE:
            return f"moving fast ({move:.3f}/h)"
        end = md_a.get("endDate") or md_a.get("end_date_iso")
        if end:
            try:
                end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if end_dt < datetime.now(timezone.utc) + timedelta(hours=PMM_MIN_HOURS_TO_END):
                    return "resolves too soon"
            except ValueError:
                pass
        return None

    @staticmethod
    def _pair_edge(md_a, md_b):
        return 1.0 - float(md_a["best_bid"]) - float(md_b["best_bid"])

    @staticmethod
    def _queue_at_bid(md):
        """Shares already resting at the best bid: we would wait behind all of them."""
        levels = get_book_levels(md, "bids")
        return levels[0][1] if levels else float("inf")

    # ------------------------------------------------------------ order helpers

    async def _set_quote(self, token_id, price, size, md):
        """Make our resting bid on token_id exactly (price, size); None cancels it. Returns fills executed."""
        existing = self._quotes(token_id)
        if price is None or md is None:
            for o in existing:
                await self.ee.cancel_order(o["order_id"])
            return 0

        tick = get_tick_size(md)
        price = round_to_tick(price, tick, "BUY")
        if price < tick or size < get_min_order_size(md):
            for o in existing:
                await self.ee.cancel_order(o["order_id"])
            return 0

        # Keep an order already resting at the right price: re-placing it loses queue priority
        if len(existing) == 1 and abs(existing[0]["price"] - price) < 1e-9:
            return 0
        for o in existing:
            await self.ee.cancel_order(o["order_id"])

        result = await self.ee.place_limit_order(
            token_id=token_id, price=price, size=size, side="BUY",
            market_data=md, is_aggressive=False, strategy=PAIR_MM_STRATEGY
        )
        if result.get("status") == "SUCCESS":  # crossed on arrival: record the taker part
            self.pm.update_position(
                token_id=token_id, size=result["fill_size"], price=result["fill_price"], side="BUY",
                strategy=PAIR_MM_STRATEGY, spread=md.get("spread", 0), slippage=result.get("slippage", 0),
                is_maker=False, order_id=result.get("order_id"), fee=result.get("fee")
            )
            return 1
        return 0

    def _merge(self, key, token_a, token_b):
        """Merge matched YES+NO shares into $1 each (paper) and realize the locked edge."""
        size_a, avg_a = self._inventory(token_a)
        size_b, avg_b = self._inventory(token_b)
        matched = min(size_a, size_b)
        if matched <= 1e-6:
            return 0

        # Split the $1 payout so leg A carries the whole edge and leg B closes flat
        pnl_a = self.pm.update_position(
            token_id=token_a, size=matched, price=1.0 - avg_b, side="SELL", strategy=PAIR_MM_STRATEGY,
            is_maker=True, fee=0.0, exit_reason="PAIR_MERGE"
        )
        pnl_b = self.pm.update_position(
            token_id=token_b, size=matched, price=avg_b, side="SELL", strategy=PAIR_MM_STRATEGY,
            is_maker=True, fee=0.0, exit_reason="PAIR_MERGE"
        )
        if self.tracker is not None:
            self.tracker._update_virtual_position(token_a, matched, 1.0 - avg_b, "SELL", 0.0)
            self.tracker._update_virtual_position(token_b, matched, avg_b, "SELL", 0.0)
        if self.risk is not None:
            self.risk.update_after_trade(success=True, token_id=token_a, pnl=pnl_a + pnl_b, strategy=PAIR_MM_STRATEGY)
        logger.info(
            f"PAIR_MERGE: {key[:20]}... | {matched:.2f} pairs @ cost {avg_a + avg_b:.4f} -> $1.00 | "
            f"PnL {pnl_a + pnl_b:+.4f}"
        )
        return 1

    async def _unwind(self, key, held_token, size, md, reason):
        """Sell a stuck single leg as taker and pause the market."""
        for tid in self.market_tokens.get(key, [held_token]):
            await self._set_quote(tid, None, 0, None)
        self.cooldown_until[key] = time.time() + PMM_COOLDOWN_S
        if md is None or not md.get("best_bid"):
            logger.warning(f"PMM_UNWIND_BLOCKED: {held_token[:20]}... | no book to sell into ({reason})")
            return 0

        tick = get_tick_size(md)
        limit = max(tick, float(md["best_bid"]) - 2 * tick)
        result = await self.ee.place_limit_order(
            token_id=held_token, price=limit, size=size, side="SELL",
            market_data=md, is_aggressive=True, strategy=PAIR_MM_STRATEGY
        )
        if result.get("status") != "SUCCESS":
            logger.warning(f"PMM_UNWIND_NO_FILL: {held_token[:20]}... | {result.get('error')} ({reason})")
            return 0

        pnl = self.pm.update_position(
            token_id=held_token, size=result["fill_size"], price=result["fill_price"], side="SELL",
            strategy=PAIR_MM_STRATEGY, spread=md.get("spread", 0), slippage=result.get("slippage", 0),
            is_maker=False, order_id=result.get("order_id"), exit_reason=f"PMM_UNWIND {reason}",
            fee=result.get("fee")
        )
        if self.risk is not None:
            self.risk.update_after_trade(success=True, token_id=held_token, pnl=pnl, strategy=PAIR_MM_STRATEGY)
        logger.warning(f"PMM_UNWIND: {held_token[:20]}... | sold {result['fill_size']:.2f} @ {result['fill_price']:.4f} | PnL {pnl:+.4f} | {reason}")
        if self.pm.positions.get(held_token, {}).get("size", 0) <= 1e-6:
            self.unmatched_since.pop(key, None)
        return 1

    # --------------------------------------------------------------- main step

    async def step(self, enriched_tokens, market_data_map):
        """Run once per loop after resting fills are processed. Returns executions made."""
        now = time.time()
        executed = 0
        for md in enriched_tokens:
            self._remember(md)

        # Resolve tokens we hold/quote but whose market we do not know yet
        managed = set()
        for key in self._managed_keys():
            if isinstance(key, tuple):
                token_id = key[1]
                resolved = await self._lookup_market(token_id) if token_id not in self._unknown_tokens else None
                if resolved is None:
                    if token_id not in self._unknown_tokens:
                        logger.warning(f"PMM: cannot find the market of held token {token_id[:20]}...; leaving it untouched")
                        self._unknown_tokens.add(token_id)
                    continue
                key = resolved
            managed.add(key)

        for key in managed:
            executed += await self._manage(key, market_data_map, now)

        # Open new pairs on the best eligible markets while capacity remains
        capacity = PMM_MAX_MARKETS - len(managed)
        if capacity <= 0:
            return executed
        candidates = []
        for key, (token_a, token_b) in self.market_tokens.items():
            if key in managed:
                continue
            md_a, md_b = market_data_map.get(token_a), market_data_map.get(token_b)
            if not md_a or not md_b:
                continue
            if self._ineligible_reason(key, md_a, md_b, now):
                continue
            edge = self._pair_edge(md_a, md_b)
            if edge >= PMM_MIN_PAIR_EDGE - 1e-9:
                # The slower leg decides when the pair completes, so rank by its queue
                queue = max(self._queue_at_bid(md_a), self._queue_at_bid(md_b))
                candidates.append((-edge, queue, key, token_a, token_b, md_a, md_b))

        for neg_edge, queue, key, token_a, token_b, md_a, md_b in sorted(candidates, key=lambda c: c[:2])[:capacity]:
            edge = -neg_edge
            cost = (float(md_a["best_bid"]) + float(md_b["best_bid"])) * PMM_PAIR_SHARES
            if cost > self._cash_available():
                break
            logger.info(
                f"PMM_OPEN: {key[:20]}... | bids {md_a['best_bid']:.3f} + {md_b['best_bid']:.3f} | "
                f"edge {edge:.3f}/share on {PMM_PAIR_SHARES:g} shares | queue ahead {queue:,.0f} shares"
            )
            executed += await self._set_quote(token_a, float(md_a["best_bid"]), PMM_PAIR_SHARES, md_a)
            executed += await self._set_quote(token_b, float(md_b["best_bid"]), PMM_PAIR_SHARES, md_b)
        return executed

    async def _manage(self, key, market_data_map, now):
        tokens = self.market_tokens.get(key)
        if not tokens:
            return 0
        token_a, token_b = tokens
        executed = self._merge(key, token_a, token_b)

        md = {}
        for tid in tokens:
            md[tid] = market_data_map.get(tid) or await self._fresh_book(tid)

        size_a, avg_a = self._inventory(token_a)
        size_b, avg_b = self._inventory(token_b)

        # Flat: keep both bids at the touch while the market stays eligible and profitable
        if size_a <= 1e-6 and size_b <= 1e-6:
            self.unmatched_since.pop(key, None)
            md_a, md_b = md[token_a], md[token_b]
            if (md_a and md_b and not self._ineligible_reason(key, md_a, md_b, now)
                    and self._pair_edge(md_a, md_b) >= PMM_MIN_PAIR_EDGE - 1e-9):
                executed += await self._set_quote(token_a, float(md_a["best_bid"]), PMM_PAIR_SHARES, md_a)
                executed += await self._set_quote(token_b, float(md_b["best_bid"]), PMM_PAIR_SHARES, md_b)
            else:
                executed += await self._set_quote(token_a, None, 0, None)
                executed += await self._set_quote(token_b, None, 0, None)
            return executed

        # One leg held: only bid the missing leg, at a price that still locks the edge
        held, missing = (token_a, token_b) if size_a > 1e-6 else (token_b, token_a)
        held_size, held_avg = (size_a, avg_a) if held == token_a else (size_b, avg_b)
        since = self.unmatched_since.setdefault(key, now)
        held_md, missing_md = md[held], md[missing]

        held_bid = float((held_md or {}).get("best_bid") or 0)
        if now - since > PMM_LEG_TIMEOUT_S:
            return executed + await self._unwind(key, held, held_size, held_md, f"timeout {now - since:.0f}s")
        if held_bid and held_bid <= held_avg - PMM_MAX_ADVERSE_MOVE:
            return executed + await self._unwind(key, held, held_size, held_md, f"adverse bid {held_bid:.3f} vs cost {held_avg:.3f}")

        executed += await self._set_quote(held, None, 0, None)
        if missing_md and missing_md.get("best_bid"):
            cap = 1.0 - held_avg - PMM_MIN_PAIR_EDGE
            price = min(float(missing_md["best_bid"]), cap)
            size = max(held_size, get_min_order_size(missing_md))
            executed += await self._set_quote(missing, price, size, missing_md)
        return executed
