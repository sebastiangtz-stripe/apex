#!/usr/bin/env python3
"""
Manage the case-to-merchant mapping (data/case-merchant-map.json).

Maps SFDC Case IDs to local project slugs so fetch-cs.py knows where to
route Case Studio messages.

Usage:
  python3 scripts/manage-case-map.py --list
  python3 scripts/manage-case-map.py --add 500VN00000qmFyAYAU acme-corp
  python3 scripts/manage-case-map.py --remove 500VN00000qmFyAYAU
  python3 scripts/manage-case-map.py --show-unmapped
  python3 scripts/manage-case-map.py --bootstrap-map
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import WORKSPACE_ROOT, PROJECTS_DIR

CASE_MAP_FILE = WORKSPACE_ROOT / "data" / "case-merchant-map.json"
CS_STATE_FILE = WORKSPACE_ROOT / "data" / "cs-scan-state.json"


def load_map():
    if CASE_MAP_FILE.exists():
        return json.loads(CASE_MAP_FILE.read_text())
    return {"version": 1, "mappings": [], "unmapped_cases": []}


def save_map(data):
    CASE_MAP_FILE.write_text(json.dumps(data, indent=2) + "\n")


def cmd_list(data):
    mappings = data.get("mappings", [])
    if not mappings:
        print("No case mappings configured.")
        return
    print(f"{'Case ID':<24} {'Slug':<30} {'Added'}")
    print("-" * 74)
    for m in sorted(mappings, key=lambda x: x.get("slug", "")):
        print(f"{m['case_id']:<24} {m['slug']:<30} {m.get('added_at', '?')[:10]}")
    print(f"\nTotal: {len(mappings)} mappings")


def cmd_add(data, case_id, slug):
    existing_ids = {m["case_id"] for m in data.get("mappings", [])}
    if case_id in existing_ids:
        print(f"Case {case_id} already mapped. Use --remove first to remap.")
        return False

    # Verify slug exists
    slug_dir = PROJECTS_DIR / slug
    if not slug_dir.exists():
        print(f"Warning: projects/active/{slug}/ does not exist. Mapping anyway.")

    data.setdefault("mappings", []).append({
        "case_id": case_id,
        "slug": slug,
        "case_subject": "",
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
    })

    # Remove from unmapped if present
    data["unmapped_cases"] = [
        u for u in data.get("unmapped_cases", []) if u["case_id"] != case_id
    ]

    save_map(data)
    print(f"Added: {case_id} → {slug}")
    return True


def cmd_remove(data, case_id):
    before = len(data.get("mappings", []))
    data["mappings"] = [m for m in data.get("mappings", []) if m["case_id"] != case_id]
    after = len(data["mappings"])
    if before == after:
        print(f"Case {case_id} not found in mappings.")
        return False
    save_map(data)
    print(f"Removed: {case_id}")
    return True


def cmd_show_unmapped():
    # Read from cs-scan-state.json (populated by fetch-cs.py)
    if not CS_STATE_FILE.exists():
        print("No CS scan state found. Run fetch-cs.py first.")
        return
    state = json.loads(CS_STATE_FILE.read_text())
    unmapped = state.get("unmapped_cases", [])
    if not unmapped:
        print("No unmapped cases.")
        return
    print(f"{'Case ID':<24} {'Subject'}")
    print("-" * 70)
    for u in unmapped:
        subj = u.get("case_subject", "")[:45]
        print(f"{u['case_id']:<24} {subj}")
    print(f"\nTotal: {len(unmapped)} unmapped cases")


def cmd_bootstrap_map(data):
    """Auto-match unmapped cases to project slugs by fuzzy name matching."""
    # Load unmapped cases
    if not CS_STATE_FILE.exists():
        print("No CS scan state found. Run fetch-cs.py first to populate unmapped cases.")
        return

    state = json.loads(CS_STATE_FILE.read_text())
    unmapped = state.get("unmapped_cases", [])
    if not unmapped:
        print("No unmapped cases to match.")
        return

    # Load project names
    projects = {}
    if PROJECTS_DIR.exists():
        for slug_dir in sorted(PROJECTS_DIR.iterdir()):
            if not slug_dir.is_dir():
                continue
            project_md = slug_dir / "PROJECT.md"
            if project_md.exists():
                first_line = project_md.read_text().splitlines()[0]
                name = first_line.lstrip("# ").strip()
                tokens = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", name) if len(t) > 2}
                projects[slug_dir.name] = {"name": name, "tokens": tokens}

    if not projects:
        print("No projects found in projects/active/.")
        return

    existing_ids = {m["case_id"] for m in data.get("mappings", [])}
    proposals = []

    for u in unmapped:
        if u["case_id"] in existing_ids:
            continue
        case_subject = u.get("case_subject", "")
        case_tokens = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", case_subject) if len(t) > 2}

        best_slug = None
        best_score = 0
        for slug, info in projects.items():
            overlap = case_tokens & info["tokens"]
            if overlap:
                score = len(overlap) / max(len(info["tokens"]), 1)
                if score > best_score:
                    best_score = score
                    best_slug = slug

        if best_slug and best_score >= 0.3:
            proposals.append({
                "case_id": u["case_id"],
                "case_subject": case_subject,
                "proposed_slug": best_slug,
                "project_name": projects[best_slug]["name"],
                "confidence": round(best_score, 2),
            })

    if not proposals:
        print("No automatic matches found. Use --add <case_id> <slug> manually.")
        return

    print(f"Proposed mappings ({len(proposals)}):\n")
    for i, p in enumerate(proposals, 1):
        print(f"  {i}. {p['case_id']}")
        print(f"     Case: {p['case_subject']}")
        print(f"     → {p['proposed_slug']} ({p['project_name']}) [confidence: {p['confidence']}]")
        print()

    print("To apply all proposals, run with --bootstrap-map --apply")
    print("To apply selectively, use --add <case_id> <slug> for each.")


def main():
    parser = argparse.ArgumentParser(description="Manage case-to-merchant mapping.")
    parser.add_argument("--list", action="store_true", help="List all current mappings")
    parser.add_argument("--add", nargs=2, metavar=("CASE_ID", "SLUG"), help="Add a mapping")
    parser.add_argument("--remove", metavar="CASE_ID", help="Remove a mapping")
    parser.add_argument("--show-unmapped", action="store_true", help="Show unmapped cases from last fetch")
    parser.add_argument("--bootstrap-map", action="store_true", help="Auto-match unmapped cases to slugs")
    parser.add_argument("--apply", action="store_true", help="Apply bootstrap proposals (use with --bootstrap-map)")
    args = parser.parse_args()

    data = load_map()

    if args.list:
        cmd_list(data)
    elif args.add:
        cmd_add(data, args.add[0], args.add[1])
    elif args.remove:
        cmd_remove(data, args.remove)
    elif args.show_unmapped:
        cmd_show_unmapped()
    elif args.bootstrap_map:
        cmd_bootstrap_map(data)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
