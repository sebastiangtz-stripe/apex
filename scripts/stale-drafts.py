#!/usr/bin/env python3
"""
Surface stale drafts: any projects/active/<slug>/drafts/*.md whose mtime is older
than --threshold-days AND has no `## Sent` section populated. Read-only.

Used inline by the scan-review skill (Phase 3 summary) and on demand.

Usage:
  python3 scripts/stale-drafts.py
  python3 scripts/stale-drafts.py --threshold-days 14   # default 7
  python3 scripts/stale-drafts.py --json
  python3 scripts/stale-drafts.py --slug example-merchant          # one merchant only
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"


SENT_SECTION_RE = re.compile(r"^##\s+Sent\b", re.MULTILINE)


def is_unsent(path: Path) -> bool:
    """A draft is 'unsent' when no `## Sent` section exists OR the section is empty."""
    text = path.read_text(errors="replace")
    m = SENT_SECTION_RE.search(text)
    if not m:
        return True
    # Section exists — check for content (anything beyond the header)
    body = text[m.end():]
    # Take until next ## or EOF
    next_h2 = re.search(r"^##\s+", body, re.MULTILINE)
    section_body = body[: next_h2.start()] if next_h2 else body
    return not bool(section_body.strip())


def scan_drafts(slugs: list[str], threshold_days: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    findings: list[dict] = []
    for slug in slugs:
        drafts_dir = ACTIVE_DIR / slug / "drafts"
        if not drafts_dir.is_dir():
            continue
        for draft in sorted(drafts_dir.glob("*.md")):
            mtime = datetime.fromtimestamp(draft.stat().st_mtime, tz=timezone.utc)
            age_days = (now - mtime).days
            if age_days < threshold_days:
                continue
            if not is_unsent(draft):
                continue
            findings.append({
                "slug": slug,
                "draft": str(draft.relative_to(WORKSPACE_ROOT)),
                "name": draft.name,
                "mtime": mtime.isoformat(timespec="seconds"),
                "age_days": age_days,
            })
    findings.sort(key=lambda f: -f["age_days"])
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold-days", type=int, default=7)
    parser.add_argument("--slug", help="restrict to one merchant")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not ACTIVE_DIR.exists():
        print(f"FATAL: {ACTIVE_DIR} missing", file=sys.stderr)
        sys.exit(2)

    slugs = ([args.slug] if args.slug else
             sorted(p.name for p in ACTIVE_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")))

    findings = scan_drafts(slugs, args.threshold_days)

    if args.json:
        print(json.dumps(findings, indent=2))
        sys.exit(1 if findings else 0)

    print(f"# Stale Drafts (>{args.threshold_days}d unsent)")
    print(f"_(generated {datetime.now().isoformat(timespec='seconds')})_\n")
    if not findings:
        print("None.")
        sys.exit(0)
    print(f"{len(findings)} stale draft(s):\n")
    by_slug: dict[str, list[dict]] = {}
    for f in findings:
        by_slug.setdefault(f["slug"], []).append(f)
    for slug, items in by_slug.items():
        print(f"## {slug} ({len(items)})")
        for it in items:
            print(f"  - {it['name']} — {it['age_days']}d old (mtime {it['mtime']})")
            print(f"      {it['draft']}")
        print()
    sys.exit(1)


if __name__ == "__main__":
    main()
