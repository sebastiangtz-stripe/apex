#!/usr/bin/env python3
from __future__ import annotations
"""
Create a merchant project from a parsed handover proposal.

Reads a proposal JSON (from handover-parse.py) and:
  1. Creates projects/active/<slug>/ with PROJECT.md + empty action-items.md
     + empty raw/comms.md + timeline.md (with a seed entry) + scan-state.json.
  2. Chains to sync-to-asana.py to create the Asana task.
  3. Chains to hubble-reconcile.py --backfill to populate SF + Kantata links.
  4. Appends the thread to data/handover-state.json so re-scans dedup.

Usage:
  python3 scripts/handover-create.py --proposal proposal.json
  cat proposal.json | python3 scripts/handover-create.py --proposal-stdin

Exit codes:
  0  clean
  1  slug collision (projects/active/<slug>/ already exists)
  2  one of the chained steps (sync-to-asana / hubble-reconcile) failed —
     folder stays so the user can inspect; state file is NOT updated so a
     retry will re-attempt
  3  filesystem error (e.g. couldn't write PROJECT.md)
  4  proposal is missing required fields (merchant_name, slug, thread_permalink)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"
STATE_FILE = WORKSPACE_ROOT / "data" / "handover-state.json"


GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "live.com", "msn.com", "me.com", "protonmail.com", "proton.me",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def email_search_query(contact: dict | None) -> str:
    if not contact or not contact.get("email"):
        return "TBD"
    email = contact["email"]
    if "@" not in email:
        return "TBD"
    domain = email.split("@", 1)[1].lower()
    name = contact.get("name", "").strip()
    parts = []
    if domain and domain not in GENERIC_EMAIL_DOMAINS:
        parts.append(f"from:{domain} OR to:{domain}")
    else:
        parts.append(f"from:{email} OR to:{email}")
    if name:
        parts.append(f'from:"{name}"')
    return " OR ".join(parts)


def render_project_md(proposal: dict) -> str:
    merchant = proposal["merchant_name"]
    slug = proposal["slug"]
    handover_url = proposal["thread_permalink"]
    manifest = proposal.get("manifest_url", "TBD")
    sfdc = proposal.get("sfdc_url") or (
        f"https://stripe.lightning.force.com/lightning/r/Opportunity/{proposal['sfdc_opp_id']}/view"
        if proposal.get("sfdc_opp_id") else "TBD"
    )
    products = proposal.get("products_hint", "TBD")
    acct = proposal.get("acct_id", "TBD")
    contact = proposal.get("primary_contact")
    ae = proposal.get("ae", "TBD")
    territory = proposal.get("territory")
    eligibility = proposal.get("eligibility")

    aonr = proposal.get("aonr", "TBD")

    contacts_block = (
        f"- {contact['name']} — {contact['email']}"
        if contact else "- TBD"
    )
    email_q = email_search_query(contact)

    notes_lines = [
        f"- Handover via Slack on {today_iso()} from @{ae}" if ae != "TBD" else
        f"- Handover via Slack on {today_iso()}",
    ]
    if territory:
        notes_lines.append(f"- Territory: {territory}")
    if eligibility:
        notes_lines.append(f"- Eligibility (at handover): {eligibility}")

    product_lines = []
    for p in (products.split(",") if products and products != "TBD" else ["TBD"]):
        product_lines.append(f"- [ ] {p.strip()}")

    return f"""# {merchant}

## Overview
- **Account ID(s)**: {acct}
- **Products**: {products}
- **Status**: Discovery
- **Priority**: Medium
- **Started**: {today_iso()}
- **Due**: TBD
- **AONR**: {aonr}
- **SFDC Opportunity Owner**: {ae}

## Key Contacts
{contacts_block}

## Communication
- **Email search**: {email_q}
- **Slack channels**: TBD
- **Stripe contacts**: @{ae if ae != "TBD" else "TBD"}

## External Links
- Handover: {handover_url}
- Manifest: {manifest}
- Salesforce: {sfdc}
- Kantata Project ID: TBD
- Kantata Workspace: TBD
- CSAT: TBD

## Product Activation
{chr(10).join(product_lines)}

## Notes
{chr(10).join(notes_lines)}
"""


def render_timeline_md(proposal: dict) -> str:
    merchant = proposal["merchant_name"]
    ae = proposal.get("ae", "unknown")
    permalink = proposal["thread_permalink"]
    return f"""# Timeline — {merchant}

