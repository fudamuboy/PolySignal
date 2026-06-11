#!/usr/bin/env python3
"""
diag_prices.py — Offline diagnostic (does NOT interfere with the running bot).
Answers two questions:
  1. What mid_price deltas exist across a 20-second REST interval?
  2. What are the actual Polymarket WS message types/keys?

Run: source venv/bin/activate && python diag_prices.py
"""
import asyncio, json, time, collections
import websockets
from app.data_fetcher import DataFetcher
from app.market_filter import MarketFilter

async def main():
    df = DataFetcher()
    mf = MarketFilter()

    print("\n── Step 1: Fetch markets + enrich (loop 1) ──────────────────────────")
    markets = await df.fetch_markets()
    candidates = mf.filter_markets(markets)[:30]

    prices_loop1 = {}
    for m in candidates:
        for tok in m.get("tokens", []):
            t_id = tok.get("token_id")
            if not t_id: continue
            ob = await df.get_orderbook(str(t_id))
            if not ob: continue
            raw_bids = ob.get("bids", []) if isinstance(ob, dict) else getattr(ob, "bids", [])
            raw_asks = ob.get("asks", []) if isinstance(ob, dict) else getattr(ob, "asks", [])
            if not raw_bids or not raw_asks: continue
            def gp(lvl):
                if hasattr(lvl, "price"): return float(lvl.price)
                if isinstance(lvl, dict): return float(lvl.get("price") or lvl.get("p") or 0)
                return 0.0
            bids = sorted(raw_bids, key=gp, reverse=True)
            asks = sorted(raw_asks, key=gp)
            bb, ba = gp(bids[0]), gp(asks[0])
            mid = (bb + ba) / 2
            prices_loop1[str(t_id)] = {"mid": mid, "bid": bb, "ask": ba, "spread": ba - bb}

    print(f"Loop 1: enriched {len(prices_loop1)} tokens")
    sample = list(prices_loop1.items())[:5]
    for tid, v in sample:
        print(f"  {tid[:20]}... mid={v['mid']:.4f} bid={v['bid']:.4f} ask={v['ask']:.4f} spread={v['spread']:.4f}")

    # ── Step 2: Sample raw WS messages ────────────────────────────────────────
    print("\n── Step 2: WS message format sample (10s) ──────────────────────────")
    WS_URI = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    sub_ids = list(prices_loop1.keys())[:10]
    msg_types = collections.Counter()
    msg_keys_seen = collections.Counter()
    raw_samples = []

    try:
        async with websockets.connect(WS_URI, open_timeout=8) as ws:
            sub_msg = json.dumps({"type": "subscribe", "assets_ids": sub_ids})
            await ws.send(sub_msg)
            print(f"  Subscribed to {len(sub_ids)} tokens. Listening 10s...")
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    data = json.loads(raw)
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        mt = item.get("event") or item.get("type") or item.get("msg_type") or "UNKNOWN"
                        msg_types[mt] += 1
                        for k in item.keys():
                            msg_keys_seen[k] += 1
                        if len(raw_samples) < 3:
                            raw_samples.append(item)
                except asyncio.TimeoutError:
                    pass
    except Exception as e:
        print(f"  WS connect failed: {e}")

    print(f"\n  Message types seen: {dict(msg_types)}")
    print(f"  All keys seen: {dict(msg_keys_seen.most_common(20))}")
    print(f"\n  Raw message samples:")
    for i, s in enumerate(raw_samples, 1):
        print(f"  [{i}] {json.dumps(s)[:300]}")

    # ── Step 3: Wait 20s then fetch again ─────────────────────────────────────
    print(f"\n── Step 3: Waiting 20s then loop 2 ─────────────────────────────────")
    await asyncio.sleep(20)

    prices_loop2 = {}
    for t_id in prices_loop1:
        ob = await df.get_orderbook(t_id)
        if not ob: continue
        raw_bids = ob.get("bids", []) if isinstance(ob, dict) else getattr(ob, "bids", [])
        raw_asks = ob.get("asks", []) if isinstance(ob, dict) else getattr(ob, "asks", [])
        if not raw_bids or not raw_asks: continue
        def gp(lvl):
            if hasattr(lvl, "price"): return float(lvl.price)
            if isinstance(lvl, dict): return float(lvl.get("price") or lvl.get("p") or 0)
            return 0.0
        bids = sorted(raw_bids, key=gp, reverse=True)
        asks = sorted(raw_asks, key=gp)
        bb, ba = gp(bids[0]), gp(asks[0])
        prices_loop2[t_id] = {"mid": (bb + ba) / 2, "bid": bb, "ask": ba}

    # ── Step 4: Compute deltas ─────────────────────────────────────────────────
    print(f"\n── Step 4: Delta analysis (loop2_mid - loop1_mid) ──────────────────")
    deltas = []
    for tid in prices_loop1:
        if tid not in prices_loop2: continue
        d = prices_loop2[tid]["mid"] - prices_loop1[tid]["mid"]
        deltas.append((abs(d), d, tid, prices_loop1[tid]["mid"], prices_loop2[tid]["mid"]))

    deltas.sort(reverse=True)
    above_020 = sum(1 for x in deltas if x[0] >= 0.020)
    above_015 = sum(1 for x in deltas if x[0] >= 0.015)
    zero       = sum(1 for x in deltas if x[0] == 0.0)

    print(f"  Total tokens compared: {len(deltas)}")
    print(f"  |delta| >= 0.020 (MeanReversion threshold): {above_020}")
    print(f"  |delta| >= 0.015 (Momentum MIN_EDGE):       {above_015}")
    print(f"  |delta| == 0.000 (completely flat):          {zero}")
    print(f"\n  Top 10 movers:")
    for abs_d, d, tid, p1, p2 in deltas[:10]:
        print(f"  {tid[:22]}... delta={d:+.4f}  ({p1:.4f} → {p2:.4f})")
    print(f"\n  Bottom 5 (flattest):")
    for abs_d, d, tid, p1, p2 in deltas[-5:]:
        print(f"  {tid[:22]}... delta={d:+.4f}  ({p1:.4f} → {p2:.4f})")

    print("\n── VERDICT ──────────────────────────────────────────────────────────")
    if above_020 > 0:
        print(f"  ✅ {above_020} tokens have sufficient delta. Signals SHOULD fire.")
        print(f"     If bot still shows 0, the issue is in WS orderbook cache (not REST data).")
    elif above_015 > 0:
        print(f"  ⚠️  Tokens move but below 0.020 threshold. Strategy threshold may be too high for current market.")
    elif zero == len(deltas):
        print(f"  ❌ ALL tokens are completely flat over 20s. Market is dormant right now.")
    else:
        print(f"  ⚠️  Max delta = {deltas[0][0]:.4f} — all below 0.020 threshold.")
        print(f"     Current market is too flat for the strategy thresholds to fire.")

asyncio.run(main())
