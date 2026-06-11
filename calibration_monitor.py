#!/usr/bin/env python3
"""
calibration_monitor.py
======================
Reads the live bot log and produces a structured calibration report.
Run at any time or at end of the observation window.
Does NOT modify any trading parameters.

Usage:
    python calibration_monitor.py                  # live snapshot
    python calibration_monitor.py --full           # include all log files (bot.log.*)
"""
import re
import os
import sys
import json
import collections
import datetime
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).resolve().parent
LOG_DIR   = BASE_DIR / "storage" / "logs"
REPORT_DIR = BASE_DIR / "storage"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Regex patterns ──────────────────────────────────────────────────────────
RE_ACCEPTED  = re.compile(r"CALIBRATION_ACCEPTED\s*\|.*?token=([\w\-\.]+).*?side=(\w+).*?delta=([\d\.]+).*?RR=([\d\.]+).*?score=([\d\.]+)")
RE_REJECTED  = re.compile(r"CALIBRATION_REJECTED\s*\|.*?token=([\w\-\.]+).*?reason=(.+?)(?:\s*$|\s*\|)")
RE_LOOP      = re.compile(r"signals generated:\s*(\d+)")
RE_EXECUTED  = re.compile(r"paper trades executed:\s*(\d+)")
RE_PNL       = re.compile(r"Net Realized PnL:\s*([+-]?\d+\.?\d*)")
RE_NEAR_MISS = re.compile(r"NEAR_MISS\s*\|.*?token=([\w\-\.]+).*?rr=([\d\.]+).*?reason=(.+?)(?:\s*$)")
RE_TRADE_EXE = re.compile(r"TRADE_EXECUTED:.*?token_id.*?side=(\w+).*?size=([\d\.]+).*?price=([\d\.]+)")
RE_HOURLY    = re.compile(r"CALIBRATION_HOURLY_REPORT\s*\|.*?signals_generated=(\d+).*?signals_accepted=(\d+).*?paper_trades=(\d+).*?acceptance_ratio=([\d\.]+)%.*?all_rejections=\[(.+?)\]")

# ─── Determine which log files to read ───────────────────────────────────────
def collect_log_files(full=False):
    files = [LOG_DIR / "bot.log"]
    if full:
        for i in range(1, 10):
            f = LOG_DIR / f"bot.log.{i}"
            if f.exists():
                files.append(f)
    return [f for f in files if f.exists()]

# ─── Parse ────────────────────────────────────────────────────────────────────
def parse_logs(log_files):
    stats = {
        "signals_generated": 0,
        "signals_accepted":  0,
        "signals_rejected":  0,
        "paper_trades":      0,
        "paper_pnl":         0.0,
        "rejection_reasons": collections.Counter(),
        "accepted_signals":  [],   # list of dicts
        "near_misses":       [],   # list of dicts
        "hourly_snapshots":  [],
        "raw_trade_execs":   [],
    }

    for log_file in log_files:
        try:
            with open(log_file, "r", errors="replace") as fh:
                for line in fh:
                    # Signals generated
                    m = RE_LOOP.search(line)
                    if m:
                        stats["signals_generated"] += int(m.group(1))

                    # Paper trades executed
                    m = RE_EXECUTED.search(line)
                    if m:
                        stats["paper_trades"] += int(m.group(1))

                    # PnL
                    m = RE_PNL.search(line)
                    if m:
                        stats["paper_pnl"] = float(m.group(1))   # keep last value (running total)

                    # Accepted
                    m = RE_ACCEPTED.search(line)
                    if m:
                        stats["signals_accepted"] += 1
                        stats["accepted_signals"].append({
                            "token": m.group(1),
                            "side":  m.group(2),
                            "delta": float(m.group(3)),
                            "rr":    float(m.group(4)),
                            "score": float(m.group(5)),
                        })

                    # Rejected
                    m = RE_REJECTED.search(line)
                    if m:
                        stats["signals_rejected"] += 1
                        reason_raw = m.group(2).strip()
                        # Normalise reason key (strip numeric suffixes)
                        reason_key = re.sub(r"\(.*?\)", "", reason_raw).strip()
                        stats["rejection_reasons"][reason_key] += 1

                    # Near-misses
                    m = RE_NEAR_MISS.search(line)
                    if m:
                        stats["near_misses"].append({
                            "token":  m.group(1),
                            "rr":     float(m.group(2)),
                            "reason": m.group(3).strip(),
                        })

                    # Hourly snapshots
                    m = RE_HOURLY.search(line)
                    if m:
                        stats["hourly_snapshots"].append({
                            "generated": int(m.group(1)),
                            "accepted":  int(m.group(2)),
                            "trades":    int(m.group(3)),
                            "ratio":     float(m.group(4)),
                            "rejections": m.group(5),
                        })

                    # Raw trade executions
                    m = RE_TRADE_EXE.search(line)
                    if m:
                        stats["raw_trade_execs"].append({
                            "side":  m.group(1),
                            "size":  float(m.group(2)),
                            "price": float(m.group(3)),
                        })

        except FileNotFoundError:
            pass

    return stats

