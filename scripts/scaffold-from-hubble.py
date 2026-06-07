#!/usr/bin/env python3
"""
Scaffold project folders from Hubble snapshot new-project rows.

Reads data/hubble-snapshot.json and creates project folders with clean slugs.
Only creates folders for projects that don't already have a local directory.

Slug derivation:
  1. Take project_name from Hubble (e.g., "Gym Force-[Connect; Billing; Payments]-US-$5M")
  2. Strip everything after the first occurrence of -[, [, or # (bracket/delimiter noise)
  3. Also strip trailing segments that look like geography or ONR (e.g., -US, -$5M, -AMER)
  4. Kebab-case the remainder and lowercase

Usage:
  python3 scripts/scaffold-from-hubble.py                  # preview what would be created
  python3 scripts/scaffold-from-hubble.py --apply          # create the folders
  python3 scripts/scaffold-from-hubble.py --slug-only      # just print slug mappings
  python3 scripts/scaffold-from-hubble.py --json           # machine-readable output
"""

import argparse
import json
import re
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = WORKSPACE_ROOT / "data" / "hubble-snapshot.json"
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"
TEMPLATE_DIR = WORKSPACE_ROOT / "templates"

NOISE_PATTERN = re.compile(r"[\-\s]*[\[\#\(].*$")
TRAILING_NOISE = re.compile(
    r"[\-\s]+(US|AMER|EMEA|APAC|LATAM|"
    r"\$\d+[KkMm]?|"
    r"\d+[KkMm])"
    r"$",
    re.IGNORECASE,
)
NON_SLUG_CHARS = re.compile(r"[^a-z0-9\-]")
MULTI_DASH = re.compile(r"-{2,}")
GENERIC_DOMAINS = {"gmail.com", "icloud.com", "hotmail.com", "outlook.com", "yahoo.com", "me.com", "live.com", "aol.com"}


def clean_slug(project_name: str) -> str:
    """Derive a clean kebab-case slug from a Hubble project_name."""
    name = project_name.strip()
    name = NOISE_PATTERN.sub("", name)
    name = TRAILING_NOISE.sub("", name)
    name = name.strip(" -")
    name = name.lower()
    name = name.replace(" ", "-")
    name = name.replace("_", "-")
    name = NON_SLUG_CHARS.sub("", name)
    name = MULTI_DASH.sub("-", name)
    name = name.strip("-")
    return name or "unknown"


def load_snapshot():
    if not SNAPSHOT_PATH.exists():
        print("ERROR: data/hubble-snapshot.json not found. Run hubble-analyst first.", file=sys.stderr)
        sys.exit(1)
    return json.loads(SNAPSHOT_PATH.read_text())


def get_existing_slugs():
    if not ACTIVE_DIR.exists():
        return set()
    return {p.name for p in ACTIVE_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")}


def main():
    parser = argparse.ArgumentParser(description="Scaffold project folders from Hubble snapshot")
    parser.add_argument("--apply", action="store_true", help="Actually create folders (default is preview)")
    parser.add_argument("--slug-only", action="store_true", help="Just print name → slug mappings")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    snapshot = load_snapshot()
    projects = snapshot.get("projects", [])
    existing = get_existing_slugs()

    results = []
    for proj in projects:
        name = proj.get("project_name", "")
        if not name:
            continue
        slug = clean_slug(name)
        already_exists = slug in existing
        results.append({
            "project_name": name,
            "slug": slug,
            "project_id": proj.get("project_id"),
            "primary_contact_email": proj.get("primary_contact_email"),
            "already_exists": already_exists,
        })

    new_projects = [r for r in results if not r["already_exists"]]

    if args.slug_only:
        for r in results:
            marker = " (exists)" if r["already_exists"] else " (NEW)"
            print(f"  {r['project_name']:<50} → {r['slug']}{marker}")
        return

    if args.json:
        print(json.dumps({"total": len(results), "new": new_projects, "existing_count": len(results) - len(new_projects)}, indent=2))
        return

    if not new_projects:
        print("All Hubble projects already have local folders. Nothing to scaffold.")
        return

    print(f"Found {len(new_projects)} new project(s) to scaffold:\n")
    for r in new_projects:
        print(f"  {r['project_name']:<50} → {r['slug']}/")

    if not args.apply:
        print(f"\nDry run. Pass --apply to create {len(new_projects)} folder(s).")
        return

    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    created = []
    for r in new_projects:
        slug = r["slug"]
        proj_dir = ACTIVE_DIR / slug
        proj_dir.mkdir(exist_ok=True)
        (proj_dir / "raw").mkdir(exist_ok=True)
        (proj_dir / "issues").mkdir(exist_ok=True)
        (proj_dir / "drafts").mkdir(exist_ok=True)

        # Minimal PROJECT.md
        project_md = proj_dir / "PROJECT.md"
        if not project_md.exists():
            merchant_display = r['project_name'].split('-')[0].split('[')[0].strip()
            contact_email = r.get("primary_contact_email", "")
            email_query = "TBD"
            if contact_email:
                domain = contact_email.split("@")[-1] if "@" in contact_email else ""
                if domain and domain not in GENERIC_DOMAINS:
                    email_query = f"from:{domain} OR to:{domain}"
                else:
                    parts = []
                    if merchant_display:
                        parts.append(f'from:"{merchant_display}"')
                    parts.append(f"from:{contact_email} OR to:{contact_email}")
                    email_query = " OR ".join(parts)

            contacts_section = "TBD"
            if contact_email:
                contacts_section = f"- {contact_email} — merchant contact (from Hubble)"

            project_md.write_text(
                f"# {merchant_display}\n\n"
                f"## Overview\n"
                f"- **Status**: Discovery\n"
                f"- **Priority**: Medium\n"
                f"- **Hubble Project ID**: {r['project_id']}\n"
                f"- **Account ID(s)**: TBD\n\n"
                f"## Key Contacts\n\n"
                f"{contacts_section}\n\n"
                f"## Communication\n\n"
                f"- **Scan source**: managed\n"
                f"- **Email search**: {email_query}\n"
                f"- **Slack channels**: TBD\n\n"
                f"## External Links\n\n"
                f"- Handover: TBD\n"
                f"- Manifest: TBD\n"
                f"- Salesforce: TBD\n"
                f"- Kantata Workspace: TBD\n\n"
                f"## Product Activation\n\nTBD\n"
            )

        # Empty action-items.md
        ai_path = proj_dir / "action-items.md"
        if not ai_path.exists():
            ai_path.write_text(f"# Action Items — {slug}\n\n## Open\n\n## Completed\n")

        # Empty timeline.md
        tl_path = proj_dir / "timeline.md"
        if not tl_path.exists():
            tl_path.write_text(f"# Timeline — {slug}\n\n")

        # scan-state.json
        ss_path = proj_dir / "scan-state.json"
        if not ss_path.exists():
            ss_path.write_text(json.dumps({
                "last_email_scan": None,
                "last_slack_scan": None,
                "logged_email_ids": [],
                "logged_slack_thread_ids": [],
                "slack_thread_state": {}
            }, indent=2) + "\n")

        created.append(slug)

    print(f"\nCreated {len(created)} project folder(s):")
    for s in created:
        print(f"  projects/active/{s}/")
    print("\nNext steps (order matters):")
    print("  1. python3 scripts/hubble-reconcile.py --backfill   # populate contacts, links, Email search")
    print("  2. Search handover channel per merchant              # extract contacts from Slack threads")
    print("  3. python3 scripts/sync-to-asana.py                  # push to Asana AFTER data is populated")


if __name__ == "__main__":
    main()
