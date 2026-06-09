#!/usr/bin/env python3
"""
Fetch Case Studio emails from Hubble query results and split into per-merchant staging files.

Reads pre-fetched Hubble results from data/cs-raw-results.json (placed there by
the LLM after executing the SQL via run_hubble_query MCP), routes messages to
merchant slugs via data/case-merchant-map.json, normalizes fields, and writes
staging files for ingest-comms.py.

Usage:
  python3 scripts/fetch-cs.py                         # process incremental results
  python3 scripts/fetch-cs.py --bootstrap 500VN...    # process bootstrap results for one case
  python3 scripts/fetch-cs.py --results-file path.json  # custom results file
  python3 scripts/fetch-cs.py --dry-run               # preview without writing
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import WORKSPACE_ROOT, load_env

STAGING_DIR = WORKSPACE_ROOT / "data" / "staging"
RESULTS_FILE = WORKSPACE_ROOT / "data" / "cs-raw-results.json"
CASE_MAP_FILE = WORKSPACE_ROOT / "data" / "case-merchant-map.json"
CS_STATE_FILE = WORKSPACE_ROOT / "data" / "cs-scan-state.json"

ENV = load_env()


# ── Helpers ───────────────────────────────────────────────────────────────────

def epoch_to_iso(epoch_val):
    """Convert Unix epoch (int or float) to ISO 8601 UTC string."""
    if epoch_val is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(epoch_val), tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError, OSError):
        return None


def strip_html(html):
    """Strip HTML tags and normalize whitespace. Basic fallback for html_body."""
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</?p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_cc(cc_raw):
    """Convert semicolon-separated CC to comma-separated."""
    if not cc_raw:
        return ""
    parts = [addr.strip() for addr in cc_raw.split(";") if addr.strip()]
    return ", ".join(parts)


def load_case_map():
    """Load the case-to-merchant mapping file."""
    if CASE_MAP_FILE.exists():
        return json.loads(CASE_MAP_FILE.read_text())
    return {"version": 1, "mappings": [], "unmapped_cases": []}


def save_case_map(data):
    """Write case-merchant-map.json."""
    CASE_MAP_FILE.write_text(json.dumps(data, indent=2) + "\n")


def load_cs_state():
    """Load CS scan state."""
    if CS_STATE_FILE.exists():
        return json.loads(CS_STATE_FILE.read_text())
    return {"last_scan": None, "last_query_row_count": 0, "unmapped_cases": []}


def save_cs_state(state):
    """Write CS scan state."""
    CS_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def build_case_to_slug_index(case_map):
    """Build a case_id → slug lookup dict from the mapping file."""
    return {m["case_id"]: m["slug"] for m in case_map.get("mappings", [])}


# ── Main logic ────────────────────────────────────────────────────────────────

def process_results(results_data, case_to_slug, dry_run=False):
    """Process Hubble query results into per-merchant staging files.

    Returns (stats_dict, unmapped_cases_list).
    """
    rows = results_data.get("results", [])
    if not rows:
        return {"total_messages": 0, "staged": 0, "unmapped": 0, "slugs": []}, []

    # Group by slug
    by_slug = {}
    unmapped = []

    for row in rows:
        case_id = row.get("case_id", "")
        slug = case_to_slug.get(case_id)

        if not slug:
            unmapped.append({
                "case_id": case_id,
                "case_subject": row.get("case_subject", ""),
                "from_address": row.get("from_address", ""),
                "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            continue

        if slug not in by_slug:
            by_slug[slug] = []

        body = row.get("text_body") or strip_html(row.get("html_body")) or ""

        by_slug[slug].append({
            "sfdc_id": row.get("message_id", ""),
            "case_id": case_id,
            "from": row.get("from_address", ""),
            "to": row.get("to_address", ""),
            "cc": normalize_cc(row.get("cc_address")),
            "date": epoch_to_iso(row.get("message_date")),
            "subject": row.get("subject", ""),
            "body": body,
            "is_incoming": row.get("is_incoming", True),
            "has_attachment": row.get("has_attachment", False),
            "case_subject": row.get("case_subject", ""),
        })

    # Write staging files
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slugs_written = []

    for slug, emails in by_slug.items():
        staging_file = STAGING_DIR / f"{slug}-{today}-cs.json"
        staging_data = {
            "slug": slug,
            "source": "case_studio",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "emails": emails,
            "slack_threads": [],
        }
        if not dry_run:
            STAGING_DIR.mkdir(parents=True, exist_ok=True)
            staging_file.write_text(json.dumps(staging_data, indent=2) + "\n")
        slugs_written.append(slug)
        print(f"  → {slug}: {len(emails)} messages → {staging_file.name}")

    stats = {
        "total_messages": len(rows),
        "staged": sum(len(e) for e in by_slug.values()),
        "unmapped": len(unmapped),
        "slugs": slugs_written,
    }
    return stats, unmapped


def main():
    parser = argparse.ArgumentParser(description="Fetch Case Studio emails from Hubble results.")
    parser.add_argument("--results-file", type=Path, default=RESULTS_FILE,
                        help="Path to Hubble query results JSON")
    parser.add_argument("--bootstrap", metavar="CASE_ID",
                        help="Process bootstrap results for a single case")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing staging files")
    args = parser.parse_args()

    if args.dry_run:
        print("[DRY RUN] No files will be modified.\n")

    # Load results
    if not args.results_file.exists():
        print(f"Error: Results file not found: {args.results_file}")
        print("Run the Hubble query via MCP and save results to this path first.")
        sys.exit(1)

    results_data = json.loads(args.results_file.read_text())
    if results_data.get("query_status") != "success":
        print(f"Error: Query did not succeed. Status: {results_data.get('query_status')}")
        if results_data.get("query_note"):
            print(f"  Note: {results_data['query_note']}")
        sys.exit(1)

    row_count = results_data.get("row_count", len(results_data.get("results", [])))
    print(f"Loaded {row_count} messages from {args.results_file.name}")

    # Load case mapping
    case_map = load_case_map()
    case_to_slug = build_case_to_slug_index(case_map)
    print(f"Case map: {len(case_to_slug)} mapped cases")

    # Process
    stats, unmapped = process_results(results_data, case_to_slug, args.dry_run)

    # Update state
    if not args.dry_run:
        cs_state = load_cs_state()
        cs_state["last_scan"] = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
        cs_state["last_query_row_count"] = stats["total_messages"]

        # Merge unmapped (deduplicate by case_id)
        existing_unmapped_ids = {u["case_id"] for u in cs_state.get("unmapped_cases", [])}
        for u in unmapped:
            if u["case_id"] not in existing_unmapped_ids:
                cs_state.setdefault("unmapped_cases", []).append(u)
                existing_unmapped_ids.add(u["case_id"])
        save_cs_state(cs_state)

    # Report
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Done:")
    print(f"  Messages processed: {stats['total_messages']}")
    print(f"  Staged: {stats['staged']} across {len(stats['slugs'])} merchants")
    print(f"  Unmapped: {stats['unmapped']} messages (from cases not in case-merchant-map.json)")

    if unmapped:
        seen_cases = set()
        print(f"\n  Unmapped cases (run `manage-case-map.py --add <case_id> <slug>`):")
        for u in unmapped:
            if u["case_id"] not in seen_cases:
                print(f"    {u['case_id']} — {u['case_subject']}")
                seen_cases.add(u["case_id"])

    # Output JSON report to stdout for programmatic consumption
    report = {"stats": stats, "unmapped_case_ids": list({u["case_id"] for u in unmapped})}
    print(f"\n{json.dumps(report)}")


if __name__ == "__main__":
    main()
