#!/usr/bin/env python3
"""
Aggregate per-session Stats blocks into a weekly metrics rollup.

Reads sessions/*.md, finds each `## Stats` section, parses leading numbers from
each `- <Label>: <text>` line, attributes to the session's date, and aggregates
across a configurable window.

Tolerant parser: many Stats lines use prose ("1 created (`<slug>`), ~10 updated").
This script extracts the *first* number found on each line and totals it. That
under-counts in some cases but is auditable and never hallucinates.

Usage:
  python3 scripts/weekly-metrics.py                       # last 7 days
  python3 scripts/weekly-metrics.py --days 30
  python3 scripts/weekly-metrics.py --since 2026-04-01
  python3 scripts/weekly-metrics.py --append-jsonl        # also write to data/weekly-metrics.jsonl
  python3 scripts/weekly-metrics.py --json                # one-shot machine-readable
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = WORKSPACE_ROOT / "sessions"
JSONL_PATH = WORKSPACE_ROOT / "data" / "weekly-metrics.jsonl"

DATE_FROM_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")
SESSION_HEADER_RE = re.compile(r"^#\s+Session\s+—\s+(\d{4}-\d{2}-\d{2})", re.MULTILINE)
STATS_BLOCK_RE = re.compile(r"^##\s+Stats\s*$\n(.*?)(?=\n##\s+|\Z)", re.MULTILINE | re.DOTALL)
STAT_LINE_RE = re.compile(r"^\s*-\s*\*?\*?([^:*]+?)\*?\*?\s*:\s*(.+?)\s*$")
NUMBER_RE = re.compile(r"~?(\d+)")

# Labels we care about. Maps free-form Stats labels (lowercased, normalized) to canonical keys.
LABEL_MAP = {
    "projects created": "projects_created",
    "projects updated": "projects_updated",
    "projects created/updated": "projects_touched",
    "emails scanned": "emails_scanned",
    "emails scanned/logged": "emails_scanned",
    "emails logged": "emails_logged",
    "emails logged retroactively": "emails_logged",
    "raw saved": "raw_entries",
    "local action items created/completed": "items_created_completed",
    "local action items created": "items_created",
    "local action items completed": "items_completed",
    "asana subtasks created": "asana_created",
    "asana subtasks completed": "asana_completed",
    "asana subtasks created/completed": "asana_created_completed",
    "asana subtasks updated": "asana_updated",
    "asana subtasks updated (due_on)": "asana_updated_due",
    "asana custom fields updated": "asana_custom_field_updates",
    "asana comments added": "asana_comments",
    "issues opened/resolved": "issues_opened_resolved",
    "issues opened": "issues_opened",
    "issues files opened": "issues_opened",
    "issues resolved": "issues_resolved",
    "drafts created": "drafts_created",
    "drafts revised": "drafts_revised",
    "subagent invocations": "subagent_invocations",
    "slack threads logged (raw + timeline + scan-state)": "slack_threads_logged",
    "slack messages posted": "slack_messages_sent",
    "slack dms sent": "slack_dms_sent",
    "script patches": "script_patches",
    "lints": "lints_introduced",
    "hallucinations owned": "hallucinations_owned",
}


def normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def first_number(text: str) -> int:
    m = NUMBER_RE.search(text)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def extract_session_dates_and_stats(path: Path):
    """Returns list of (date, stats_dict) tuples — multi-session days produce multiple."""
    text = path.read_text(errors="replace")
    file_date_match = DATE_FROM_NAME.match(path.name)
    if not file_date_match:
        return []
    file_date = date(int(file_date_match.group(1)), int(file_date_match.group(2)),
                     int(file_date_match.group(3)))

    # Each `# Session — YYYY-MM-DD` header marks a sub-session; same-day files have multiple
    headers = list(SESSION_HEADER_RE.finditer(text))
    stats_blocks = list(STATS_BLOCK_RE.finditer(text))

    results = []
    if not headers:
        # Fall back: just use the file_date and the first Stats block (if any)
        for sb in stats_blocks:
            results.append((file_date, parse_stats_body(sb.group(1))))
        return results

    # Pair each Stats block with the closest preceding header
    header_positions = [(h.start(), h.group(1)) for h in headers]
    for sb in stats_blocks:
        sb_start = sb.start()
        # Find the latest header before this Stats block
        sess_date = file_date
        for hp, hd in header_positions:
            if hp < sb_start:
                try:
                    sess_date = datetime.strptime(hd, "%Y-%m-%d").date()
                except ValueError:
                    pass
        results.append((sess_date, parse_stats_body(sb.group(1))))
    return results


def parse_stats_body(body: str) -> dict:
    out: dict = defaultdict(int)
    for line in body.splitlines():
        m = STAT_LINE_RE.match(line)
        if not m:
            continue
        label = normalize_label(m.group(1))
        val_text = m.group(2).strip()
        # Map to canonical
        key = LABEL_MAP.get(label)
        if not key:
            # Try prefix match (handles "asana subtasks created" within "asana subtasks created (...)")
            for k, v in LABEL_MAP.items():
                if label.startswith(k):
                    key = v
                    break
        if not key:
            continue

        # Special handling for "X/Y" combined labels
        if "_created_completed" in key or "_opened_resolved" in key:
            # Find first two numbers, attribute to base + base+_completed/_resolved
            nums = NUMBER_RE.findall(val_text)
            base, second_suffix = ("_created", "_completed") if "_created_completed" in key else ("_opened", "_resolved")
            stem = key.rsplit("_", 2)[0]  # e.g. "items"
            if nums:
                try:
                    out[stem + base] += int(nums[0])
                except ValueError:
                    pass
            if len(nums) >= 2:
                try:
                    out[stem + second_suffix] += int(nums[1])
                except ValueError:
                    pass
            continue

        out[key] += first_number(val_text)

    return dict(out)


def aggregate(window_start: date, window_end: date) -> dict:
    per_day: dict[date, dict] = defaultdict(lambda: defaultdict(int))
    session_count = 0
    for path in sorted(SESSIONS_DIR.glob("*.md")):
        if path.name == "INDEX.md":
            continue
        for sess_date, stats in extract_session_dates_and_stats(path):
            if sess_date < window_start or sess_date > window_end:
                continue
            session_count += 1
            for k, v in stats.items():
                per_day[sess_date][k] += v

    totals: dict = defaultdict(int)
    for d, stats in per_day.items():
        for k, v in stats.items():
            totals[k] += v

    return {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "days": (window_end - window_start).days + 1,
        "session_count": session_count,
        "totals": dict(sorted(totals.items())),
        "per_day": {d.isoformat(): dict(sorted(s.items())) for d, s in sorted(per_day.items())},
    }


def render(agg: dict) -> str:
    lines = [f"# Weekly Metrics — {agg['window_start']} → {agg['window_end']} ({agg['days']}d)",
             f"_(based on {agg['session_count']} session block(s))_", ""]
    lines.append("## Totals")
    if not agg["totals"]:
        lines.append("  (no parseable stats in window)")
    else:
        for k, v in agg["totals"].items():
            lines.append(f"  - {k}: {v}")
    lines.append("")
    lines.append("## Per-day")
    if not agg["per_day"]:
        lines.append("  (none)")
    for d, stats in agg["per_day"].items():
        lines.append(f"  ### {d}")
        for k, v in stats.items():
            lines.append(f"    - {k}: {v}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="rolling window from today")
    parser.add_argument("--since", help="explicit YYYY-MM-DD start (overrides --days)")
    parser.add_argument("--until", help="explicit YYYY-MM-DD end (default: today)")
    parser.add_argument("--append-jsonl", action="store_true",
                        help="also append the rollup to data/weekly-metrics.jsonl")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    end = (datetime.strptime(args.until, "%Y-%m-%d").date()
           if args.until else date.today())
    start = (datetime.strptime(args.since, "%Y-%m-%d").date()
             if args.since else end - timedelta(days=args.days - 1))

    agg = aggregate(start, end)

    if args.append_jsonl:
        JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with JSONL_PATH.open("a") as f:
            f.write(json.dumps({
                "computed_at": datetime.now().isoformat(timespec="seconds"),
                "window_start": agg["window_start"],
                "window_end": agg["window_end"],
                "totals": agg["totals"],
                "session_count": agg["session_count"],
            }) + "\n")
        print(f"Appended to {JSONL_PATH}")

    if args.json:
        print(json.dumps(agg, indent=2))
        return

    print(render(agg))


if __name__ == "__main__":
    main()
