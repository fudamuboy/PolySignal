"""
OBI v1 vs v2 — Live Performance Comparison Tracker
====================================================
Run at any time to compare OBI performance before and after the v2 fix.

v1 cutoff = restart timestamp when OBI v2 went live.
v2 start  = 2026-06-04 20:56:00 UTC+3 (17:56:00 UTC)
"""
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sqlite3
import datetime
from collections import defaultdict

DB_PATH    = "storage/database.db"
STRATEGY   = "OrderBookImbalanceStrategy"
FEE_BPS    = 20          # 0.20% per leg

# Timestamp when OBI v2 went live with the price filter (UTC time stored in DB)
OBI_V2_START = datetime.datetime(2026, 6, 4, 17, 56, 0)   # UTC time

def load_obi_trades():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()
    cur.execute(
        "SELECT * FROM trades WHERE strategy = ? ORDER BY timestamp ASC",
        (STRATEGY,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def pair_trades(rows):
    """Pair BUY→SELL chronologically per token. Returns list of round-trip dicts."""
    token_buys  = defaultdict(list)
    token_sells = defaultdict(list)
    for r in rows:
        ts = datetime.datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        r["_ts"] = ts
        if r["side"] == "BUY":
            token_buys[r["token_id"]].append(r)
        else:
            token_sells[r["token_id"]].append(r)

    completed = []
    for token_id, buy_list in token_buys.items():
        sell_list = list(token_sells[token_id])
        for b in buy_list:
            match = None
            for s in sell_list:
                if s["_ts"] >= b["_ts"]:
                    match = s
                    sell_list.remove(s)
                    break
            if match:
                buy_price  = float(b["price"])
                sell_price = float(match["price"])
                size       = float(b["size"])
                hold_secs  = (match["_ts"] - b["_ts"]).total_seconds()
                gross_pnl  = (sell_price - buy_price) * size
                fee_cost   = buy_price * size * (FEE_BPS / 10000) * 2
                net_pnl    = float(match["realized_pnl"])
                # Spread paid on entry (ask - mid, proxy)
                spread_paid = float(b.get("spread", 0)) if b.get("spread") else 0.0

                completed.append({
                    "token_id":    token_id[:20],
                    "buy_ts":      b["_ts"],
                    "era":         "v1" if b["_ts"] < OBI_V2_START else "v2",
                    "buy_price":   buy_price,
                    "sell_price":  sell_price,
                    "size":        size,
                    "gross_pnl":   gross_pnl,
                    "fee_cost":    fee_cost,
                    "net_pnl":     net_pnl,
                    "hold_secs":   hold_secs,
                    "spread_paid": spread_paid,
                })
    return completed

def stats(trades):
    if not trades:
        return {
            "count": 0, "wins": 0, "win_rate": 0.0,
            "net_pnl": 0.0, "ev": 0.0,
            "avg_hold": 0.0, "avg_gross": 0.0,
            "avg_fee": 0.0, "avg_spread": 0.0,
            "gross_pnl": 0.0, "max_dd": 0.0,
        }
    wins      = [t for t in trades if t["net_pnl"] > 0]
    net_pnl   = sum(t["net_pnl"] for t in trades)
    gross_pnl = sum(t["gross_pnl"] for t in trades)
    # Max drawdown on cumulative net PnL
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["buy_ts"]):
        cum += t["net_pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "count":      len(trades),
        "wins":       len(wins),
        "win_rate":   len(wins) / len(trades) * 100,
        "net_pnl":    net_pnl,
        "gross_pnl":  gross_pnl,
        "ev":         net_pnl / len(trades),
        "avg_hold":   sum(t["hold_secs"] for t in trades) / len(trades),
        "avg_gross":  gross_pnl / len(trades),
        "avg_fee":    sum(t["fee_cost"] for t in trades) / len(trades),
        "avg_spread": sum(t["spread_paid"] for t in trades) / len(trades),
        "max_dd":     max_dd,
    }

def fmt_delta(v1_val, v2_val, higher_is_better=True):
    """Return coloured arrow string for terminal."""
    if v2_val == v1_val:
        return "  (no change)"
    better = (v2_val > v1_val) if higher_is_better else (v2_val < v1_val)
    arrow  = "▲" if v2_val > v1_val else "▼"
    diff   = v2_val - v1_val
    tag    = "BETTER" if better else "WORSE"
    return f"  {arrow} {diff:+.4f}  [{tag}]"

def print_report(v1, v2):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print()
    print("=" * 68)
    print(f"  OBI v1 vs v2 — Performance Comparison   [{now}]")
    print(f"  v2 live since: {OBI_V2_START.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 68)

    rows = [
        ("Completed trades",   f"{v1['count']:>6}",     f"{v2['count']:>6}",     None,             True),
        ("Win rate (%)",       f"{v1['win_rate']:>6.1f}", f"{v2['win_rate']:>6.1f}", v2['win_rate']-v1['win_rate'], True),
        ("Net PnL ($)",        f"{v1['net_pnl']:>+6.4f}", f"{v2['net_pnl']:>+6.4f}", v2['net_pnl']-v1['net_pnl'], True),
        ("EV per trade ($)",   f"{v1['ev']:>+6.4f}",    f"{v2['ev']:>+6.4f}",    v2['ev']-v1['ev'], True),
        ("Avg gross PnL ($)",  f"{v1['avg_gross']:>+6.4f}", f"{v2['avg_gross']:>+6.4f}", v2['avg_gross']-v1['avg_gross'], True),
        ("Avg fee cost ($)",   f"{v1['avg_fee']:>6.4f}", f"{v2['avg_fee']:>6.4f}", v2['avg_fee']-v1['avg_fee'], False),
        ("Avg spread paid",    f"{v1['avg_spread']:>6.4f}", f"{v2['avg_spread']:>6.4f}", v2['avg_spread']-v1['avg_spread'], False),
        ("Avg hold time (s)",  f"{v1['avg_hold']:>6.1f}", f"{v2['avg_hold']:>6.1f}", v2['avg_hold']-v1['avg_hold'], None),
        ("Max drawdown ($)",   f"{v1['max_dd']:>6.4f}", f"{v2['max_dd']:>6.4f}", v2['max_dd']-v1['max_dd'], False),
    ]

    print(f"  {'Metric':<22} {'OBI v1':>10}  {'OBI v2':>10}  {'Change'}")
    print(f"  {'-'*22} {'-'*10}  {'-'*10}  {'-'*24}")
    for label, v1_s, v2_s, diff, hib in rows:
        if diff is None:
            change = ""
        elif hib is None:
            change = f"  {'▲' if diff>0 else '▼'} {diff:+.1f}s"
        else:
            better = (diff > 0) if hib else (diff < 0)
            arrow  = "▲" if diff > 0 else "▼"
            tag    = "BETTER" if better else "WORSE"
            if hib:
                change = f"  {arrow} {diff:+.4f}  [{tag}]"
            else:
                change = f"  {arrow} {diff:+.4f}  [{tag}]"
        print(f"  {label:<22} {v1_s:>10}  {v2_s:>10}  {change}")

    print()
    print("── Verdict ─────────────────────────────────────────────────────")
    if v2["count"] == 0:
        print("  OBI v2: NO COMPLETED TRADES YET — accumulating...")
    elif v2["ev"] > 0:
        print(f"  ✅ OBI v2 EV POSITIVE: ${v2['ev']:+.4f} per trade")
    elif v2["ev"] > v1["ev"]:
        print(f"  ⚠  OBI v2 EV improving: ${v2['ev']:+.4f} (was ${v1['ev']:+.4f}) — still negative")
    else:
        print(f"  ❌ OBI v2 EV still negative: ${v2['ev']:+.4f} — needs more data or re-audit")

    if v2["count"] < 10:
        print(f"  ⏳ Sample size too small ({v2['count']} trades) — results not statistically significant yet.")
    print("=" * 68)
    print()

def main():
    rows      = load_obi_trades()
    completed = pair_trades(rows)

    v1_trades = [t for t in completed if t["era"] == "v1"]
    v2_trades = [t for t in completed if t["era"] == "v2"]

    v1 = stats(v1_trades)
    v2 = stats(v2_trades)

    print_report(v1, v2)

    # Also print raw v2 trades for transparency
    if v2_trades:
        print("── OBI v2 Individual Trades ─────────────────────────────────────")
        print(f"  {'Timestamp':<20} {'Token':>20} {'Buy':>6} {'Sell':>6} {'Size':>5} {'NetPnL':>8} {'Hold(s)':>8}")
        print(f"  {'-'*20} {'-'*20} {'-'*6} {'-'*6} {'-'*5} {'-'*8} {'-'*8}")
        for t in sorted(v2_trades, key=lambda x: x["buy_ts"]):
            print(
                f"  {t['buy_ts'].strftime('%Y-%m-%d %H:%M:%S'):20} "
                f"{t['token_id']:>20} "
                f"{t['buy_price']:6.4f} {t['sell_price']:6.4f} "
                f"{t['size']:5.1f} {t['net_pnl']:+8.4f} {t['hold_secs']:8.1f}"
            )
        print()

if __name__ == "__main__":
    main()