# ─── Assessment logic ─────────────────────────────────────────────────────────
def assess_calibration(stats):
    total     = stats["signals_generated"]
    accepted  = stats["signals_accepted"]
    ratio_pct = (accepted / total * 100) if total > 0 else 0

    if ratio_pct == 0:
        status = "CRITICAL — NO signals accepted yet. Bot may be over-filtered or not running long enough."
    elif ratio_pct < 2:
        status = "OVER-FILTERED — acceptance < 2%. Strategy is too conservative; consider relaxing thresholds."
    elif ratio_pct < 8:
        status = "TIGHT — acceptance 2-8%. Viable but lean; observe whether trades are profitable."
    elif ratio_pct < 20:
        status = "BALANCED — acceptance 8-20%. Healthy signal quality gate."
    elif ratio_pct < 35:
        status = "ACTIVE — acceptance 20-35%. Good activity level; watch RR quality."
    else:
        status = "AGGRESSIVE — acceptance > 35%. Risk of over-trading; verify RR remains positive."

    trades    = stats["paper_trades"]
    pnl       = stats["paper_pnl"]
    if trades == 0:
        viability = "Not yet viable — zero paper trades executed. Bot needs to generate and accept signals before assessment is meaningful."
    elif pnl > 0:
        viability = f"Promising — {trades} paper trades with positive PnL (+{pnl:.4f}). Strategy shows edge in paper mode."
    elif pnl == 0:
        viability = f"Neutral — {trades} paper trades, PnL={pnl:.4f}. Not enough data yet."
    else:
        viability = f"Underperforming — {trades} paper trades with negative PnL ({pnl:.4f}). Review strategy edge."

    return status, viability

