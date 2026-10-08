"""
Per-strategy paper-trading report from storage/database.db.

Usage:
    venv/bin/python report_paper.py            # all recorded trades
    venv/bin/python report_paper.py --days 7   # last 7 days only
    venv/bin/python report_paper.py --since "2026-10-08 00:00"   # UTC timestamp

Realized PnL is recorded on SELL fills and is already net of entry + exit fees.
"""
import argparse
import sqlite3

from app.config import DB_PATH


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=None, help="only include the last N days")
    parser.add_argument("--since", default=None, help="only include trades at or after this UTC timestamp")
    args = parser.parse_args()

    clauses = []
    params = []
    if args.days:
        clauses.append("timestamp >= datetime('now', ?)")
        params.append(f"-{args.days} days")
    if args.since:
        clauses.append("timestamp >= ?")
        params.append(args.since)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = sqlite3.connect(DB_PATH)
    span = conn.execute(f"SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM trades {where}", params).fetchone()
    print(f"Database : {DB_PATH}")
    print(f"Period   : {span[0]} -> {span[1]}  ({span[2]} fills)\n")

    rows = conn.execute(f"""
        SELECT strategy,
               SUM(side = 'BUY')                                  AS buys,
               SUM(side = 'SELL')                                 AS closes,
               SUM(CASE WHEN side = 'SELL' AND realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN side = 'SELL' THEN realized_pnl ELSE 0 END)           AS net_pnl,
               SUM(fee_paid)                                      AS fees,
               SUM(CASE WHEN side = 'SELL' AND realized_pnl > 0 THEN realized_pnl ELSE 0 END) AS gross_win,
               SUM(CASE WHEN side = 'SELL' AND realized_pnl < 0 THEN -realized_pnl ELSE 0 END) AS gross_loss
        FROM trades {where}
        GROUP BY strategy
        ORDER BY net_pnl DESC
    """, params).fetchall()

    if not rows:
        print("No trades recorded yet.")
        return

    header = f"{'strategy':28} {'buys':>5} {'closes':>6} {'win%':>6} {'net PnL $':>10} {'avg/close $':>11} {'fees $':>8} {'PF':>5}"
    print(header)
    print("-" * len(header))
    for strategy, buys, closes, wins, net_pnl, fees, gross_win, gross_loss in rows:
        win_rate = (wins / closes * 100) if closes else 0.0
        avg = (net_pnl / closes) if closes else 0.0
        pf = (gross_win / gross_loss) if gross_loss else float("inf") if gross_win else 0.0
        print(f"{strategy:28} {buys:5d} {closes:6d} {win_rate:5.1f}% {net_pnl:+10.4f} {avg:+11.4f} {fees:8.4f} {pf:5.2f}")

    exits = conn.execute(f"""
        SELECT COALESCE(SUBSTR(exit_reason, 1, INSTR(exit_reason || ' ', ' ') - 1), 'resting fill') AS reason,
               COUNT(*), SUM(realized_pnl)
        FROM trades {where} {'AND' if where else 'WHERE'} side = 'SELL'
        GROUP BY reason ORDER BY COUNT(*) DESC
    """, params).fetchall()
    if exits:
        print("\nExits by reason:")
        for reason, count, pnl in exits:
            print(f"  {reason:28} {count:5d}  {pnl:+.4f} $")

    open_pos = conn.execute("SELECT COUNT(*), COALESCE(SUM(size * avg_price), 0) FROM positions WHERE size > 0").fetchone()
    print(f"\nOpen positions: {open_pos[0]} (cost basis ${open_pos[1]:.2f}, not included in PnL above)")
    print("A strategy is only worth considering once it has dozens of closes with a positive avg/close.")


if __name__ == "__main__":
    main()
