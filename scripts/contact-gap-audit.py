#!/usr/bin/env python3
"""
Contact gap audit. For each merchant, scan raw/comms.md for email addresses NOT
covered by the project's PROJECT.md `Email search` query. Surfaces historical
contacts that the merchant-scanner's inline contact-discovery rule (added later)
never had a chance to backfill.

Coverage rule per CLAUDE.md:
  1. Domain search — `from:company.com OR to:company.com` covers all addresses @company.com
  2. Name search — `from:"First Last"` covers that display name regardless of email host
  3. Specific address — `from:personal@gmail.com` covers exactly that address

Generic providers (gmail.com, icloud.com, hotmail.com, outlook.com, yahoo.com)
are NOT auto-covered by domain match; they need a name or specific address line.

Usage:
  python3 scripts/contact-gap-audit.py
  python3 scripts/contact-gap-audit.py --slug example-merchant     # single merchant
  python3 scripts/contact-gap-audit.py --json
"""

import argparse
import json
import re
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"
ENV_FILE = WORKSPACE_ROOT / ".env"

GENERIC_DOMAINS = {"gmail.com", "icloud.com", "hotmail.com", "outlook.com",
                   "yahoo.com", "me.com", "live.com", "aol.com", "proton.me",
                   "protonmail.com", "msn.com", "mac.com"}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
DISPLAY_NAME_RE = re.compile(r'"?([^"<]+?)"?\s*<([^>]+)>')


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
OUTBOUND_ADDRS = {a.strip().lower() for a in (ENV.get("MY_OUTBOUND_ADDRESSES", "")
                                              .split(",")) if a.strip()}


