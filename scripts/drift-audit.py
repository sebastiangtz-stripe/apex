#!/usr/bin/env python3
"""
Workspace drift audit. Surfaces inconsistencies before they cause hallucinations
or rotted state. Read-only: prints a structured report, never modifies files.

Checks:
  A. INDEX vs filesystem
     - Slugs in INDEX.md not found in projects/active/ (listed-but-missing)
     - Slugs in projects/archive/ that appear in INDEX.md (archived-but-listed)
     - Slugs in projects/active/ missing from INDEX.md (orphan active project)
  B. INDEX freshness
     - Days since "Last reconciliation" header
  C. Slug collisions
     - Same merchant likely under multiple slugs (fuzzy name match across folders)
     - Same Hubble project_id referenced by multiple local hubble.json files
  D. PROJECT.md hygiene
     - Email search query is "TBD" despite Key Contacts populated
     - Status field missing or empty
     - Priority field missing or empty
     - Account ID field empty AND no Account Manifest URL
  E. scan-state.json sanity
     - Missing entirely (project has raw/comms.md but no scan-state.json)
     - last_*_scan timestamp is in the future
     - Duplicate entries in logged_email_ids or logged_slack_thread_ids
     - last_email_scan or last_slack_scan older than 14 days

Usage:
  python3 scripts/drift-audit.py
  python3 scripts/drift-audit.py --json     # machine-readable
  python3 scripts/drift-audit.py --section A,C,E   # subset

Exit codes:
  0 — no drift found
  1 — drift found (any severity)
  2 — fatal error (missing dirs)
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"
ARCHIVE_DIR = WORKSPACE_ROOT / "projects" / "archive"
INDEX_PATH = WORKSPACE_ROOT / "projects" / "INDEX.md"

STOPWORDS = {"the", "co", "us", "inc", "llc", "corp", "ltd",
             "payments", "connect", "billing", "tax", "checkout",
             "standard", "express", "platform"}


def _norm_tokens(text: str) -> set[str]:
    s = re.sub(r"\[.*?\]|\(.*?\)|\{.*?\}", " ", text)
    s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    return {t for t in s.split() if t and t not in STOPWORDS and len(t) > 2}


# ── A: INDEX vs filesystem ──

def audit_index_filesystem(active_slugs: set[str], archive_slugs: set[str]) -> dict:
    findings = {"listed_but_missing": [], "archived_but_listed": [], "orphan_active": []}
    if not INDEX_PATH.exists():
        return {"error": f"{INDEX_PATH} does not exist", **findings}
    text = INDEX_PATH.read_text()
    # Pattern: [Display](active/<slug>/PROJECT.md)
    listed = set(re.findall(r"\(active/([^/)]+)/PROJECT\.md\)", text))
    for slug in sorted(listed - active_slugs):
        findings["listed_but_missing"].append(slug)
    for slug in sorted(listed & archive_slugs):
        findings["archived_but_listed"].append(slug)
    for slug in sorted(active_slugs - listed):
        findings["orphan_active"].append(slug)
    return findings


# ── B: INDEX freshness ──

def audit_index_freshness() -> dict:
    if not INDEX_PATH.exists():
        return {"error": "INDEX.md missing"}
    text = INDEX_PATH.read_text()
    m = re.search(r"Last reconciliation\**:\s*(\d{4}-\d{2}-\d{2})", text)
    if not m:
        return {"error": "no Last reconciliation header found"}
    last = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()
    days = (today - last).days
    return {"last_reconciliation": m.group(1), "days_stale": days,
            "stale": days > 3}


# ── C: Slug collisions ──

def _h1_title(slug: str) -> str:
    p = ACTIVE_DIR / slug / "PROJECT.md"
    if not p.exists():
        return ""
    m = re.search(r"^#\s+(.+?)\s*$", p.read_text(errors="replace"), re.MULTILINE)
    return m.group(1).strip() if m else ""


def _compressed(text: str) -> str:
    """Lowercase + strip all non-alphanumeric — to catch fused-word duplicates."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def audit_slug_collisions(active_slugs: list[str]) -> dict:
    findings = {"name_collisions": [], "hubble_pid_collisions": []}

    # Build token + compressed forms from slug + H1 title combined
    slug_tokens: dict[str, set[str]] = {}
    slug_compressed: dict[str, str] = {}
    for s in active_slugs:
        title = _h1_title(s)
        combined = (s.replace("-", " ") + " " + title).strip()
        slug_tokens[s] = _norm_tokens(combined)
        slug_compressed[s] = _compressed(combined)

    pairs_seen = set()
    for i, s1 in enumerate(active_slugs):
        t1 = slug_tokens[s1]
        c1 = slug_compressed[s1]
        for s2 in active_slugs[i + 1:]:
            t2 = slug_tokens[s2]
            c2 = slug_compressed[s2]
            overlap = t1 & t2
            min_required = 1 if (len(t1) <= 2 or len(t2) <= 2) else 2
            token_match = len(overlap) >= min_required
            # Substring match on the compressed (no-whitespace) representation
            # Catches e.g. "acmeglass" ⊂ "acmeautoglass" via shared "acmeglass"
            # Substrings that look "shared" but are just industry boilerplate.
            # If the longest common substring is one of these, ignore it.
            BORING_FRAGMENTS = {
                "technolog", "technology", "technologies", "platform", "platforms",
                "payments", "billing", "connect", "checkout", "standard",
                "express", "express", "industries", "international", "integration",
                "softwar", "software", "solutions", "services", "ventures",
            }
            substring_match = False
            shared_substr = ""
            if c1 and c2:
                # Find the longest common substring of length >= 6
                for length in range(min(len(c1), len(c2)), 5, -1):
                    found = False
                    for start in range(len(c1) - length + 1):
                        sub = c1[start:start + length]
                        if sub in c2:
                            if any(sub in b or b in sub for b in BORING_FRAGMENTS):
                                continue
                            substring_match = True
                            shared_substr = sub
                            found = True
                            break
                    if found:
                        break
            if not (token_match or substring_match):
                continue
            pair = (s1, s2)
            if pair in pairs_seen:
                continue
            pairs_seen.add(pair)
            evidence = {
                "slug_a": s1, "slug_b": s2,
                "title_a": _h1_title(s1), "title_b": _h1_title(s2),
            }
            if overlap:
                evidence["shared_tokens"] = sorted(overlap)
            if substring_match:
                evidence["shared_substring"] = shared_substr
            findings["name_collisions"].append(evidence)

    # Hubble project_id collisions
    pid_to_slugs: dict[int, list[str]] = defaultdict(list)
    for slug in active_slugs:
        h = ACTIVE_DIR / slug / "hubble.json"
        if not h.exists():
            continue
        try:
            pid = json.loads(h.read_text()).get("project_id")
            if pid is not None:
                pid_to_slugs[int(pid)].append(slug)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    for pid, slugs in pid_to_slugs.items():
        if len(slugs) > 1:
            findings["hubble_pid_collisions"].append({"project_id": pid, "slugs": sorted(slugs)})

    return findings


