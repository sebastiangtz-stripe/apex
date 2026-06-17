#!/usr/bin/env python3
"""
Dual-write health rollup over a window of recent runs.

Parses data/runbooks/dual-write-log.md (append-only, one entry per
apply-proposals.py run) and surfaces:

  - Run count + clean-run rate
  - Aggregate counts (auto-closed, created, dedup-skipped, inline gaps logged, needs_human_review)
  - Verification drift sum
  - Most recent review-queue items (medium/low confidence auto-closes that need human attention)
  - Stale items: review-queue entries older than 3 days

Plus an auto-startup one-liner format for CLAUDE.md to surface in the
morning briefing without listing every detail.

Usage:
  python3 scripts/dual-write-health.py                 # default 7-day window
  python3 scripts/dual-write-health.py --window 30d    # 30-day rollup
  python3 scripts/dual-write-health.py --oneliner      # single-line summary for auto-startup
  python3 scripts/dual-write-health.py --json          # machine-readable
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
LOG = WORKSPACE_ROOT / "data" / "runbooks" / "dual-write-log.md"


HEADER_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+—\s+(\d+)\s+file\(s\)\s*$")
COUNT_RE = re.compile(r"^- ([A-Za-z\- ]+):\s+(\d+)(?:\s+item\(s\))?\s*$")
QUEUE_HEADER_RE = re.compile(r"^- Review queue:\s*$")
QUEUE_ITEM_RE = re.compile(r"^\s+-\s+(.+?)\s*$")
DRIFT_RE = re.compile(r"^- Verification drift:\s+(\d+)\s+item\(s\)\s*$")


def parse_window(window_arg):
    """Accept N, Nd, Nh formats. Default unit is days."""
    if not window_arg:
        return timedelta(days=7)
    s = str(window_arg).strip().lower()
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    return timedelta(days=int(s))


def parse_log():
    """Parse dual-write-log.md into a list of run records."""
    if not LOG.exists():
        return []
    runs = []
    current = None
    in_queue = False
    for line in LOG.read_text().splitlines():
        m = HEADER_RE.match(line)
        if m:
            if current:
                runs.append(current)
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            current = {
                "ts": ts,
                "files": int(m.group(2)),
                "auto_closed": 0,
                "created": 0,
                "dedup_skipped": 0,
                "inline_gaps_logged": 0,
                "needs_human_review": 0,
                "verification_drift": 0,
                "queue": [],
            }
            in_queue = False
            continue
        if current is None:
            continue
        m = QUEUE_HEADER_RE.match(line)
        if m:
            in_queue = True
            continue
        m = COUNT_RE.match(line)
        if m:
            in_queue = False
            key = m.group(1).strip().lower().replace(" ", "_").replace("-", "_")
            try:
                current[key] = int(m.group(2))
            except (KeyError, ValueError):
                pass
            continue
        m = DRIFT_RE.match(line)
        if m:
            current["verification_drift"] = int(m.group(1))
            continue
        if in_queue:
            m = QUEUE_ITEM_RE.match(line)
            if m:
                current["queue"].append({"raw": m.group(1), "ts": current["ts"].isoformat()})
                continue
            in_queue = False
    if current:
        runs.append(current)
    return runs


def aggregate(runs):
    totals = {
        "runs": len(runs),
        "auto_closed": 0,
        "created": 0,
        "dedup_skipped": 0,
        "inline_gaps_logged": 0,
        "needs_human_review": 0,
        "verification_drift": 0,
        "clean_runs": 0,
    }
    open_queue = []
    for r in runs:
        for k in (
            "auto_closed",
            "created",
            "dedup_skipped",
            "inline_gaps_logged",
            "needs_human_review",
            "verification_drift",
        ):
            totals[k] += r.get(k, 0)
        clean = r.get("verification_drift", 0) == 0 and r.get("needs_human_review", 0) == 0
        if clean:
            totals["clean_runs"] += 1
        for q in r.get("queue", []):
            open_queue.append(q)
    return totals, open_queue


def render_oneliner(totals, open_queue, window_days):
    if totals["runs"] == 0:
        return f"Dual-write health: no runs in last {window_days}d."
    parts = [f"Dual-write health: {totals['runs']} runs in last {window_days}d"]
    parts.append(f"{totals['clean_runs']} clean")
    if totals["needs_human_review"]:
        parts.append(f"{totals['needs_human_review']} pending review")
    if totals["verification_drift"]:
        parts.append(f"{totals['verification_drift']} drift")
    parts.append(
        f"applied {totals['auto_closed']}c+{totals['created']}n"
    )
    return ". ".join([parts[0] + " (" + ", ".join(parts[1:]) + ")"])


def render_full(totals, open_queue, window_days):
    print(f"Dual-write health — last {window_days} days")
    print("=" * 50)
    print(f"  Runs:               {totals['runs']}")
    print(
        f"  Clean runs:         {totals['clean_runs']} "
        f"({(totals['clean_runs']/totals['runs']*100):.0f}%)"
        if totals["runs"]
        else "  Clean runs:         0"
    )
    print(f"  Auto-closed:        {totals['auto_closed']}")
    print(f"  Created:            {totals['created']}")
    print(f"  Dedup-skipped:      {totals['dedup_skipped']}")
    print(f"  Inline gaps logged: {totals['inline_gaps_logged']}")
    print(f"  Needs human review: {totals['needs_human_review']}")
    print(f"  Verification drift: {totals['verification_drift']}")
    if open_queue:
        print(f"\n  Open review queue ({len(open_queue)} item(s)):")
        for q in open_queue[-15:]:  # last 15
            print(f"    - {q['raw'][:120]}")
    if totals["verification_drift"] > 0:
        print(
            f"\n  WARNING: {totals['verification_drift']} drift item(s). "
            "Run scripts/verify-proposals.py --recent 7 for details."
        )


def main():
    parser = argparse.ArgumentParser(description="Dual-write pipeline health rollup.")
    parser.add_argument("--window", default="7d", help="Window like 7d, 24h, 30d (default 7d)")
    parser.add_argument("--oneliner", action="store_true", help="Single-line summary for auto-startup")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON")
    args = parser.parse_args()

    window = parse_window(args.window)
    cutoff = datetime.now(timezone.utc) - window
    runs = [r for r in parse_log() if r["ts"] >= cutoff]
    totals, open_queue = aggregate(runs)

    window_days = int(window.total_seconds() // 86400) or 1

    if args.json:
        print(json.dumps({
            "window_days": window_days,
            "totals": totals,
            "open_queue": open_queue,
        }, indent=2))
        return
    if args.oneliner:
        print(render_oneliner(totals, open_queue, window_days))
        return
    render_full(totals, open_queue, window_days)


if __name__ == "__main__":
    main()
