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
    cross_source_match,
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

# Batch-level cross-source dedup: populated by CS staging files, checked by Gmail files.
# Keyed by slug → list of {from, date, subject} dicts from CS messages ingested this run.
_cs_batch_index = {}


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


def _resolve_display_name(addr, from_field, to_field, cc_field):
    """Find display name for addr from raw header fields."""
    for field in (from_field, to_field, cc_field):
        if not field:
            continue
        if addr.lower() in field.lower():
            return extract_name(field)
    local = addr.split("@")[0]
    return local.replace(".", " ").title()


# ── Scan-state management ─────────────────────────────────────────────────

def load_scan_state(slug):
    path = PROJECTS_DIR / slug / "scan-state.json"
    if path.exists():
        return json.loads(path.read_text())
    return {
        "last_email_scan": None,
        "last_slack_scan": None,
        "last_cs_scan": None,
        "logged_email_ids": [],
        "logged_cs_message_ids": [],
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


def format_slack_entry_incremental(thread, new_messages):
    """Format only new Slack thread replies as a continuation comms.md entry."""
    if not new_messages:
        return None

    first_new = new_messages[0]
    channel_id = thread.get("channel_id", "")
    channel_name = thread.get("channel_name", channel_id)
    permalink = thread.get("permalink", "")

    # Date from first new message
    try:
        dt = datetime.fromtimestamp(float(first_new.get("ts", "0")), tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        date_display = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError, OSError):
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_display = date_str

    # Use original thread root for subject context
    all_messages = thread.get("messages", [])
    root_text = all_messages[0].get("text", "")[:80].replace("\n", " ") if all_messages else ""
    if len(all_messages[0].get("text", "") if all_messages else "") > 80:
        root_text += "..."

    lines = [
        f"## {date_str} — slack (continued) — {channel_name} — {root_text}",
        f"- **Channel**: {channel_name} ({channel_id})",
    ]
    if permalink:
        lines.append(f"- **Continuation of**: {permalink}")
    lines.append(f"- **Date**: {date_display}")
    lines.append(f"- **New replies**: {len(new_messages)}")
    lines.append("")

    for msg in new_messages:
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
    """Find new addresses not covered by the identity model. Return list of added contacts.
    all_addresses: dict {addr: display_name} or iterable of bare addresses (backward compat).
    """
    added = []
    items = all_addresses.items() if isinstance(all_addresses, dict) else ((a, None) for a in all_addresses)
    for addr, display_name in items:
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
        added.append(addr)
        if not dry_run:
            _patch_project_email_query(slug, domain)
            _patch_project_contacts(slug, addr, display_name=display_name)

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


def _patch_project_contacts(slug, addr, display_name=None):
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
        if display_name and display_name.lower() != addr.lower():
            display = display_name
        else:
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
    source = data.get("source", "gmail")

    result = {
        "slug": slug,
        "source": source,
        "new_emails": 0,
        "new_cs_emails": 0,
        "new_slack_threads": 0,
        "outbound_emails": 0,
        "inbound_emails": 0,
        "cross_source_deduped": 0,
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
    logged_cs_ids = set(state.get("logged_cs_message_ids", []))
    logged_slack_ids = set(state.get("logged_slack_thread_ids", []))

    # Message-level Slack tracking (backward-compat: migrate from flat list)
    slack_thread_state = state.get("slack_thread_state", {})
    if not slack_thread_state and logged_slack_ids:
        slack_thread_state = {k: {"last_message_ts": "0", "messages_logged": 0}
                             for k in logged_slack_ids}

    all_new_addresses = {}  # {bare_addr: display_name}

    # ── Process emails ─────────────────────────────────────────────────
    for email in data.get("emails", []):
        from_field = email.get("from", "")
        to_field = email.get("to", "")
        cc_field = email.get("cc", "")
        participants = emails_in(from_field) + emails_in(to_field) + emails_in(cc_field)
        non_auto_participants = [p for p in participants if not is_automated(p)]

        if source == "case_studio":
            # ── CS path: dedup by sfdc_id, hybrid direction ────────────
            sfdc_id = email.get("sfdc_id", "")
            if not sfdc_id:
                result["errors"].append(f"CS email missing sfdc_id: {email.get('subject', '?')}")
                continue

            if sfdc_id in logged_cs_ids:
                continue

            # Identity gate
            if not any(matches_merchant(p, identity) for p in non_auto_participants):
                quarantine_entry(slug, {
                    "date": email.get("date", ""),
                    "subject": email.get("subject", ""),
                    "from": from_field,
                    "to": to_field,
                    "message_id": sfdc_id,
                }, "no participant matches merchant identity model", dry_run)
                logged_cs_ids.add(sfdc_id)
                result["quarantined"] += 1
                continue

            # Hybrid direction detection
            if not email.get("is_incoming"):
                direction = "Outbound"
            elif is_outbound(from_field, OUTBOUND_ADDRESSES):
                direction = "Outbound"
            else:
                direction = "Inbound"

            if direction == "Outbound":
                result["outbound_emails"] += 1
            else:
                result["inbound_emails"] += 1

            # Format and write
            entry = format_email_entry(email, direction)
            append_to_comms(slug, entry, dry_run)

            date_str = parse_date_from_email(email.get("date", ""))
            timeline = format_timeline_entry(
                date_str=date_str,
                comm_type="email (CS)",
                subject=email.get("subject", "No Subject"),
                direction=direction,
                from_field=from_field,
                to_field=to_field,
                url="",
                msg_id=sfdc_id,
                participants=[extract_name(from_field)] + [extract_name(to_field)],
            )
            prepend_to_timeline(slug, timeline, dry_run)

            logged_cs_ids.add(sfdc_id)
            for p in non_auto_participants:
                if p not in all_new_addresses:
                    all_new_addresses[p] = _resolve_display_name(p, from_field, to_field, cc_field)
            result["new_cs_emails"] += 1

            # Register in batch index for cross-source dedup
            _cs_batch_index.setdefault(slug, []).append({
                "from": from_field,
                "date": email.get("date", ""),
                "subject": email.get("subject", ""),
            })

        else:
            # ── Gmail path: dedup by message_id, cross-source check ────
            msg_id = email.get("message_id", "")
            if not msg_id:
                result["errors"].append(f"Email missing message_id: {email.get('subject', '?')}")
                continue

            if msg_id in logged_email_ids:
                continue

            # Cross-source dedup: skip if CS already ingested this message
            if identity.get("scan_source") == "core" and slug in _cs_batch_index:
                gmail_fingerprint = {
                    "from": from_field,
                    "date": email.get("date", ""),
                    "subject": email.get("subject", ""),
                }
                if any(cross_source_match(cs_fp, gmail_fingerprint) for cs_fp in _cs_batch_index[slug]):
                    logged_email_ids.add(msg_id)
                    result["cross_source_deduped"] += 1
                    continue

            # Identity gate
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
            for p in non_auto_participants:
                if p not in all_new_addresses:
                    all_new_addresses[p] = _resolve_display_name(p, from_field, to_field, cc_field)
            result["new_emails"] += 1

    # ── Process Slack threads ──────────────────────────────────────────
    for thread in data.get("slack_threads", []):
        channel_id = thread.get("channel_id", "")
        thread_ts = thread.get("thread_ts", "")
        if not channel_id or not thread_ts:
            result["errors"].append(f"Slack thread missing channel_id/thread_ts")
            continue

        thread_key = f"{channel_id}/{thread_ts}"
        messages = thread.get("messages", [])
        if not messages:
            continue

        # Message-level dedup: check for new replies in known threads
        thread_meta = slack_thread_state.get(thread_key)
        if thread_meta:
            last_ts = float(thread_meta.get("last_message_ts", "0"))
            new_messages = [m for m in messages if float(m.get("ts", "0")) > last_ts]
            if not new_messages:
                continue
            entry = format_slack_entry_incremental(thread, new_messages)
            is_continuation = True
        else:
            new_messages = messages
            entry = format_slack_entry(thread)
            is_continuation = False

        if entry is None:
            continue

        append_to_comms(slug, entry, dry_run)

        # Timeline entry
        ref_msg = new_messages[0]
        try:
            dt = datetime.fromtimestamp(float(ref_msg.get("ts", "0")), tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        slack_users = {msg.get("user", "") for msg in new_messages if msg.get("user")}
        first_user = ref_msg.get("user", "unknown")
        outbound = first_user.lower() in {a.split("@")[0] for a in OUTBOUND_ADDRESSES}
        slack_handle = ENV.get("SLACK_HANDLE", "").lower()
        if slack_handle and first_user.lower() == slack_handle:
            outbound = True
        direction = "Outbound" if outbound else "Inbound"

        channel_name = thread.get("channel_name", channel_id)
        subject_hint = ref_msg.get("text", "")[:60].replace("\n", " ")
        permalink = thread.get("permalink", "")
        comm_type = "slack (continued)" if is_continuation else "slack"

        timeline = format_timeline_entry(
            date_str=date_str,
            comm_type=comm_type,
            subject=f"{channel_name} — {subject_hint}",
            direction=direction,
            from_field=first_user,
            to_field=channel_name,
            url=permalink,
            msg_id=thread_key,
            participants=list(slack_users)[:5],
        )
        prepend_to_timeline(slug, timeline, dry_run)

        # Update thread state
        max_ts = max((m.get("ts", "0") for m in messages), key=float)
        prev_count = thread_meta["messages_logged"] if thread_meta else 0
        slack_thread_state[thread_key] = {
            "last_message_ts": max_ts,
            "messages_logged": prev_count + len(new_messages),
        }
        logged_slack_ids.add(thread_key)
        result["new_slack_threads"] += 1

    # ── Contact discovery ──────────────────────────────────────────────
    if all_new_addresses:
        added = discover_contacts(slug, all_new_addresses, identity, dry_run)
        result["contacts_added"] = added

    # ── Update scan-state ──────────────────────────────────────────────
    state["logged_email_ids"] = sorted(logged_email_ids)
    state["logged_cs_message_ids"] = sorted(logged_cs_ids)
    state["logged_slack_thread_ids"] = sorted(logged_slack_ids)
    state["slack_thread_state"] = slack_thread_state
    fetched_at = data.get("fetched_at")
    if not fetched_at:
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if data.get("emails") is not None:
        if source == "case_studio":
            state["last_cs_scan"] = fetched_at
        else:
            state["last_email_scan"] = fetched_at
    if data.get("slack_threads") is not None and source != "case_studio":
        state["last_slack_scan"] = fetched_at
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

    # Sort: CS files (*-cs.json) first so cross-source dedup works correctly
    staging_files.sort(key=lambda f: (0 if f.stem.endswith("-cs") else 1, f.name))

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
            written = result["new_emails"] + result.get("new_cs_emails", 0) + result["new_slack_threads"]
            total_written += written
            total_quarantined += result["quarantined"]
            cs_label = f", {result['new_cs_emails']} CS" if result.get("new_cs_emails") else ""
            dedup_label = f", {result['cross_source_deduped']} cross-deduped" if result.get("cross_source_deduped") else ""
            print(f"  → {result['new_emails']} emails{cs_label}, {result['new_slack_threads']} slack, "
                  f"{result['quarantined']} quarantined{dedup_label}, {len(result['contacts_added'])} contacts added")
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
