#!/usr/bin/env python3
"""
Cross-merchant contamination audit for raw/comms.md files.

For every entry in projects/active/<slug>/raw/comms.md, parse the From and To
addresses and score each one against the merchant's identity model:

  - Email search query (PROJECT.md ## Communication, "Email search")
  - Key Contacts (PROJECT.md ## Key Contacts) — emails on those lines
  - Stripe internal (always allowed: any *@stripe.com address)

If at least one participant matches the merchant, the entry is OK. If no
participant matches, the entry is flagged as a likely cross-merchant
misroute and surfaced in the report. The report optionally suggests a
better-matching merchant by re-scoring the suspect addresses against
every other active merchant's identity model.

This diagnostic catches cross-merchant contamination (misrouted comms entries,
leaked Asana subtasks). Run it weekly + after every slug merge to catch regressions.

Usage:
  python3 scripts/cross-merchant-audit.py                # all merchants
  python3 scripts/cross-merchant-audit.py --slug <slug>  # one merchant
  python3 scripts/cross-merchant-audit.py --since 2026-05-01  # only entries on/after date
  python3 scripts/cross-merchant-audit.py --json         # machine-readable
  python3 scripts/cross-merchant-audit.py --suggest      # also suggest correct merchant for each suspect entry
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = WORKSPACE_ROOT / "projects" / "active"

# Generic mailbox providers that should never be treated as merchant identifiers
GENERIC_DOMAINS = {
    "gmail.com",
    "icloud.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "me.com",
    "live.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
}

# Automated-notification senders that pollute comms.md but aren't contamination.
# When a suspect entry's only non-Stripe participants are from these domains,
# treat it as auto-noise rather than a misroute.
AUTOMATED_DOMAINS = {
    "asana.com",
    "goodtime.io",
    "ws.mavenlink.com",
    "mavenlink.com",
    "calendly.com",
    "amazonses.com",
    "amazonaws.com",
    "noreply.google.com",
    "google.com",
    "docusign.net",
    "zoom.us",
    "salesforce.com",
    "slack.com",
}


def is_automated(addr):
    if not addr or "@" not in addr:
        return False
    domain = addr.split("@", 1)[1].lower()
    if domain in AUTOMATED_DOMAINS:
        return True
    local = addr.split("@", 1)[0].lower()
    if local.startswith("noreply") or local.startswith("no-reply") or local.startswith("workspace+"):
        return True
    return False

# Stripe-internal domain — always allowed across every merchant.
# Matched as a suffix (so jira.stripe.com, mail.stripe.com, etc. also count).
STRIPE_INTERNAL = {"stripe.com"}


def is_stripe_internal(domain):
    if not domain:
        return False
    domain = domain.lower()
    for s in STRIPE_INTERNAL:
        if domain == s or domain.endswith("." + s):
            return True
    return False

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ENTRY_HEADER_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s+—\s+([^\s—]+)\s+—\s+(.*)$")
FROM_RE = re.compile(r"^\*\*From\*\*:\s*(.+)$")
TO_RE = re.compile(r"^\*\*To\*\*:\s*(.+)$")


def emails_in(text):
    return [m.group(0).lower() for m in EMAIL_RE.finditer(text or "")]


def parse_email_search(text):
    """Extract domains and quoted-name aliases from an Email search query."""
    domains = set()
    names = set()
    for m in re.finditer(r"(?:from|to):([A-Za-z0-9.\-_]+\.[A-Za-z]{2,})", text):
        d = m.group(1).lower()
        if d not in GENERIC_DOMAINS:
            domains.add(d)
    for m in re.finditer(r'(?:from|to):"([^"]+)"', text):
        names.add(m.group(1).lower())
    for m in re.finditer(r"(?:from|to):([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text):
        addr = m.group(1).lower()
        # Catch personal-fallback addresses (gmail/icloud/etc) as explicit allowlist entries
        names.add(addr)
    return domains, names


def parse_project(slug):
    """Extract the merchant identity model from PROJECT.md.

    Returns:
      {
        "slug": str,
        "domains": set[str],          # exact domains the merchant uses
        "explicit_addresses": set[str],   # specific addresses explicitly allowlisted
        "key_contact_addresses": set[str],
        "name_tokens": set[str],      # for soft suggest scoring (lowercase tokens of merchant name)
      }
    """
    path = PROJECTS_DIR / slug / "PROJECT.md"
    if not path.exists():
        return None
    text = path.read_text()
    lines = text.splitlines()
    name = lines[0].lstrip("# ").strip() if lines else slug

    # Parse Email search line (under ## Communication)
    in_comm = False
    email_query = ""
    in_key_contacts = False
    key_contact_addresses = set()
    for line in lines:
        if re.match(r"^## Communication", line, re.I):
            in_comm = True
            in_key_contacts = False
            continue
        if re.match(r"^## Key Contacts", line, re.I):
            in_key_contacts = True
            in_comm = False
            continue
        if line.startswith("## "):
            in_comm = False
            in_key_contacts = False
            continue
        if in_comm:
            m = re.match(r"-\s*\*\*Email search\*\*:\s*(.+)$", line)
            if m:
                email_query += " " + m.group(1)
        if in_key_contacts:
            for addr in emails_in(line):
                if addr.split("@")[1] not in GENERIC_DOMAINS:
                    key_contact_addresses.add(addr)
                else:
                    # Personal mailbox listed as a contact: still allowlist it
                    key_contact_addresses.add(addr)

    domains, explicit = parse_email_search(email_query)
    name_tokens = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", name) if len(t) > 2}

    return {
        "slug": slug,
        "name": name,
        "domains": domains,
        "explicit_addresses": explicit,
        "key_contact_addresses": key_contact_addresses,
        "name_tokens": name_tokens,
    }


def matches_merchant(addr, identity):
    """True if addr is allowed under the merchant's identity model.

    Stripe-internal addresses always match (cross-merchant Stripe-internal
    threads are normal). For everything else we require an exact match in the
    domains, explicit address allowlist, or key-contact set.
    """
    if not addr or "@" not in addr:
        return False
    addr = addr.lower()
    domain = addr.split("@", 1)[1]
    if is_stripe_internal(domain):
        return True
    if domain in identity["domains"]:
        return True
    if addr in identity["explicit_addresses"]:
        return True
    if addr in identity["key_contact_addresses"]:
        return True
    return False


def score_against(addr, identity):
    """Score how well an address matches a merchant's identity model.

    Higher = stronger match. Returns float so tie-breakers can promote a
    "natural" match (where the merchant's name tokens overlap the address's
    domain root) over a "leaked" match (where the address is in a list but
    no token overlap suggests it really belongs there).

      3.5 = exact key-contact / explicit-allowlist match AND name-token overlap
            (natural — contact@example.com listed as contact for example-merchant)
      3.0 = exact key-contact / explicit-allowlist match without name-token
            overlap (could be a leaked contact — address also appears in another merchant)
      2.5 = domain match AND name-token overlap (natural domain)
      2.0 = domain match without name-token overlap (could be a leaked domain)
      1.0 = local-part or domain root overlaps with name tokens (soft match)
      0.0 = no match
    """
    if not addr or "@" not in addr:
        return 0.0
    addr = addr.lower()
    domain = addr.split("@", 1)[1]
    domain_root = domain.split(".")[0] if "." in domain else domain
    local = addr.split("@", 1)[0]

    name_overlap = False
    for token in identity["name_tokens"]:
        if not token:
            continue
        if token in domain_root or domain_root in token or token in local:
            name_overlap = True
            break

    if addr in identity["explicit_addresses"] or addr in identity["key_contact_addresses"]:
        return 3.5 if name_overlap else 3.0
    if domain in identity["domains"]:
        return 2.5 if name_overlap else 2.0
    if name_overlap:
        return 1.0
    return 0.0


def parse_comms_entries(path):
    """Parse comms.md into a list of entries.

    Each entry: { 'date', 'kind', 'subject', 'from', 'to', 'line_start', 'line_end' }
    """
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    entries = []
    current = None
    for i, line in enumerate(lines):
        m = ENTRY_HEADER_RE.match(line)
        if m:
            if current:
                current["line_end"] = i - 1
                entries.append(current)
            current = {
                "date": m.group(1),
                "kind": m.group(2),
                "subject": m.group(3).strip(),
                "from": [],
                "to": [],
                "line_start": i,
                "line_end": None,
            }
            continue
        if current is None:
            continue
        m = FROM_RE.match(line)
        if m:
            current["from"].extend(emails_in(m.group(1)))
            continue
        m = TO_RE.match(line)
        if m:
            current["to"].extend(emails_in(m.group(1)))
            continue
    if current:
        current["line_end"] = len(lines) - 1
        entries.append(current)
    return entries


def audit_merchant(slug, identity, all_identities, since=None, suggest=False):
    """Return list of suspect entries for one merchant.

    Two types of contamination get flagged:
      1. NO_MATCH: no participant matches the current merchant's identity model
         (and the thread isn't all-Stripe-internal coordination)
      2. BETTER_MATCH: at least one participant matches a DIFFERENT merchant's
         identity model strictly better than this one. Catches the failure
         mode where the current merchant's email_query is itself corrupted
         (e.g. merchant-A's query lists merchant-B domains, so merchant-B emails appear
         to "match" merchant-A even though they clearly belong to merchant-B).
    """
    comms_path = PROJECTS_DIR / slug / "raw" / "comms.md"
    entries = parse_comms_entries(comms_path)
    suspects = []
    for entry in entries:
        if since and entry["date"] < since:
            continue
        participants = set(entry["from"] + entry["to"])
        if not participants:
            continue

        # All-internal Stripe threads (no merchant participants) are typically
        # internal coordination — not contamination. Allow them.
        if all(
            is_stripe_internal(addr.split("@", 1)[1])
            for addr in participants
            if "@" in addr
        ):
            continue

        # Stripe-internal participants don't help differentiate which merchant
        # an entry belongs to (your.name@stripe.com appears in every PROJECT.md's
        # Stripe contacts). Exclude them from merchant-identity scoring so the
        # better-match comparison is driven by the actual merchant addresses.
        merchant_participants = {
            addr for addr in participants
            if "@" in addr and not is_stripe_internal(addr.split("@", 1)[1])
        }
        if not merchant_participants:
            # All-internal Stripe thread: handled above. This branch shouldn't fire.
            continue

        # If every non-Stripe participant is an automated notification sender
        # (Asana, GoodTime, Mavenlink, etc.), this is a system notification, not
        # contamination. Skip silently.
        if all(is_automated(addr) for addr in merchant_participants):
            continue

        own_match = any(matches_merchant(addr, identity) for addr in merchant_participants)
        own_score = max(
            (score_against(addr, identity) for addr in merchant_participants), default=0
        )

        best_other_slug = None
        best_other_score = 0
        for other_slug, other_identity in all_identities.items():
            if other_slug == slug:
                continue
            score = max(
                (score_against(addr, other_identity) for addr in merchant_participants),
                default=0,
            )
            if score > best_other_score:
                best_other_score = score
                best_other_slug = other_slug

        contamination_type = None
        if not own_match:
            contamination_type = "no_match"
        elif best_other_score > own_score and best_other_score >= 2.5:
            # Another merchant has a strictly better match — and it has the
            # name-token tie-breaker (>= 2.5 means at least a domain-with-overlap
            # or a key-contact-with-overlap match). The current merchant's
            # identity model probably leaks tokens that shouldn't be there.
            contamination_type = "better_match_elsewhere"
        else:
            continue

        suspect = {
            "slug": slug,
            "date": entry["date"],
            "kind": entry["kind"],
            "subject": entry["subject"][:100],
            "line_start": entry["line_start"] + 1,
            "line_end": (entry["line_end"] or entry["line_start"]) + 1,
            "participants": sorted(participants),
            "contamination_type": contamination_type,
            "own_score": own_score,
        }
        if suggest or contamination_type == "better_match_elsewhere":
            if best_other_slug and best_other_score >= 2:
                suspect["likely_belongs_to"] = best_other_slug
                suspect["suggest_score"] = best_other_score
        suspects.append(suspect)
    return suspects


def render_report(all_suspects, json_mode):
    if json_mode:
        print(json.dumps(all_suspects, indent=2))
        return
    if not all_suspects:
        print("No cross-merchant contamination detected.")
        return
    by_slug = {}
    for s in all_suspects:
        by_slug.setdefault(s["slug"], []).append(s)
    print(f"Found {len(all_suspects)} suspect entries across {len(by_slug)} merchants.\n")
    for slug, items in sorted(by_slug.items()):
        print(f"  [{slug}] {len(items)} suspect entr{'y' if len(items)==1 else 'ies'}")
        for s in items[:8]:
            tail = ""
            if s.get("likely_belongs_to"):
                tail = f"  -> likely belongs to [{s['likely_belongs_to']}] (score {s['suggest_score']})"
            participants = ", ".join(s["participants"][:3])
            if len(s["participants"]) > 3:
                participants += f" + {len(s['participants']) - 3} more"
            print(
                f"    {s['date']} L{s['line_start']}: {s['subject'][:60]} ({participants}){tail}"
            )
        if len(items) > 8:
            print(f"    ... and {len(items)-8} more")


def audit_query_overlaps(all_identities, json_mode):
    """Check for domain overlaps across merchants' Email search queries.

    Returns list of overlaps. Use before a scan to block contaminated merchants.
    """
    domain_to_slugs = {}
    for slug, identity in all_identities.items():
        for domain in identity["domains"]:
            domain_to_slugs.setdefault(domain, []).append(slug)

    overlaps = []
    for domain, slugs in sorted(domain_to_slugs.items()):
        if len(slugs) > 1:
            overlaps.append({"domain": domain, "merchants": sorted(slugs)})

    if json_mode:
        print(json.dumps({"overlaps": overlaps, "blocked_slugs": sorted({s for o in overlaps for s in o["merchants"]})}, indent=2))
    elif overlaps:
        print(f"Found {len(overlaps)} domain overlap(s) across Email search queries:\n")
        for o in overlaps:
            print(f"  {o['domain']} — shared by: {', '.join(o['merchants'])}")
        blocked = sorted({s for o in overlaps for s in o["merchants"]})
        print(f"\nBlocked merchants (fix their Email search queries): {', '.join(blocked)}")
    else:
        print("No email query overlaps detected.")
    return overlaps


def main():
    parser = argparse.ArgumentParser(description="Detect cross-merchant contamination in raw/comms.md.")
    parser.add_argument("--slug", help="Limit audit to one merchant")
    parser.add_argument("--since", help="Only entries on/after YYYY-MM-DD")
    parser.add_argument("--suggest", action="store_true", help="Suggest correct merchant for each suspect entry")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--queries-only", action="store_true",
                        help="Check for domain overlaps in Email search queries only (no comms.md scan)")
    args = parser.parse_args()

    all_slugs = sorted(d.name for d in PROJECTS_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))
    all_identities = {}
    for slug in all_slugs:
        identity = parse_project(slug)
        if identity:
            all_identities[slug] = identity

    if args.queries_only:
        overlaps = audit_query_overlaps(all_identities, args.json)
        sys.exit(1 if overlaps else 0)

    target_slugs = [args.slug] if args.slug else all_slugs
    all_suspects = []
    for slug in target_slugs:
        identity = all_identities.get(slug)
        if not identity:
            continue
        suspects = audit_merchant(slug, identity, all_identities, since=args.since, suggest=args.suggest)
        all_suspects.extend(suspects)

    render_report(all_suspects, args.json)


if __name__ == "__main__":
    main()
