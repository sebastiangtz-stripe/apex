#!/usr/bin/env python3
"""
Canonical "last activity date" + "days silent" helper.

Parses each merchant's projects/active/<slug>/timeline.md, scanning H2 headers
for dates in either `## YYYY-MM-DD` or `## [YYYY-MM-DD]` form. Both formats
appear in real timelines. Uses max(parsed_dates) — robust against out-of-order
entries from misbehaved scanners.

Replaces ad-hoc inline parsers (e.g. `re.findall + dates[-1]`) which silently
inverted the silence calc because timelines are newest-at-top.

Used by:
- CLAUDE.md Auto-Startup Step 3 (Agent B — silence scan)
- .cursor/agents/quick-context.md (per-merchant status synthesis)
- Anywhere else that needs "how long since the merchant heard from us / we
  heard from them".

Usage:
  python3 scripts/last-activity.py                          # all active, table
  python3 scripts/last-activity.py --slug <merchant-slug>   # one merchant
  python3 scripts/last-activity.py --threshold-days 7       # only silent >= N
  python3 scripts/last-activity.py --include-scan-state     # also factor scan-state.json last_*_scan
  python3 scripts/last-activity.py --json                   # machine-readable
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"

H2_DATE = re.compile(r"^##\s+\[?(\d{4}-\d{2}-\d{2})\]?\b")


def parse_timeline_last_date(timeline_path: Path) -> Optional[date]:
    """Return the most recent date found in H2 headers of timeline.md, or None."""
    if not timeline_path.exists():
        return None
    dates: list[date] = []
    for line in timeline_path.read_text(errors="replace").splitlines():
        m = H2_DATE.match(line)
        if not m:
            continue
        try:
            dates.append(date.fromisoformat(m.group(1)))
        except ValueError:
            continue
    return max(dates) if dates else None


def parse_scan_state_last_date(scan_state_path: Path) -> Optional[date]:
    """Return max(last_email_scan, last_slack_scan) as a date, or None."""
    if not scan_state_path.exists():
        return None
    try:
        data = json.loads(scan_state_path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    dates: list[date] = []
    for key in ("last_email_scan", "last_slack_scan"):
        val = data.get(key)
        if not val:
            continue
        try:
            dates.append(datetime.fromisoformat(val.replace("Z", "+00:00")).date())
        except (ValueError, AttributeError):
            continue
    return max(dates) if dates else None


def compute(slugs: list[str], today: date, include_scan_state: bool) -> list[dict]:
    rows: list[dict] = []
    for slug in slugs:
        proj_dir = ACTIVE_DIR / slug
        timeline = proj_dir / "timeline.md"
        scan_state = proj_dir / "scan-state.json"

        timeline_date = parse_timeline_last_date(timeline)
        scan_state_date = parse_scan_state_last_date(scan_state) if include_scan_state else None

        candidates = [d for d in (timeline_date, scan_state_date) if d is not None]
        last = max(candidates) if candidates else None
        days_silent = (today - last).days if last else None

        rows.append({
            "slug": slug,
            "last_activity": last.isoformat() if last else None,
            "days_silent": days_silent,
            "source": (
                "timeline+scan-state" if include_scan_state and scan_state_date and (
                    scan_state_date == last and (timeline_date is None or scan_state_date > timeline_date)
                )
                else "timeline" if timeline_date else
                "scan-state" if scan_state_date else
                "none"
            ),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="restrict to one merchant")
    parser.add_argument("--threshold-days", type=int, default=0,
                        help="only show projects silent >= N days (default 0 = all)")
    parser.add_argument("--include-scan-state", action="store_true",
                        help="factor scan-state.json last_email_scan/last_slack_scan into max()")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not ACTIVE_DIR.exists():
        print(f"FATAL: {ACTIVE_DIR} missing", file=sys.stderr)
        sys.exit(2)

    if args.slug:
        slugs = [args.slug]
    else:
        slugs = sorted(p.name for p in ACTIVE_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))

    today = datetime.now(timezone.utc).date()
    rows = compute(slugs, today, args.include_scan_state)

    if args.threshold_days > 0:
        rows = [r for r in rows if r["days_silent"] is not None and r["days_silent"] >= args.threshold_days]

    rows.sort(key=lambda r: (-(r["days_silent"] if r["days_silent"] is not None else -1), r["slug"]))

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print("No projects matched.")
        return

    slug_w = max(len(r["slug"]) for r in rows)
    print(f"{'slug':<{slug_w}}  {'last_activity':<14}  {'silent':>6}  source")
    print(f"{'-'*slug_w}  {'-'*14}  {'-'*6}  ------")
    for r in rows:
        la = r["last_activity"] or "—"
        ds = f"{r['days_silent']}d" if r["days_silent"] is not None else "—"
        print(f"{r['slug']:<{slug_w}}  {la:<14}  {ds:>6}  {r['source']}")


if __name__ == "__main__":
    main()