# ── D: PROJECT.md hygiene ──

CONTACT_LINE_RE = re.compile(r"^- ", re.MULTILINE)


def audit_project_hygiene(active_slugs: list[str]) -> dict:
    findings = {
        "tbd_email_query_with_contacts": [],
        "missing_status": [],
        "missing_priority": [],
        "no_account_id_or_manifest": [],
    }
    for slug in active_slugs:
        p = ACTIVE_DIR / slug / "PROJECT.md"
        if not p.exists():
            findings["missing_status"].append({"slug": slug, "detail": "PROJECT.md missing"})
            findings["missing_priority"].append({"slug": slug, "detail": "PROJECT.md missing"})
            continue
        text = p.read_text(encoding="utf-8", errors="replace")

        def field(name: str) -> str:
            m = re.search(rf"^\s*-\s*\*\*{re.escape(name)}\*\*\s*:\s*(.+?)\s*$",
                          text, re.MULTILINE)
            return (m.group(1).strip() if m else "")

        status = field("Status")
        priority = field("Priority")
        account_id = field("Account ID(s)") or field("Account ID")
        email_search = field("Email search")

        if not status:
            findings["missing_status"].append({"slug": slug})
        if not priority:
            findings["missing_priority"].append({"slug": slug})

        no_acct = not account_id or "TBD" in account_id.upper()
        no_manifest = "account-manifest" not in text.lower()
        if no_acct and no_manifest:
            findings["no_account_id_or_manifest"].append({"slug": slug})

        # TBD email query with contacts populated
        if email_search and "tbd" in email_search.lower():
            # Check Key Contacts section
            kc_match = re.search(r"## Key Contacts\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
            if kc_match:
                contact_lines = [l for l in kc_match.group(1).splitlines()
                                 if l.strip().startswith("- ") and "@" in l]
                if contact_lines:
                    findings["tbd_email_query_with_contacts"].append({
                        "slug": slug,
                        "contact_count": len(contact_lines),
                    })

    return findings


# ── E: scan-state.json sanity ──

def audit_scan_state(active_slugs: list[str]) -> dict:
    findings = {
        "missing_scan_state": [],
        "future_timestamp": [],
        "duplicate_logged_ids": [],
        "stale_scan": [],
    }
    now = datetime.now(timezone.utc)
    for slug in active_slugs:
        sp = ACTIVE_DIR / slug / "scan-state.json"
        comms = ACTIVE_DIR / slug / "raw" / "comms.md"
        if not sp.exists():
            if comms.exists() and comms.stat().st_size > 100:
                findings["missing_scan_state"].append({"slug": slug})
            continue
        try:
            data = json.loads(sp.read_text())
        except json.JSONDecodeError as e:
            findings["future_timestamp"].append({"slug": slug, "detail": f"unparseable: {e}"})
            continue

        for key in ("last_email_scan", "last_slack_scan"):
            v = data.get(key)
            if not v:
                continue
            try:
                ts = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                findings["future_timestamp"].append({"slug": slug, "field": key, "value": v,
                                                     "detail": "unparseable timestamp"})
                continue
            if ts > now + timedelta(hours=1):
                findings["future_timestamp"].append({"slug": slug, "field": key, "value": v})
            elif (now - ts) > timedelta(days=14):
                findings["stale_scan"].append({"slug": slug, "field": key,
                                               "days_stale": (now - ts).days})

        for arr_key in ("logged_email_ids", "logged_slack_thread_ids"):
            arr = data.get(arr_key, []) or []
            if not isinstance(arr, list):
                continue
            counts = Counter(arr)
            dupes = {k: v for k, v in counts.items() if v > 1}
            if dupes:
                findings["duplicate_logged_ids"].append({
                    "slug": slug, "field": arr_key,
                    "duplicates": dict(list(dupes.items())[:5]),
                    "total_dupe_count": len(dupes),
                })

    return findings


# ── Render ──

def render_text(audit: dict) -> tuple[str, int]:
    """Returns (rendered, drift_count)."""
    out = ["# Workspace Drift Audit", f"_(generated {datetime.now().isoformat(timespec='seconds')})_", ""]
    drift_count = 0

    a = audit.get("A", {})
    out.append("## A. INDEX.md vs filesystem")
    if a.get("error"):
        out.append(f"  ERROR: {a['error']}")
        drift_count += 1
    else:
        for key, label in [
            ("archived_but_listed", "Archived projects still listed in INDEX.md (CRITICAL — Apr 11 leak pattern)"),
            ("listed_but_missing", "Slugs in INDEX.md with no projects/active/ folder"),
            ("orphan_active", "Active projects missing from INDEX.md (run /index-reconciler)"),
        ]:
            items = a.get(key, [])
            if items:
                out.append(f"  - {label}: {', '.join(items)}")
                drift_count += len(items)
        if not any(a.get(k) for k in ("archived_but_listed", "listed_but_missing", "orphan_active")):
            out.append("  OK")
    out.append("")

    b = audit.get("B", {})
    out.append("## B. INDEX.md freshness")
    if b.get("error"):
        out.append(f"  ERROR: {b['error']}")
        drift_count += 1
    else:
        last = b.get("last_reconciliation")
        days = b.get("days_stale", 0)
        if b.get("stale"):
            out.append(f"  STALE: last reconciliation {last} ({days} days ago) — run /index-reconciler")
            drift_count += 1
        else:
            out.append(f"  OK: last reconciliation {last} ({days} days ago)")
    out.append("")

    c = audit.get("C", {})
    out.append("## C. Slug collisions")
    name_col = c.get("name_collisions", [])
    pid_col = c.get("hubble_pid_collisions", [])
    if name_col:
        out.append("  Possible duplicate merchants (fuzzy name match):")
        for col in name_col:
            evidence = []
            if col.get("shared_tokens"):
                evidence.append(f"shared tokens: {', '.join(col['shared_tokens'])}")
            if col.get("shared_substring"):
                evidence.append(f"shared substring: '{col['shared_substring']}'")
            ev = "; ".join(evidence)
            out.append(f"    - `{col['slug_a']}` (\"{col.get('title_a','')}\") vs `{col['slug_b']}` (\"{col.get('title_b','')}\") — {ev}")
        drift_count += len(name_col)
    if pid_col:
        out.append("  Same Hubble project_id under multiple slugs (CRITICAL):")
        for col in pid_col:
            out.append(f"    - project_id {col['project_id']}: {', '.join(col['slugs'])}")
        drift_count += len(pid_col)
    if not (name_col or pid_col):
        out.append("  OK")
    out.append("")

    d = audit.get("D", {})
    out.append("## D. PROJECT.md hygiene")
    section_d_total = 0
    for key, label in [
        ("tbd_email_query_with_contacts", "TBD Email search with Key Contacts populated"),
        ("missing_status", "Missing Status field"),
        ("missing_priority", "Missing Priority field"),
        ("no_account_id_or_manifest", "No Account ID and no Account Manifest URL"),
    ]:
        items = d.get(key, [])
        if items:
            slugs = [it.get("slug") for it in items]
            out.append(f"  - {label} ({len(items)}): {', '.join(slugs)}")
            section_d_total += len(items)
    if not section_d_total:
        out.append("  OK")
    drift_count += section_d_total
    out.append("")

    e = audit.get("E", {})
    out.append("## E. scan-state.json sanity")
    section_e_total = 0
    for key, label in [
        ("missing_scan_state", "Missing scan-state.json (project has raw/comms.md but no state)"),
        ("future_timestamp", "Future or unparseable scan timestamp"),
        ("duplicate_logged_ids", "Duplicate IDs in logged_email_ids / logged_slack_thread_ids"),
        ("stale_scan", "Last scan >14 days old"),
    ]:
        items = e.get(key, [])
        if items:
            out.append(f"  - {label} ({len(items)}):")
            for it in items[:10]:
                out.append(f"      {json.dumps(it, default=str)}")
            if len(items) > 10:
                out.append(f"      ... and {len(items) - 10} more")
            section_e_total += len(items)
    if not section_e_total:
        out.append("  OK")
    drift_count += section_e_total
    out.append("")

    out.append(f"## Summary: {drift_count} drift item(s) found")
    return "\n".join(out), drift_count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--section", help="comma-separated subset, e.g. A,C,E")
    args = parser.parse_args()

    if not ACTIVE_DIR.exists():
        print(f"FATAL: {ACTIVE_DIR} does not exist", file=sys.stderr)
        sys.exit(2)

    active_slugs = sorted(p.name for p in ACTIVE_DIR.iterdir()
                          if p.is_dir() and not p.name.startswith("."))
    archive_slugs = (sorted(p.name for p in ARCHIVE_DIR.iterdir()
                            if p.is_dir() and not p.name.startswith("."))
                     if ARCHIVE_DIR.exists() else [])

    requested = set(args.section.upper().split(",")) if args.section else {"A", "B", "C", "D", "E"}
    audit = {}
    if "A" in requested:
        audit["A"] = audit_index_filesystem(set(active_slugs), set(archive_slugs))
    if "B" in requested:
        audit["B"] = audit_index_freshness()
    if "C" in requested:
        audit["C"] = audit_slug_collisions(active_slugs)
    if "D" in requested:
        audit["D"] = audit_project_hygiene(active_slugs)
    if "E" in requested:
        audit["E"] = audit_scan_state(active_slugs)

    if args.json:
        print(json.dumps(audit, indent=2, default=str))
        # Determine drift count from the audit dict for exit code
        _, drift_count = render_text(audit)
        sys.exit(1 if drift_count > 0 else 0)
    else:
        rendered, drift_count = render_text(audit)
        print(rendered)
        sys.exit(1 if drift_count > 0 else 0)


if __name__ == "__main__":
    main()