## {today_iso()} — Project created from Slack handover
- Handover by @{ae}
- Source: {permalink}
- Bootstrap: automated via handover-create.py
"""


def render_scan_state() -> str:
    return json.dumps(
        {
            "last_email_scan": None,
            "last_slack_scan": None,
            "logged_email_ids": [],
            "logged_slack_thread_ids": [],
        },
        indent=2,
    ) + "\n"


# ── State file ────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return _empty_state()
    return _empty_state()


def _empty_state() -> dict:
    return {
        "last_scan": None,
        "channels_scanned": [],
        "processed_threads": [],
        "skipped_threads": [],
    }


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def append_processed(proposal: dict) -> None:
    state = load_state()
    entry = {
        "channel_id": proposal.get("channel_id"),
        "thread_ts": proposal.get("thread_ts"),
        "slug": proposal["slug"],
        "processed_at": now_iso(),
    }
    if not any(
        t.get("channel_id") == entry["channel_id"]
        and t.get("thread_ts") == entry["thread_ts"]
        for t in state.get("processed_threads", [])
    ):
        state.setdefault("processed_threads", []).append(entry)
        save_state(state)


# ── Bootstrap one proposal ────────────────────────────────────────────────────

REQUIRED = ["merchant_name", "slug", "thread_permalink"]


def bootstrap(proposal: dict) -> dict:
    missing_req = [f for f in REQUIRED if not proposal.get(f)]
    if missing_req:
        return {"ok": False, "exit": 4,
                "error": f"Proposal missing required fields: {missing_req}"}

    slug = proposal["slug"]
    target = ACTIVE_DIR / slug
    if target.exists():
        return {"ok": False, "exit": 1,
                "error": f"Slug collision: {target.relative_to(WORKSPACE_ROOT)} already exists"}

    try:
        target.mkdir(parents=True, exist_ok=False)
        (target / "raw").mkdir()
        (target / "drafts").mkdir()
        (target / "issues").mkdir()
        (target / "PROJECT.md").write_text(render_project_md(proposal))
        (target / "timeline.md").write_text(render_timeline_md(proposal))
        (target / "action-items.md").write_text(
            f"# Action Items — {proposal['merchant_name']}\n\n## Open\n\n## Completed\n"
        )
        (target / "raw" / "comms.md").write_text(
            f"# Raw Comms — {proposal['merchant_name']}\n"
        )
        (target / "scan-state.json").write_text(render_scan_state())
    except OSError as e:
        return {"ok": False, "exit": 3, "error": f"Filesystem error: {e}"}

    # Hubble backfill BEFORE Asana so the task description has SF/Kantata links
    hubble_ok, hubble_msg = _chain(
        ["python3", str(WORKSPACE_ROOT / "scripts" / "hubble-reconcile.py"),
         "--backfill", "--slug", slug],
        "hubble-reconcile",
    )

    asana_ok, asana_msg = _chain(
        ["python3", str(WORKSPACE_ROOT / "scripts" / "sync-to-asana.py"),
         "--slug", slug],
        "sync-to-asana",
    )
    if not asana_ok:
        return {"ok": False, "exit": 2,
                "error": f"Asana sync failed: {asana_msg}",
                "folder": str(target.relative_to(WORKSPACE_ROOT))}

    append_processed(proposal)

    return {
        "ok": True,
        "exit": 0,
        "slug": slug,
        "merchant_name": proposal["merchant_name"],
        "aonr": proposal.get("aonr", "TBD"),
        "ae": proposal.get("ae", "TBD"),
        "asana": "created",
        "hubble_backfill": "ok" if hubble_ok else f"skipped ({hubble_msg})",
        "folder": str(target.relative_to(WORKSPACE_ROOT)),
    }


def _chain(cmd: list[str], label: str) -> tuple[bool, str]:
    """Run a subprocess. Returns (success, last_line_of_output_or_error)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
            cwd=str(WORKSPACE_ROOT),
        )
    except FileNotFoundError as e:
        return False, str(e)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        return False, tail[-1] if tail else f"{label} exited {result.returncode}"
    return True, "ok"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--proposal", type=str,
                     help="Path to a single proposal JSON file.")
    grp.add_argument("--proposal-stdin", action="store_true",
                     help="Read a single proposal JSON from stdin.")
    grp.add_argument("--proposals-stdin", action="store_true",
                     help="Read a JSON array of proposals from stdin.")
    args = ap.parse_args()

    if args.proposal:
        proposals = [json.loads(Path(args.proposal).read_text())]
    elif args.proposal_stdin:
        proposals = [json.loads(sys.stdin.read())]
    else:
        proposals = json.loads(sys.stdin.read())
        if not isinstance(proposals, list):
            print("ERROR: --proposals-stdin expects a JSON array.", file=sys.stderr)
            sys.exit(2)

    results = [bootstrap(p) for p in proposals]
    worst_exit = max((r.get("exit", 0) for r in results), default=0)
    print(json.dumps(results, indent=2))
    sys.exit(worst_exit)


if __name__ == "__main__":
    main()