# ─── Report generator ─────────────────────────────────────────────────────────
def generate_report(stats, log_files):
    total     = stats["signals_generated"]
    accepted  = stats["signals_accepted"]
    rejected  = stats["signals_rejected"]
    ratio_pct = (accepted / total * 100) if total > 0 else 0

    top_rejections = stats["rejection_reasons"].most_common(10)

    top_accepted = sorted(
        stats["accepted_signals"], key=lambda x: x["rr"], reverse=True
    )[:10]

    # Near-misses: highest RR (closest to passing)
    top_near_misses = sorted(
        stats["near_misses"], key=lambda x: x["rr"], reverse=True
    )[:10]

    status, viability = assess_calibration(stats)

    # ── Comparison estimate (vs common "old" baseline: ~5% acceptance) ────────
    old_ratio_pct = 5.0   # historical conservative baseline assumption
    freq_change = ratio_pct - old_ratio_pct

    lines = []
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("=" * 70)
    lines.append(f"  POLYMARKET BOT — CALIBRATION REPORT  ({ts})")
    lines.append(f"  Log files: {', '.join(str(f.name) for f in log_files)}")
    lines.append("=" * 70)

    lines.append("")
    lines.append("── 1. TOTALS ─────────────────────────────────────────────────────────")
    lines.append(f"   Total signals generated   : {total:,}")
    lines.append(f"   Total CALIBRATION_ACCEPTED : {accepted:,}")
    lines.append(f"   Total CALIBRATION_REJECTED : {rejected:,}")
    lines.append(f"   Acceptance ratio           : {ratio_pct:.2f}%")
    lines.append("")

    lines.append("── 2. PAPER TRADING ──────────────────────────────────────────────────")
    lines.append(f"   Paper trades executed      : {stats['paper_trades']:,}")
    lines.append(f"   Paper PnL (latest snapshot): {stats['paper_pnl']:+.4f}")
    lines.append("")

    lines.append("── 3. TOP REJECTION REASONS ──────────────────────────────────────────")
    if top_rejections:
        for reason, count in top_rejections:
            pct = count / rejected * 100 if rejected > 0 else 0
            lines.append(f"   [{count:>6}] ({pct:5.1f}%)  {reason}")
    else:
        lines.append("   (no rejection data yet)")
    lines.append("")

    lines.append("── 4. TOP ACCEPTED SIGNALS (by RR) ───────────────────────────────────")
    if top_accepted:
        for s in top_accepted:
            lines.append(
                f"   token={s['token'][:24]:<24} side={s['side']} "
                f"delta={s['delta']:.4f}  RR={s['rr']:.3f}  score={s['score']:.2f}"
            )
    else:
        lines.append("   (no accepted signals yet)")
    lines.append("")

    lines.append("── 5. TOP NEAR-MISS REJECTED SIGNALS (closest to passing) ────────────")
    if top_near_misses:
        for nm in top_near_misses:
            lines.append(
                f"   token={nm['token'][:24]:<24}  RR={nm['rr']:.3f}  "
                f"reason={nm['reason'][:50]}"
            )
    else:
        lines.append("   (no near-miss data yet — near-misses only buffered at RR reject stage)")
    lines.append("")

    lines.append("── 6. TRADES SAVED BY CALIBRATION CHANGES ───────────────────────────")
    # "saved" = rejections that prevented bad trades (high rejection on noise/ev filters)
    noise_saved = (
        stats["rejection_reasons"].get("NOISE_MOVE_TOO_SMALL", 0) +
        stats["rejection_reasons"].get("NEGATIVE_EV", 0) +
        stats["rejection_reasons"].get("LOW_LIQUIDITY", 0)
    )
    zombie_saved = (
        stats["rejection_reasons"].get("ZOMBIE_PRICE_CAP", 0) +
        stats["rejection_reasons"].get("ZOMBIE_BID_FLOOR", 0) +
        stats["rejection_reasons"].get("ZOMBIE_SPREAD", 0)
    )
    lines.append(f"   Noise / low-EV rejections  : {noise_saved:,}")
    lines.append(f"   Zombie / spread rejections : {zombie_saved:,}")
    lines.append(f"   Total protected rejections : {noise_saved + zombie_saved:,}")
    lines.append("")

    lines.append("── 7. COMPARISON VS PREVIOUS CONFIG ──────────────────────────────────")
    lines.append(f"   Assumed prior acceptance rate : {old_ratio_pct:.1f}%")
    lines.append(f"   Current acceptance rate       : {ratio_pct:.2f}%")
    freq_label = f"+{freq_change:.1f}%" if freq_change >= 0 else f"{freq_change:.1f}%"
    lines.append(f"   Trade frequency change        : {freq_label}")

    rr_values = [s["rr"] for s in stats["accepted_signals"]]
    avg_rr = sum(rr_values) / len(rr_values) if rr_values else 0
    lines.append(f"   Avg RR of accepted signals    : {avg_rr:.3f}  (target ≥ 0.50)")
    lines.append("")

    lines.append("── 8. CALIBRATION STATUS ─────────────────────────────────────────────")
    lines.append(f"   {status}")
    lines.append("")

    lines.append("── 9. VIABILITY ASSESSMENT ───────────────────────────────────────────")
    lines.append(f"   {viability}")
    lines.append("")

    if stats["hourly_snapshots"]:
        lines.append("── 10. HOURLY SNAPSHOTS ──────────────────────────────────────────────")
        for i, h in enumerate(stats["hourly_snapshots"][-24:], 1):
            lines.append(
                f"   [{i:>2}] gen={h['generated']:>4}  acc={h['accepted']:>3}  "
                f"trades={h['trades']:>2}  ratio={h['ratio']:.1f}%  "
                f"rejections=[{h['rejections'][:60]}]"
            )
        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    full_mode = "--full" in sys.argv
    log_files = collect_log_files(full=full_mode)

    print(f"\n📂 Parsing {len(log_files)} log file(s)...\n")
    stats  = parse_logs(log_files)
    report = generate_report(stats, log_files)

    print(report)

    # Save to disk
    ts_str  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORT_DIR / f"calibration_report_{ts_str}.txt"
    out_path.write_text(report)
    print(f"\n📄 Report saved to: {out_path}")
