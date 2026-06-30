#!/usr/bin/env python3
from __future__ import annotations
"""
Prepare a match manifest for batch handover backfill.

Reads local project state + Hubble snapshot and outputs a JSON manifest of the
projects still missing a handover link, each with its cleaned project_name and
best-effort AE handle. Does NOT call Slack itself.

The caller reads the handover channel BY ID via read_slack_channel_history and
filters that history to these projects in code — by merchant name, AE handle, or
SFDC opportunity id. It does NOT use search_slack_messages or any `in:<name>`
filter: the human-readable channel name does not reliably resolve as a Slack
search target, which previously zeroed retrieval. Channel ID always resolves.

Per merchant the manifest provides two filter signals:
  - project_name : primary signal (merchant name appears in the thread).
  - ae_handle    : recovery signal (the AE who initiated the handover).

Usage:
  python3 scripts/handover-search.py              # all active projects
  python3 scripts/handover-search.py --slug foo   # single project
  python3 scripts/handover-search.py --force      # ignore dedup state

Exit codes:
  0  clean (manifest emitted to stdout)
  1  fatal config error (missing snapshot, missing env vars)
"""

import argparse
import json
import re
import sys
from pathlib import Path

from _name_match import (
    clean_project_name,
    name_similarity,
    strip_diacritics,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"
SNAPSHOT_PATH = WORKSPACE_ROOT / "data" / "hubble-snapshot.json"
STATE_FILE = WORKSPACE_ROOT / "data" / "handover-state.json"
HANDLES_FILE = WORKSPACE_ROOT / "data" / "ae-handles.json"
ENV_FILE = WORKSPACE_ROOT / ".env"


# ── Env ──────────────────────────────────────────────────────────────────────


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


# ── AE handle derivation ────────────────────────────────────────────────────


def load_confirmed_handles() -> dict[str, str]:
    if HANDLES_FILE.exists():
        try:
            return json.loads(HANDLES_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def derive_ae_handle(display_name: str, confirmed: dict[str, str]) -> tuple[str | None, str]:
    """
    Derive a Slack handle from an AE display name.
    Returns (handle_or_None, source) where source is "confirmed" or "derived".
    """
    if not display_name or not display_name.strip():
        return None, "none"

    if display_name in confirmed:
        return confirmed[display_name], "confirmed"

    name = strip_diacritics(display_name).strip()
    parts = name.split()
    if len(parts) < 2:
        return name.lower().replace(" ", ""), "derived"

    first_initial = parts[0][0].lower()
    last_name = parts[-1].lower()
    handle = first_initial + last_name
    handle = re.sub(r"[^a-z0-9]", "", handle)
    return handle, "derived"


# ── State helpers ────────────────────────────────────────────────────────────


def load_processed_slugs() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        state = json.loads(STATE_FILE.read_text())
        return {t["slug"] for t in state.get("processed_threads", []) if t.get("slug")}
    except (json.JSONDecodeError, ValueError, KeyError):
        return set()


def has_handover_link(project_dir: Path) -> bool:
    """Check if PROJECT.md already has a non-TBD Handover link."""
    pm = project_dir / "PROJECT.md"
    if not pm.exists():
        return False
    for line in pm.read_text().splitlines():
        if re.match(r"^- Handover:\s*(.+)", line):
            value = line.split(":", 1)[1].strip()
            return bool(value) and value.upper() != "TBD"
    return False


def get_project_id_from_hubble_json(project_dir: Path) -> int | None:
    hj = project_dir / "hubble.json"
    if not hj.exists():
        return None
    try:
        data = json.loads(hj.read_text())
        pid = data.get("project_id")
        return int(pid) if pid else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--slug", help="Restrict to a single project slug")
    parser.add_argument("--force", action="store_true",
                        help="Skip handover-state.json dedup (re-search all)")
    args = parser.parse_args()

    # ── Load config ──
    env = load_env()
    channel_id = env.get("HANDOVER_CHANNEL_ID", "").strip()

    if not channel_id or channel_id == "REPLACE":
        print(json.dumps({"error": "HANDOVER_CHANNEL_ID not set in .env"}))
        sys.exit(1)

    # ── Load snapshot ──
    if not SNAPSHOT_PATH.exists():
        print(json.dumps({"error": "data/hubble-snapshot.json not found. Run hubble-analyst to refresh."}))
        sys.exit(1)

    try:
        snapshot = json.loads(SNAPSHOT_PATH.read_text())
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid hubble-snapshot.json: {e}"}))
        sys.exit(1)

    hubble_rows = snapshot.get("projects", [])
    by_id: dict[int, dict] = {}
    for row in hubble_rows:
        try:
            by_id[int(row["project_id"])] = row
        except (KeyError, ValueError, TypeError):
            continue

    # ── Load state + handles ──
    processed_slugs = set() if args.force else load_processed_slugs()
    confirmed_handles = load_confirmed_handles()

    # ── Enumerate projects ──
    if args.slug:
        target = ACTIVE_DIR / args.slug
        if not target.is_dir():
            print(json.dumps({"error": f"Project not found: {args.slug}"}))
            sys.exit(1)
        scope = [target]
    else:
        if not ACTIVE_DIR.exists():
            print(json.dumps({"error": "projects/active/ does not exist"}))
            sys.exit(1)
        scope = sorted([
            d for d in ACTIVE_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    # ── Build manifest ──
    searches: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for project_dir in scope:
        slug = project_dir.name

        # Skip: already has handover link
        if has_handover_link(project_dir):
            skipped.append({"slug": slug, "reason": "handover link already populated"})
            continue

        # Skip: already processed (unless --force)
        if slug in processed_slugs:
            skipped.append({"slug": slug, "reason": "already in processed_threads"})
            continue

        # Match to Hubble snapshot row
        pid = get_project_id_from_hubble_json(project_dir)
        hubble_row = by_id.get(pid) if pid else None

        # Fallback: fuzzy name match if no hubble.json
        if not hubble_row:
            pm = project_dir / "PROJECT.md"
            if pm.exists():
                local_name = pm.read_text().splitlines()[0].replace("# ", "").strip()
                best_score = 0.0
                best_row = None
                for row in hubble_rows:
                    score = name_similarity(local_name, row.get("project_name", ""))
                    if score > best_score:
                        best_score = score
                        best_row = row
                if best_score >= 0.6 and best_row:
                    hubble_row = best_row

        if not hubble_row:
            errors.append({"slug": slug, "reason": "no hubble.json and no snapshot match"})
            continue

        # Extract search parameters
        raw_project_name = hubble_row.get("project_name", "")
        project_name = clean_project_name(raw_project_name)
        ae_display = hubble_row.get("account_executive", "")
        ae_handle, handle_source = derive_ae_handle(ae_display, confirmed_handles)

        if not project_name:
            errors.append({"slug": slug, "reason": "empty project_name after cleaning"})
            continue

        entry: dict = {
            "slug": slug,
            "project_name": project_name,
            "ae_display_name": ae_display or None,
            "ae_handle": ae_handle,
            "handle_source": handle_source,
        }
        searches.append(entry)

    # ── Output ──
    # The manifest lists which projects to look for and their AE handles. The
    # caller reads the channel BY ID (read_slack_channel_history) and filters the
    # history to these projects in code by name / AE / SFDC opp — no name-based
    # search_slack_messages.
    result = {
        "channel_id": channel_id,
        "searches": searches,
        "skipped": skipped,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
