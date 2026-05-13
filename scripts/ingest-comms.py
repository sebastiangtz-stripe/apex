#!/usr/bin/env python3
"""
Ingest staged communications into project files.

Reads raw fetch dumps from data/staging/<slug>-<date>.json, applies dedup,
identity gate, outbound detection, and contact discovery, then writes to:
  - projects/active/<slug>/raw/comms.md (full verbatim entry)
  - projects/active/<slug>/timeline.md (structured metadata + _pending_ summary)
  - projects/active/<slug>/scan-state.json (dedup state update)
  - projects/active/<slug>/PROJECT.md (new contacts, if discovered)
  - data/scan-review-queue/<slug>-<date>.md (quarantined mismatches)

This is the deterministic Stage 2 of the pipeline split. It replaces the
write logic that previously lived inside the LLM merchant-scanner agent,
making it testable, retry-safe, and free from hallucination risk.

Usage:
  python3 scripts/ingest-comms.py                    # process all staging files
  python3 scripts/ingest-comms.py --slug example-merchant        # process one merchant
  python3 scripts/ingest-comms.py --dry-run          # show what would be written
  python3 scripts/ingest-comms.py --keep-staging     # don't clean up staging files
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import (
    GENERIC_DOMAINS,
    WORKSPACE_ROOT,
    PROJECTS_DIR,
    emails_in,
    is_automated,
    is_outbound,
    is_stripe_internal,
    load_env,
    matches_merchant,
    parse_project,
)

STAGING_DIR = WORKSPACE_ROOT / "data" / "staging"
PROCESSED_DIR = STAGING_DIR / "processed"
QUARANTINE_DIR = WORKSPACE_ROOT / "data" / "scan-review-queue"

ENV = load_env()
OUTBOUND_ADDRESSES = {
    a.strip().lower()
    for a in ENV.get("MY_OUTBOUND_ADDRESSES", "").split(",")
    if a.strip()
}


# ── Helpers ────────────────────────────────────────────────────────────────

def parse_date_from_email(date_str):
    """Extract YYYY-MM-DD from an email Date header. Best-effort."""
    if not date_str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z (%Z)",
        "%d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S %Z",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    match = re.search(r"(\d{4}-\d{2}-\d{2})", date_str)
    if match:
        return match.group(1)
    match = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})", date_str)
    if match:
        day, mon, year = match.groups()
        months = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
                  "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
                  "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}
        return f"{year}-{months[mon]}-{int(day):02d}"
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def extract_name(from_or_to):
    """Extract display name from 'Display Name <email>' or just return the email."""
    if not from_or_to:
        return "Unknown"
    m = re.match(r'"?([^"<]+?)"?\s*<', from_or_to)
    if m:
        return m.group(1).strip()
    addr = emails_in(from_or_to)
    if addr:
        return addr[0].split("@")[0]
    return from_or_to.strip()


def short_name(from_or_to):
    """Short name for timeline display (first name or local part)."""
    name = extract_name(from_or_to)
    parts = name.split()
    if parts:
        return parts[0]
    return name


# ── Scan-state management ─────────────────────────────────────────────────

def load_scan_state(slug):
    path = PROJECTS_DIR / slug / "scan-state.json"
    if path.exists():
        return json.loads(path.read_text())
    return {
        "last_email_scan": None,
        "last_slack_scan": None,
        "logged_email_ids": [],
        "logged_slack_thread_ids": [],
    }


def save_scan_state(slug, state, dry_run=False):
    if dry_run:
        return
    path = PROJECTS_DIR / slug / "scan-state.json"
    path.write_text(json.dumps(state, indent=2) + "\n")


# ── Comms.md writing ──────────────────────────────────────────────────────

def format_email_entry(email, direction_label):
    """Format a single email as a comms.md entry."""
    date_str = parse_date_from_email(email.get("date", ""))
    subject = email.get("subject", "No Subject")
    from_field = email.get("from", "Unknown")
    to_field = email.get("to", "Unknown")
    date_raw = email.get("date", "")
    body = email.get("body", "")
    url = email.get("url", "")

    lines = [
        f"## {date_str} — email — {subject}",
        f"**From**: {from_field}",
        f"**To**: {to_field}",
        f"**Date**: {date_raw}",
    ]
    if url:
        lines.append(f"**Link**: {url}")
    lines.append("")
    lines.append(body.rstrip())
    return "\n".join(lines)


def format_slack_entry(thread):
    """Format a Slack thread as a comms.md entry."""
    messages = thread.get("messages", [])
    if not messages:
        return None

    first_msg = messages[0]
    ts = first_msg.get("ts", "")
    channel_id = thread.get("channel_id", "")
    channel_name = thread.get("channel_name", channel_id)
    permalink = thread.get("permalink", "")

    # Parse date from first message timestamp
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        date_display = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError, OSError):
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_display = date_str

    first_user = first_msg.get("user", "unknown")
    subject_hint = first_msg.get("text", "")[:80].replace("\n", " ")
    if len(first_msg.get("text", "")) > 80:
        subject_hint += "..."

    lines = [
        f"## {date_str} — slack — {channel_name} — {subject_hint}",
        f"- **Channel**: {channel_name} ({channel_id})",
    ]
    if permalink:
        lines.append(f"- **Permalink**: {permalink}")
    lines.append(f"- **From**: {first_user}")
    lines.append(f"- **Date**: {date_display}")
    lines.append("")

    for msg in messages:
        user = msg.get("user", "unknown")
        text = msg.get("text", "")
        lines.append(f"> **{user}**: {text}")
        lines.append(">")

    return "\n".join(lines)


def append_to_comms(slug, entry_text, dry_run=False):
    """Append an entry to raw/comms.md."""
    path = PROJECTS_DIR / slug / "raw" / "comms.md"
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else f"# {slug} — Raw Communications\n"
    if not existing.endswith("\n"):
        existing += "\n"
    existing += f"\n---\n{entry_text}\n"
    path.write_text(existing)


# ── Timeline.md writing ───────────────────────────────────────────────────

def format_timeline_entry(date_str, comm_type, subject, direction, from_field, to_field, url, msg_id, participants):
    """Format a structured timeline entry with _pending_ summary."""
    from_short = short_name(from_field)
    to_short = short_name(to_field)
    id_ref = msg_id or ""

    lines = [
        f"## {date_str} — {comm_type}",
        f"- **Source**: {subject} (`{id_ref}`)",
        f"- **Direction**: {direction} ({from_short} → {to_short})",
    ]
    if url:
        lines.append(f"- **Link**: {url}")
    if participants:
        lines.append(f"- **Participants**: {', '.join(participants)}")
    lines.append("- **Summary**: _pending_")
    return "\n".join(lines)


def prepend_to_timeline(slug, entry_text, dry_run=False):
    """Prepend an entry to timeline.md (newest at top)."""
    path = PROJECTS_DIR / slug / "timeline.md"
    if dry_run:
        return
    if not path.exists():
        header = f"# Timeline — {slug}\n\n<!-- Append new entries at the top -->\n"
        path.write_text(header + entry_text + "\n")
        return
    text = path.read_text()
    lines = text.splitlines()
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = i
            break
    else:
        insert_at = len(lines)

    new_lines = lines[:insert_at] + entry_text.splitlines() + [""] + lines[insert_at:]
    path.write_text("\n".join(new_lines) + "\n")


# ── Contact discovery ─────────────────────────────────────────────────────

def discover_contacts(slug, all_addresses, identity, dry_run=False):
    """Find new addresses not covered by the identity model. Return list of added contacts."""
    added = []
    for addr in all_addresses:
        addr = addr.lower()
        domain = addr.split("@", 1)[1] if "@" in addr else ""
        if is_stripe_internal(domain):
            continue
        if is_automated(addr):
            continue
        if matches_merchant(addr, identity):
            continue
        if domain in GENERIC_DOMAINS:
            continue
        # New non-generic domain not in identity model — add it
        added.append(addr)
        if not dry_run:
            _patch_project_email_query(slug, domain)
            _patch_project_contacts(slug, addr)

    return added


def _patch_project_email_query(slug, domain):
    """Add a domain to the Email search query in PROJECT.md if not already present."""
    path = PROJECTS_DIR / slug / "PROJECT.md"
    if not path.exists():
        return
    text = path.read_text()
    if domain.lower() in text.lower():
        return
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"(-\s*\*\*Email search\*\*:\s*)(.+)$", line)
        if m:
            prefix, query = m.group(1), m.group(2)
            new_query = f"{query} OR from:{domain} OR to:{domain}"
            lines[i] = f"{prefix}{new_query}"
            break
    path.write_text("\n".join(lines) + "\n")


def _patch_project_contacts(slug, addr):
    """Add an address to Key Contacts in PROJECT.md if not already present."""
    path = PROJECTS_DIR / slug / "PROJECT.md"
    if not path.exists():
        return
    text = path.read_text()
    if addr.lower() in text.lower():
        return
    lines = text.splitlines()
    in_contacts = False
    insert_at = None
    for i, line in enumerate(lines):
        if re.match(r"^## Key Contacts", line, re.I):
            in_contacts = True
            continue
        if in_contacts and line.startswith("## "):
            insert_at = i
            break
        if in_contacts and line.strip().startswith("- "):
            insert_at = i + 1
    if insert_at is not None:
        local = addr.split("@")[0]
        display = local.replace(".", " ").title()
        lines.insert(insert_at, f"- {display} — {addr}")
        path.write_text("\n".join(lines) + "\n")


# ── Quarantine ────────────────────────────────────────────────────────────

def quarantine_entry(slug, entry_meta, reason, dry_run=False):
    """Write a quarantined message to data/scan-review-queue/."""
    if dry_run:
        return
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = QUARANTINE_DIR / f"{slug}-{today}.md"
    line = (
        f"- [{entry_meta.get('date', '?')}] "
        f"Subject: {entry_meta.get('subject', '?')} | "
        f"From: {entry_meta.get('from', '?')} | "
        f"To: {entry_meta.get('to', '?')} | "
        f"ID: {entry_meta.get('message_id', '?')} | "
        f"Reason: {reason}\n"
    )
    with open(path, "a") as f:
        f.write(line)


# ── Main ingest logic ─────────────────────────────────────────────────────

def ingest_staging_file(staging_path, dry_run=False):
    """Process one staging JSON file. Returns a per-merchant result dict."""
    data = json.loads(staging_path.read_text())
    slug = data["slug"]

    result = {
        "slug": slug,
        "new_emails": 0,
        "new_slack_threads": 0,
        "outbound_emails": 0,
        "inbound_emails": 0,
        "contacts_added": [],
        "quarantined": 0,
        "errors": [],
    }

    # Load merchant identity model
    identity = parse_project(slug)
    if identity is None:
        result["errors"].append(f"PROJECT.md not found for {slug}")
        return result

    # Load scan state for dedup
    state = load_scan_state(slug)
    logged_email_ids = set(state.get("logged_email_ids", []))
    logged_slack_ids = set(state.get("logged_slack_thread_ids", []))

    all_new_addresses = set()

    # ── Process emails ─────────────────────────────────────────────────
    for email in data.get("emails", []):
        msg_id = email.get("message_id", "")
        if not msg_id:
            result["errors"].append(f"Email missing message_id: {email.get('subject', '?')}")
            continue

        # Dedup
        if msg_id in logged_email_ids:
            continue

        # Identity gate
        from_field = email.get("from", "")
        to_field = email.get("to", "")
        participants = emails_in(from_field) + emails_in(to_field)
        non_auto_participants = [p for p in participants if not is_automated(p)]

        if not any(matches_merchant(p, identity) for p in non_auto_participants):
            quarantine_entry(slug, {
                "date": email.get("date", ""),
                "subject": email.get("subject", ""),
                "from": from_field,
                "to": to_field,
                "message_id": msg_id,
            }, "no participant matches merchant identity model", dry_run)
            logged_email_ids.add(msg_id)
            result["quarantined"] += 1
            continue

        # Determine direction
        outbound = is_outbound(from_field, OUTBOUND_ADDRESSES)
        direction = "Outbound" if outbound else "Inbound"
        if outbound:
            result["outbound_emails"] += 1
        else:
            result["inbound_emails"] += 1

        # Format and write comms.md entry
        entry = format_email_entry(email, direction)
        append_to_comms(slug, entry, dry_run)

        # Format and write timeline entry
        date_str = parse_date_from_email(email.get("date", ""))
        timeline = format_timeline_entry(
            date_str=date_str,
            comm_type="email",
            subject=email.get("subject", "No Subject"),
            direction=direction,
            from_field=from_field,
            to_field=to_field,
            url=email.get("url", ""),
            msg_id=msg_id,
            participants=[extract_name(from_field)] + [extract_name(to_field)],
        )
        prepend_to_timeline(slug, timeline, dry_run)

        # Track for dedup + contact discovery
        logged_email_ids.add(msg_id)
        all_new_addresses.update(non_auto_participants)
        result["new_emails"] += 1

    # ── Process Slack threads ──────────────────────────────────────────
    for thread in data.get("slack_threads", []):
        channel_id = thread.get("channel_id", "")
        thread_ts = thread.get("thread_ts", "")
        if not channel_id or not thread_ts:
            result["errors"].append(f"Slack thread missing channel_id/thread_ts")
            continue

        thread_key = f"{channel_id}/{thread_ts}"

        # Dedup
        if thread_key in logged_slack_ids:
            continue

        # Identity gate for Slack is looser: if the channel is listed in
        # PROJECT.md Communication section, it's always allowed.
        # For DMs/search results, check participant handles.
        messages = thread.get("messages", [])
        slack_users = {msg.get("user", "") for msg in messages if msg.get("user")}

        # For now, allow all Slack threads that came through (the fetch
        # subagent already used the merchant's configured channels).
        # Cross-merchant contamination for Slack is rare since channels are
        # merchant-specific. The cross-merchant-audit.py handles edge cases.

        # Format and write
        entry = format_slack_entry(thread)
        if entry is None:
            continue

        append_to_comms(slug, entry, dry_run)

        # Timeline entry
        first_msg = messages[0] if messages else {}
        try:
            dt = datetime.fromtimestamp(float(first_msg.get("ts", "0")), tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        first_user = first_msg.get("user", "unknown")
        # Determine direction for Slack
        outbound = first_user.lower() in {a.split("@")[0] for a in OUTBOUND_ADDRESSES}
        # Also check if the user matches the SLACK_HANDLE from .env
        slack_handle = ENV.get("SLACK_HANDLE", "").lower()
        if slack_handle and first_user.lower() == slack_handle:
            outbound = True
        direction = "Outbound" if outbound else "Inbound"

        channel_name = thread.get("channel_name", channel_id)
        subject_hint = first_msg.get("text", "")[:60].replace("\n", " ")
        permalink = thread.get("permalink", "")

        timeline = format_timeline_entry(
            date_str=date_str,
            comm_type="slack",
            subject=f"{channel_name} — {subject_hint}",
            direction=direction,
            from_field=first_user,
            to_field=channel_name,
            url=permalink,
            msg_id=thread_key,
            participants=list(slack_users)[:5],
        )
        prepend_to_timeline(slug, timeline, dry_run)

        logged_slack_ids.add(thread_key)
        result["new_slack_threads"] += 1

    # ── Contact discovery ──────────────────────────────────────────────
    if all_new_addresses:
        added = discover_contacts(slug, all_new_addresses, identity, dry_run)
        result["contacts_added"] = added

    # ── Update scan-state ──────────────────────────────────────────────
    state["logged_email_ids"] = sorted(logged_email_ids)
    state["logged_slack_thread_ids"] = sorted(logged_slack_ids)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if data.get("emails") is not None:
        state["last_email_scan"] = now
    if data.get("slack_threads") is not None:
        state["last_slack_scan"] = now
    save_scan_state(slug, state, dry_run)

    return result


def main():
    parser = argparse.ArgumentParser(description="Ingest staged communications into project files.")
    parser.add_argument("--slug", help="Process only this merchant's staging file(s)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without modifying files")
    parser.add_argument("--keep-staging", action="store_true", help="Don't move staging files after processing")
    parser.add_argument("--file", help="Process a specific staging file path")
    args = parser.parse_args()

    if args.dry_run:
        print("[DRY RUN] No files will be modified.\n")

    # Find staging files
    if args.file:
        staging_files = [Path(args.file)]
    else:
        if not STAGING_DIR.exists():
            print("No staging directory found.")
            sys.exit(0)
        staging_files = sorted(STAGING_DIR.glob("*.json"))
        if args.slug:
            staging_files = [f for f in staging_files if f.stem.startswith(args.slug)]

    if not staging_files:
        print("No staging files to process.")
        report = {"processed": [], "skipped_dedup": 0, "skipped_identity_gate": 0, "total_written": 0}
        print(json.dumps(report, indent=2))
        sys.exit(0)

    results = []
    total_written = 0
    total_quarantined = 0

    for sf in staging_files:
        if sf.name == ".gitkeep":
            continue
        print(f"Processing: {sf.name}")
        try:
            result = ingest_staging_file(sf, dry_run=args.dry_run)
            results.append(result)
            written = result["new_emails"] + result["new_slack_threads"]
            total_written += written
            total_quarantined += result["quarantined"]
            print(f"  → {result['new_emails']} emails, {result['new_slack_threads']} slack, "
                  f"{result['quarantined']} quarantined, {len(result['contacts_added'])} contacts added")
            if result["errors"]:
                for err in result["errors"]:
                    print(f"  ⚠ {err}")
        except Exception as e:
            print(f"  ✗ Error: {e}")
            results.append({"slug": sf.stem, "errors": [str(e)]})

        # Clean up staging
        if not args.dry_run and not args.keep_staging:
            PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
            dest = PROCESSED_DIR / sf.name
            sf.rename(dest)

    # Final report
    report = {
        "processed": results,
        "skipped_identity_gate": total_quarantined,
        "total_written": total_written,
    }
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Done: {total_written} entries written, "
          f"{total_quarantined} quarantined across {len(results)} merchants.")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