def parse_email_query(project_md: Path) -> dict:
    """Returns {'domains': set, 'addresses': set, 'names': set, 'raw': str}."""
    if not project_md.exists():
        return {"domains": set(), "addresses": set(), "names": set(), "raw": ""}
    text = project_md.read_text(errors="replace")
    m = re.search(r"^\s*-\s*\*\*Email search\*\*\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    raw = m.group(1).strip() if m else ""
    domains = set()
    addresses = set()
    names = set()
    if raw and "tbd" not in raw.lower():
        # from:domain.com OR to:domain.com
        for m in re.finditer(r"\b(?:from|to):([A-Za-z0-9._%+-]+(?:\.[A-Za-z]{2,})+)\b", raw):
            v = m.group(1).lower()
            if "@" in v:
                addresses.add(v)
            else:
                domains.add(v)
        # from:"Display Name" or to:"Display Name"
        for m in re.finditer(r'\b(?:from|to):"([^"]+)"', raw):
            names.add(m.group(1).strip().lower())
    return {"domains": domains, "addresses": addresses, "names": names, "raw": raw}


def extract_contacts_from_comms(comms: Path):
    """Yields (email, display_name, line_no, source_section)."""
    if not comms.exists():
        return
    current_section = None
    for line_no, raw_line in enumerate(comms.read_text(errors="replace").splitlines(), start=1):
        line = raw_line.strip()
        if line.startswith("## "):
            current_section = line.lstrip("# ").strip()
            continue
        if not line.startswith("- **From**:") and not line.startswith("- **To**:"):
            continue
        # Extract display name + email pairs
        for m in DISPLAY_NAME_RE.finditer(line):
            name = m.group(1).strip().rstrip(",")
            email = m.group(2).strip().lower()
            yield email, name, line_no, current_section
        # Standalone emails on the line not in <>
        for m in EMAIL_RE.finditer(line):
            email = m.group(0).lower()
            # Skip ones already captured via DISPLAY_NAME_RE
            if f"<{email}>" in line.lower():
                continue
            yield email, "", line_no, current_section


STRIPE_INTERNAL_DOMAINS = {"stripe.com", "professionalservices.stripe.com",
                           "alerts.stripe.com", "corp.stripe.com"}


def is_covered(email: str, name: str, query: dict) -> bool:
    if email in OUTBOUND_ADDRS:
        return True
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    # Stripe-internal addresses are intentionally not in merchant Email search queries.
    # They're not merchant contacts; skip silently.
    if domain in STRIPE_INTERNAL_DOMAINS or domain.endswith(".stripe.com"):
        return True
    if email in query["addresses"]:
        return True
    if domain and domain not in GENERIC_DOMAINS and domain in query["domains"]:
        return True
    # Generic-provider addresses still need name OR specific-address coverage
    if name and name.lower() in query["names"]:
        return True
    return False


def audit_slug(slug: str) -> dict:
    proj = ACTIVE_DIR / slug
    project_md = proj / "PROJECT.md"
    comms = proj / "raw" / "comms.md"
    if not comms.exists():
        return {"slug": slug, "skipped": "no raw/comms.md"}
    query = parse_email_query(project_md)
    if not query["raw"]:
        query_status = "MISSING"
    elif "tbd" in query["raw"].lower():
        query_status = "TBD"
    else:
        query_status = "OK"

    seen: dict[str, dict] = {}
    for email, name, line_no, section in extract_contacts_from_comms(comms):
        if email in seen:
            seen[email]["count"] += 1
            if name and not seen[email]["sample_name"]:
                seen[email]["sample_name"] = name
            continue
        seen[email] = {
            "email": email,
            "sample_name": name,
            "first_line": line_no,
            "first_section": section,
            "count": 1,
        }

    gaps = []
    for email, info in seen.items():
        if not is_covered(email, info["sample_name"], query):
            domain = email.rsplit("@", 1)[-1] if "@" in email else ""
            info["domain"] = domain
            info["domain_is_generic"] = domain in GENERIC_DOMAINS
            gaps.append(info)
    gaps.sort(key=lambda g: (-g["count"], g["email"]))

    return {
        "slug": slug,
        "query_status": query_status,
        "query_raw": query["raw"],
        "covered_domains": sorted(query["domains"]),
        "covered_names": sorted(query["names"]),
        "covered_addresses": sorted(query["addresses"]),
        "total_unique_addresses": len(seen),
        "gap_count": len(gaps),
        "gaps": gaps,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="restrict to one merchant")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-count", type=int, default=1,
                        help="only show gaps with at least N occurrences (default 1)")
    args = parser.parse_args()

    if not ACTIVE_DIR.exists():
        print(f"FATAL: {ACTIVE_DIR} missing", file=sys.stderr)
        sys.exit(2)

    slugs = ([args.slug] if args.slug else
             sorted(p.name for p in ACTIVE_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")))

    results = [audit_slug(s) for s in slugs]
    results = [r for r in results if not r.get("skipped")]
    for r in results:
        r["gaps"] = [g for g in r.get("gaps", []) if g["count"] >= args.min_count]

    if args.json:
        print(json.dumps(results, indent=2))
        sys.exit(1 if any(r["gap_count"] > 0 for r in results) else 0)

    print(f"# Contact Gap Audit\n_(min_count={args.min_count})_\n")
    total_gaps = 0
    for r in results:
        if r["gap_count"] == 0 and r["query_status"] == "OK":
            continue
        print(f"## {r['slug']} — query: {r['query_status']}, {r['gap_count']} gap(s) of {r['total_unique_addresses']} unique addresses")
        if r["query_status"] != "OK":
            print(f"  Email search query: `{r['query_raw'] or '(empty)'}`")
        if r["query_status"] == "OK":
            print(f"  Covered: domains={r['covered_domains']}, names={r['covered_names']}, addrs={r['covered_addresses']}")
        for g in r["gaps"][:20]:
            tag = " (generic provider — needs name+address line)" if g["domain_is_generic"] else " (domain not in query)"
            name = f' "{g["sample_name"]}"' if g["sample_name"] else ""
            print(f"  - {g['email']}{name} × {g['count']}{tag}")
            print(f"      first seen: {g['first_section']} (line {g['first_line']})")
        if len(r["gaps"]) > 20:
            print(f"  ... and {len(r['gaps']) - 20} more")
        print()
        total_gaps += r["gap_count"]

    print(f"Total: {total_gaps} gap(s) across {len(results)} merchant(s)")
    sys.exit(1 if total_gaps > 0 else 0)


if __name__ == "__main__":
    main()
