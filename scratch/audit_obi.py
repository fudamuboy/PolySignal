"""
OBI Audit Script — Identify root cause of negative EV
Queries live database for all OrderBookImbalanceStrategy trades.
"""
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sqlite3
import datetime
from collections import defaultdict

DB_PATH = "storage/database.db"
STRATEGY = "OrderBookImbalanceStrategy"
ESTIMATED_FEE_BPS = 20   # 0.20% per leg = 0.40% round trip

def audit_obi():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM trades WHERE strategy = ? ORDER BY timestamp ASC", (STRATEGY,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not rows:
        print(f"No trades found for strategy: {STRATEGY}")
        return

    buys  = [r for r in rows if r["side"] == "BUY"]
    sells = [r for r in rows if r["side"] == "SELL"]

    print(f"\nRaw OBI trades — {len(buys)} BUYs | {len(sells)} SELLs\n")

    # ── Pair BUY → SELL chronologically per token ──────────────────────
    token_buys  = defaultdict(list)
    token_sells = defaultdict(list)
    for r in buys:
        token_buys[r["token_id"]].append(r)
    for r in sells:
        token_sells[r["token_id"]].append(r)

    completed = []
    for token_id, buy_list in token_buys.items():
        sell_list = list(token_sells[token_id])
        for b in buy_list:
            b_time = datetime.datetime.strptime(b["timestamp"], "%Y-%m-%d %H:%M:%S")
            match = None
            for s in sell_list:
                s_time = datetime.datetime.strptime(s["timestamp"], "%Y-%m-%d %H:%M:%S")
                if s_time >= b_time:
                    match = s
                    sell_list.remove(s)
                    break
            if match:
                s_time = datetime.datetime.strptime(match["timestamp"], "%Y-%m-%d %H:%M:%S")
                hold = (s_time - b_time).total_seconds()
                buy_price  = float(b["price"])
                sell_price = float(match["price"])
                size       = float(b["size"])
                gross_pnl  = (sell_price - buy_price) * size
                fee_cost   = buy_price * size * (ESTIMATED_FEE_BPS / 10000) * 2  # round-trip
                net_pnl    = float(match["realized_pnl"])
                spread_cost = max(0.0, -gross_pnl + net_pnl + fee_cost)  # implied
                completed.append({
                    "token_id":    token_id[:28],
                    "buy_price":   buy_price,
                    "sell_price":  sell_price,
                    "size":        size,
                    "gross_pnl":   gross_pnl,
                    "fee_cost":    fee_cost,
                    "net_pnl":     net_pnl,
                    "hold_secs":   hold,
                    "loss_driver": None,  # determined below
                })

    if not completed:
        print("No completed (paired) OBI trades found.")
        return

    # ── Classify each trade by loss driver ─────────────────────────────
    fee_losses     = []
    spread_losses  = []
    adverse_losses = []
    winners        = []

    for t in completed:
        if t["net_pnl"] > 0:
            winners.append(t)
            t["loss_driver"] = "WIN"
            continue
        # Fee-dominant: gross was positive but fees wiped it
        if t["gross_pnl"] >= 0 and t["net_pnl"] < 0:
            fee_losses.append(t)
            t["loss_driver"] = "FEE_DOMINANT"
        # Adverse price move — sold lower than bought
        elif t["gross_pnl"] < 0:
            if abs(t["fee_cost"]) < abs(t["gross_pnl"]) * 0.3:
                adverse_losses.append(t)
                t["loss_driver"] = "ADVERSE_PRICE_MOVE"
            else:
                spread_losses.append(t)
                t["loss_driver"] = "SPREAD_CROSSING"
        else:
            adverse_losses.append(t)
            t["loss_driver"] = "OTHER"

    total      = len(completed)
    net_total  = sum(t["net_pnl"] for t in completed)
    avg_hold   = sum(t["hold_secs"] for t in completed) / total

    print("=" * 60)
    print("OBI TRADE AUDIT REPORT")
    print("=" * 60)
    print(f"  Completed round-trips : {total}")
    print(f"  Winners               : {len(winners)}")
    print(f"  Losers                : {total - len(winners)}")
    print(f"  Win rate              : {len(winners)/total*100:.1f}%")
    print(f"  Net PnL               : ${net_total:+.4f}")
    print(f"  EV per trade          : ${net_total/total:+.4f}")
    print(f"  Avg hold time         : {avg_hold:.1f}s ({avg_hold/60:.1f} min)")
    print()

    print("── Loss Driver Breakdown ──────────────────────────────────")
    for label, group in [
        ("FEE_DOMINANT  (gross +, fees wiped it)", fee_losses),
        ("ADVERSE_PRICE (sold below buy price)",   adverse_losses),
        ("SPREAD_CROSS  (spread larger than move)", spread_losses),
    ]:
        pnl = sum(t["net_pnl"] for t in group)
        print(f"  {label:46s} count={len(group):3d}  PnL=${pnl:+.4f}")

    print()

    # ── Per-trade detail for worst losers ──────────────────────────────
    losers = [t for t in completed if t["net_pnl"] < 0]
    losers.sort(key=lambda x: x["net_pnl"])

    print("── Top 10 Worst OBI Trades ────────────────────────────────")
    print(f"  {'Token':28} {'Buy':>6} {'Sell':>6} {'Size':>6} {'GrossPnL':>9} {'Fees':>7} {'NetPnL':>8} {'Driver'}")
    print(f"  {'-'*28} {'-'*6} {'-'*6} {'-'*6} {'-'*9} {'-'*7} {'-'*8} {'-'*20}")
    for t in losers[:10]:
        print(f"  {t['token_id']:28} {t['buy_price']:6.4f} {t['sell_price']:6.4f} "
              f"{t['size']:6.2f} {t['gross_pnl']:+9.4f} {t['fee_cost']:7.4f} "
              f"{t['net_pnl']:+8.4f} {t['loss_driver']}")

    # ── Statistics on buy_price to understand spread crossing ──────────
    print()
    buy_prices = [t["buy_price"] for t in completed]
    sell_prices = [t["sell_price"] for t in completed]
    price_deltas = [t["sell_price"] - t["buy_price"] for t in completed]

    print("── Price Statistics ───────────────────────────────────────")
    print(f"  Avg buy price   : {sum(buy_prices)/len(buy_prices):.4f}")
    print(f"  Avg sell price  : {sum(sell_prices)/len(sell_prices):.4f}")
    print(f"  Avg price delta : {sum(price_deltas)/len(price_deltas):+.4f}")
    print(f"  Trades sold higher : {sum(1 for d in price_deltas if d > 0)}")
    print(f"  Trades sold lower  : {sum(1 for d in price_deltas if d < 0)}")
    print(f"  Trades sold same   : {sum(1 for d in price_deltas if d == 0)}")

    # ── Recommended threshold adjustments ──────────────────────────────
    print()
    print("=" * 60)
    print("DIAGNOSIS SUMMARY")
    print("=" * 60)
    primary = max(
        ("FEE_DOMINANT",   len(fee_losses)),
        ("ADVERSE_PRICE",  len(adverse_losses)),
        ("SPREAD_CROSSING",len(spread_losses)),
        key=lambda x: x[1]
    )
    print(f"  PRIMARY LOSS DRIVER : {primary[0]} ({primary[1]} trades)")
    print()

    if primary[0] == "FEE_DOMINANT":
        print("  ACTION: Require larger OBI delta before entry.")
        print("  OBI pseudo-delta = obi * 0.10 is too small to clear 0.40% round-trip fees.")
        print("  RECOMMENDATION: Raise delta multiplier OR require higher min imbalance score.")
    elif primary[0] == "ADVERSE_PRICE":
        print("  ACTION: OBI signal is not predictive enough.")
        print("  Price reverses after entry despite imbalance.")
        print("  RECOMMENDATION: Add price momentum confirmation + tighter imbalance threshold.")
    elif primary[0] == "SPREAD_CROSSING":
        print("  ACTION: Entry spread exceeds anticipated profit.")
        print("  RECOMMENDATION: Add max-spread filter at OBI strategy level (not just risk manager).")
    print()

if __name__ == "__main__":
    audit_obi()
