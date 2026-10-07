"""
Realistic paper-trading fill model.

Pure functions used by ExecutionEngine (paper mode) and PaperValidationTracker
to simulate how the Polymarket CLOB would actually treat an order:

- prices must sit on the market's tick grid (orderPriceMinTickSize)
- orders below orderMinSize shares are rejected
- marketable orders walk the real order book level by level, honouring the
  limit price (FAK semantics: unfilled remainder is not magically filled)
- taker fees follow the market's feeSchedule:
      fee = shares * p * rate * (p * (1 - p)) ** exponent
- resting (maker) orders only fill when the opposite side crosses our price,
  and they fill at OUR price. This is deliberately conservative: without trade
  prints we cannot see queue position, so touch-only fills are never assumed.
"""
import math

from .config import TAKER_FEE_BPS

DEFAULT_TICK_SIZE = 0.01
DEFAULT_MIN_ORDER_SIZE = 5.0


def _level_price_size(level):
    if isinstance(level, dict):
        price = level.get("price", level.get("p", 0))
        size = level.get("size", level.get("s", 0))
    else:
        price = getattr(level, "price", 0)
        size = getattr(level, "size", 0)
    return float(price or 0), float(size or 0)


def get_book_levels(market_data, book_side):
    """
    Return [(price, size), ...] best-first for book_side 'asks' or 'bids'.
    Falls back to a single synthetic level built from best price + top-of-book
    depth (USD) when no full orderbook is attached.
    """
    market_data = market_data or {}
    orderbook = market_data.get("orderbook")
    raw = []
    if orderbook is not None:
        raw = orderbook.get(book_side, []) if isinstance(orderbook, dict) else getattr(orderbook, book_side, [])

    levels = [_level_price_size(l) for l in (raw or [])]
    levels = [(p, s) for p, s in levels if p > 0 and s > 0]

    if not levels:
        best_key, depth_key = ("best_ask", "ask_depth") if book_side == "asks" else ("best_bid", "bid_depth")
        best = float(market_data.get(best_key) or 0)
        depth_usd = float(market_data.get(depth_key) or 0)
        if best > 0 and depth_usd > 0:
            levels = [(best, depth_usd / best)]

    return sorted(levels, key=lambda x: x[0], reverse=(book_side == "bids"))


def get_tick_size(market_data):
    md = market_data or {}
    tick = md.get("orderPriceMinTickSize") or md.get("minimum_tick_size") or md.get("tick_size")
    try:
        tick = float(tick)
    except (TypeError, ValueError):
        tick = 0
    return tick if tick > 0 else DEFAULT_TICK_SIZE


def get_min_order_size(market_data):
    md = market_data or {}
    try:
        min_size = float(md.get("orderMinSize") or md.get("minimum_order_size") or DEFAULT_MIN_ORDER_SIZE)
    except (TypeError, ValueError):
        min_size = DEFAULT_MIN_ORDER_SIZE
    return min_size


def round_to_tick(price, tick, side, aggressive=False):
    """
    Snap a price onto the tick grid.
    Passive orders round away from the touch (BUY down, SELL up) so we never
    post a better price than intended; aggressive orders round towards the
    touch (BUY up, SELL down) so they keep their crossing intent.
    """
    steps = price / tick
    round_up = (side == "BUY") == aggressive
    # small epsilon so 0.57 / 0.01 = 56.99999 does not floor to 0.56
    steps = math.ceil(steps - 1e-9) if round_up else math.floor(steps + 1e-9)
    snapped = round(steps * tick, 6)
    return min(max(snapped, tick), round(1 - tick, 6))


def taker_fee_rate(price, market_data=None):
    """Effective taker fee as a fraction of notional (shares * price)."""
    md = market_data or {}
    schedule = md.get("feeSchedule")
    if isinstance(schedule, dict) and schedule.get("rate") is not None:
        rate = float(schedule.get("rate") or 0)
        exponent = float(schedule.get("exponent") or 1)
        return rate * (price * (1 - price)) ** exponent
    if md.get("feesEnabled") is False:
        return 0.0
    # Unknown market fee model: fall back to the configured flat taker fee
    return TAKER_FEE_BPS / 10000.0


def maker_fee_rate(price, market_data=None):
    """Polymarket feeSchedules are takerOnly; makers pay nothing (rebates ignored)."""
    md = market_data or {}
    schedule = md.get("feeSchedule")
    if isinstance(schedule, dict) and schedule.get("takerOnly") is False:
        return taker_fee_rate(price, md)
    return 0.0


def simulate_taker_fill(side, size, limit_price, market_data):
    """
    Walk the opposite side of the book up to limit_price.
    Returns dict(filled_size, avg_price, best_price, slippage, fee, levels_used).
    filled_size may be < size (partial) or 0 (nothing within the limit).
    """
    book_side = "asks" if side == "BUY" else "bids"
    levels = get_book_levels(market_data, book_side)

    remaining = float(size)
    filled = 0.0
    notional = 0.0
    fee = 0.0
    levels_used = 0
    for price, avail in levels:
        if remaining <= 1e-9:
            break
        crosses = price <= limit_price + 1e-9 if side == "BUY" else price >= limit_price - 1e-9
        if not crosses:
            break
        take = min(remaining, avail)
        filled += take
        notional += take * price
        fee += take * price * taker_fee_rate(price, market_data)
        remaining -= take
        levels_used += 1

    best_price = levels[0][0] if levels else 0.0
    avg_price = notional / filled if filled > 0 else 0.0
    slippage = 0.0
    if filled > 0:
        slippage = (avg_price - best_price) if side == "BUY" else (best_price - avg_price)

    return {
        "filled_size": round(filled, 6),
        "avg_price": avg_price,
        "best_price": best_price,
        "slippage": slippage,
        "fee": fee,
        "levels_used": levels_used,
    }


def is_marketable(side, price, market_data):
    """True if a limit order at this price would cross the spread on arrival."""
    md = market_data or {}
    if side == "BUY":
        best_ask = float(md.get("best_ask") or 0)
        return 0 < best_ask <= price + 1e-9
    best_bid = float(md.get("best_bid") or 0)
    return best_bid > 0 and best_bid >= price - 1e-9


def resting_order_crossed(side, price, market_data):
    """
    A resting maker order is considered filled only once the opposite side has
    traded through to our level (ask <= our bid / bid >= our ask). Fill price is
    our own limit price, as on the real CLOB.
    """
    return is_marketable(side, price, market_data)
