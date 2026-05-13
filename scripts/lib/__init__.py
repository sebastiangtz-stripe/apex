"""
Shared utilities for Accelerate Assistant scripts.

Centralizes identity model parsing, email query parsing, environment loading,
and fuzzy matching so that ingest-comms.py, cross-merchant-audit.py,
contact-gap-audit.py, and apply-proposals.py all use the same logic.
"""

import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECTS_DIR = WORKSPACE_ROOT / "projects" / "active"
ENV_FILE = WORKSPACE_ROOT / ".env"

# ── Constants ──────────────────────────────────────────────────────────────

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
    "msn.com",
    "mac.com",
}

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

STRIPE_INTERNAL = {"stripe.com"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ENTRY_HEADER_RE = re.compile(r"^## \[?(\d{4}-\d{2}-\d{2})\]?\s+—\s+([^\s—]+)\s+—\s+(.*)$")
FROM_RE = re.compile(r"^\*\*From\*\*:\s*(.+)$")
TO_RE = re.compile(r"^\*\*To\*\*:\s*(.+)$")


# ── Environment ────────────────────────────────────────────────────────────

def load_env(env_file=None):
    """Load .env file into a dict. Skips comments and blank lines."""
    path = Path(env_file) if env_file else ENV_FILE
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


# ── Email / Identity helpers ───────────────────────────────────────────────

def emails_in(text):
    """Extract all email addresses from a text string."""
    return [m.group(0).lower() for m in EMAIL_RE.finditer(text or "")]


def is_automated(addr):
    """True if the address is from an automated notification sender."""
    if not addr or "@" not in addr:
        return False
    domain = addr.split("@", 1)[1].lower()
    if domain in AUTOMATED_DOMAINS:
        return True
    local = addr.split("@", 1)[0].lower()
    if local.startswith("noreply") or local.startswith("no-reply") or local.startswith("workspace+"):
        return True
    return False


def is_stripe_internal(domain):
    """True if domain is stripe.com or a subdomain (jira.stripe.com, etc.)."""
    if not domain:
        return False
    domain = domain.lower()
    for s in STRIPE_INTERNAL:
        if domain == s or domain.endswith("." + s):
            return True
    return False


def parse_email_search(text):
    """Extract domains and explicit addresses from an Email search query string.

    Returns (domains: set[str], explicit_addresses: set[str]).
    """
    domains = set()
    explicit = set()
    for m in re.finditer(r"(?:from|to):([A-Za-z0-9.\-_]+\.[A-Za-z]{2,})", text):
        d = m.group(1).lower()
        if d not in GENERIC_DOMAINS:
            domains.add(d)
    for m in re.finditer(r'(?:from|to):"([^"]+)"', text):
        explicit.add(m.group(1).lower())
    for m in re.finditer(r"(?:from|to):([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text):
        addr = m.group(1).lower()
        explicit.add(addr)
    return domains, explicit


def parse_project(slug, projects_dir=None):
    """Extract the merchant identity model from PROJECT.md.

    Returns dict with: slug, name, domains, explicit_addresses,
    key_contact_addresses, name_tokens. Returns None if PROJECT.md missing.
    """
    base = Path(projects_dir) if projects_dir else PROJECTS_DIR
    path = base / slug / "PROJECT.md"
    if not path.exists():
        return None
    text = path.read_text()
    lines = text.splitlines()
    name = lines[0].lstrip("# ").strip() if lines else slug

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
        "email_query_raw": email_query.strip(),
    }


def matches_merchant(addr, identity):
    """True if addr is allowed under the merchant's identity model.

    Stripe-internal addresses always match. For everything else, requires
    match in domains, explicit address allowlist, or key-contact set.
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


def is_outbound(from_field, outbound_addresses):
    """True if the from field matches any of the outbound addresses.

    Handles display-name wrapping like 'Accelerate Core <accelerate@stripe.com>'.
    """
    if not from_field:
        return False
    from_lower = from_field.lower()
    for addr in outbound_addresses:
        if addr.lower() in from_lower:
            return True
    return False


# ── Normalization + fuzzy matching ─────────────────────────────────────────

def normalize(text):
    """Normalize an action item description for fuzzy comparison.

    Strips tags, metadata fields, punctuation, and collapses whitespace.
    """
    if not text:
        return ""
    s = re.sub(r"#\w+\s*", "", text)
    s = re.sub(r"\s*—\s*(Complexity|Owner|Due|Source|Completed):.*", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    return s


def fuzzy_match(a, b, threshold=0.6):
    """True if normalized strings a and b share enough word overlap."""
    if not a or not b:
        return False
    if a == b:
        return True
    aw, bw = set(a.split()), set(b.split())
    if not aw or not bw:
        return False
    overlap = aw & bw
    smaller = min(len(aw), len(bw))
    return len(overlap) / smaller >= threshold
