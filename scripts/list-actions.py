#!/usr/bin/env python3
"""
Global rollup of action items across all active merchants. Read-only.

Replaces the "open every action-items.md and scroll" workflow with a single
command that filters by tag, due date, age, and status across ~30 merchants.

Usage examples:
  python3 scripts/list-actions.py                            # everything open, grouped by merchant
  python3 scripts/list-actions.py --tag waiting              # all #waiting items
  python3 scripts/list-actions.py --due-window 7             # due within 7 days
  python3 scripts/list-actions.py --overdue                  # past due only
  python3 scripts/list-actions.py --untouched-days 5         # source date >5 days ago
  python3 scripts/list-actions.py --tag research --untouched-days 5
  python3 scripts/list-actions.py --group-by tag             # group by tag instead of merchant
  python3 scripts/list-actions.py --json
  python3 scripts/list-actions.py --include-closed           # also include [x] items
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"

ITEM_RE = re.compile(r"^- \[([ xX])\]\s*(.+?)\s*$")
TAG_RE = re.compile(r"#([a-z][a-z0-9_-]*)", re.IGNORECASE)
DUE_RE = re.compile(r"Due:\s*(\d{4}-\d{2}-\d{2}|TBD|ASAP)", re.IGNORECASE)
COMPLEX_RE = re.compile(r"Complexity:\s*([HMLhml])", re.IGNORECASE)
SOURCE_RE = re.compile(r"Source:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
OWNER_RE = re.compile(r"Owner:\s*([^\—\-]+?)(?:\s*[—\-]|\s*$)", re.IGNORECASE)


def parse_action_items(slug: str, include_closed: bool):
    p = ACTIVE_DIR / slug / "action-items.md"
    if not p.exists():
        return []
    items = []
    in_open_section = True  # default: assume start is Open
    for line_no, raw in enumerate(p.read_text(errors="replace").splitlines(), start=1):
        line = raw.strip()
        if line.startswith("## "):
            in_open_section = "open" in line.lower()
            continue
        m = ITEM_RE.match(raw)
        if not m:
            continue
        completed = m.group(1) in ("x", "X")
        body = m.group(2)
        if completed and not include_closed:
            continue
        if not in_open_section and not include_closed:
            continue
        tags = ["#" + t.lower() for t in TAG_RE.findall(body)]
        due_match = DUE_RE.search(body)
        due_raw = due_match.group(1) if due_match else None
        try:
            due_date = datetime.strptime(due_raw, "%Y-%m-%d").date() if due_raw and due_raw not in ("TBD", "ASAP") else None
        except ValueError:
            due_date = None
        complex_match = COMPLEX_RE.search(body)
        complexity = complex_match.group(1).upper() if complex_match else None
        source_match = SOURCE_RE.search(body)
        try:
            source_date = datetime.strptime(source_match.group(1), "%Y-%m-%d").date() if source_match else None
        except ValueError:
            source_date = None
        owner_match = OWNER_RE.search(body)
        owner = owner_match.group(1).strip() if owner_match else None

        items.append({
            "slug": slug,
            "completed": completed,
            "raw": raw,
            "line_no": line_no,
            "tags": tags,
            "due_raw": due_raw,
            "due_date": due_date,
            "complexity": complexity,
            "source_date": source_date,
            "owner": owner,
        })
    return items


def filter_items(items, tag=None, due_window=None, overdue=False,
                 untouched_days=None, complexity=None):
    today = date.today()
    out = []
    for it in items:
        if tag:
            t = tag if tag.startswith("#") else f"#{tag.lower()}"
            if t not in it["tags"]:
                continue
        if overdue:
            if not it["due_date"] or it["due_date"] >= today:
                continue
        if due_window is not None:
            if not it["due_date"]:
                continue
            delta = (it["due_date"] - today).days
            if delta < 0 or delta > due_window:
                continue
        if untouched_days is not None:
            if not it["source_date"]:
                continue
            age = (today - it["source_date"]).days
            if age < untouched_days:
                continue
        if complexity:
            if (it["complexity"] or "").upper() != complexity.upper():
                continue
        out.append(it)
    return out


def render(items, group_by="merchant"):
    if not items:
        return "No matching items.\n"
    today = date.today()
    out_lines = []
    if group_by == "merchant":
        groups = defaultdict(list)
        for it in items:
            groups[it["slug"]].append(it)
        for slug in sorted(groups):
            out_lines.append(f"## {slug} ({len(groups[slug])})")
            for it in sorted(groups[slug], key=lambda x: (x["due_date"] is None, x["due_date"] or date.max)):
                out_lines.append(f"  {it['raw']}")
            out_lines.append("")
    elif group_by == "tag":
        groups = defaultdict(list)
        for it in items:
            for t in (it["tags"] or ["#untagged"]):
                groups[t].append(it)
        for tag in sorted(groups):
            out_lines.append(f"## {tag} ({len(groups[tag])})")
            for it in sorted(groups[tag], key=lambda x: (x["due_date"] is None, x["due_date"] or date.max, x["slug"])):
                due = it["due_raw"] or "—"
                out_lines.append(f"  - [{it['slug']}] Due {due} {'OVERDUE ' if it['due_date'] and it['due_date'] < today else ''}— {it['raw'].lstrip('- [ ] ').lstrip('- [x] ').lstrip('- [X] ')}")
            out_lines.append("")
    elif group_by == "due":
        groups = defaultdict(list)
        for it in items:
            if not it["due_date"]:
                bucket = "no_due"
            else:
                delta = (it["due_date"] - today).days
                if delta < 0:
                    bucket = "overdue"
                elif delta == 0:
                    bucket = "today"
                elif delta <= 7:
                    bucket = "this_week"
                elif delta <= 30:
                    bucket = "this_month"
                else:
                    bucket = "later"
            groups[bucket].append(it)
        order = ["overdue", "today", "this_week", "this_month", "later", "no_due"]
        for bucket in order:
            if bucket not in groups:
                continue
            out_lines.append(f"## {bucket.replace('_', ' ').title()} ({len(groups[bucket])})")
            for it in sorted(groups[bucket], key=lambda x: (x["due_date"] is None, x["due_date"] or date.max, x["slug"])):
                due = it["due_raw"] or "—"
                out_lines.append(f"  - [{it['slug']}] Due {due} — {it['raw'].lstrip('- [ ] ').lstrip('- [x] ').lstrip('- [X] ')}")
            out_lines.append("")
    return "\n".join(out_lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="restrict to one merchant")
    parser.add_argument("--tag", help="filter by tag (with or without leading #)")
    parser.add_argument("--due-window", type=int, help="only items due within N days from today")
    parser.add_argument("--overdue", action="store_true", help="only items past due")
    parser.add_argument("--untouched-days", type=int, help="only items whose Source date is N+ days old")
    parser.add_argument("--complexity", choices=["L", "M", "H", "l", "m", "h"], help="filter by complexity")
    parser.add_argument("--group-by", choices=["merchant", "tag", "due"], default="merchant")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-closed", action="store_true", help="include [x] items too")
    args = parser.parse_args()

    if not ACTIVE_DIR.exists():
        print(f"FATAL: {ACTIVE_DIR} missing", file=sys.stderr)
        sys.exit(2)

    slugs = ([args.slug] if args.slug else
             sorted(p.name for p in ACTIVE_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")))

    items = []
    for s in slugs:
        items.extend(parse_action_items(s, args.include_closed))

    items = filter_items(items, tag=args.tag, due_window=args.due_window,
                         overdue=args.overdue, untouched_days=args.untouched_days,
                         complexity=args.complexity)

    if args.json:
        print(json.dumps([{**it, "due_date": it["due_date"].isoformat() if it["due_date"] else None,
                           "source_date": it["source_date"].isoformat() if it["source_date"] else None}
                          for it in items], indent=2))
        sys.exit(0)

    print(f"# Action Items — {len(items)} matching")
    flt = []
    if args.tag: flt.append(f"tag={args.tag}")
    if args.overdue: flt.append("overdue")
    if args.due_window is not None: flt.append(f"due-window={args.due_window}d")
    if args.untouched_days is not None: flt.append(f"untouched>={args.untouched_days}d")
    if args.complexity: flt.append(f"complexity={args.complexity}")
    if args.include_closed: flt.append("incl_closed")
    if flt:
        print(f"_(filters: {', '.join(flt)})_")
    print()
    print(render(items, group_by=args.group_by))


if __name__ == "__main__":
    main()
