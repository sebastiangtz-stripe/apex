#!/usr/bin/env python3
from __future__ import annotations
"""
Classify handover proposals against the consultant's roster.

The handover-scanner reads the handover channel by ID and parses every
handover-shaped thread (see handover-scanner.md). This script decides which of
those proposals belong to *this* consultant by matching them against the roster
(Hubble snapshot = my assigned projects; projects/active/* = already bootstrapped).

Match order, per proposal (first hit wins):
  1. SFDC opportunity id   — exact (15-char prefix) against the Hubble row's
     sfdc_opp_link / sfdc_opp_id.
  2. Normalized name       — name_similarity(merchant, hubble project_name) >= 0.6.
  3. Contact email domain  — a merchant-domain email in the thread matches the
     Hubble row's primary_contact_email domain (generic providers excluded).
     Recovers the legacy "manifest review" handovers that carry a Salesforce
     account id (not a 006 opp) and no clean merchant name.

Matched proposals are returned with the canonical merchant_name (from Hubble) and
a resolved slug. Anything handover-shaped that matches no roster row is returned
in `triage` — never silently dropped — so the consultant can decide.

Two modes:
  (default)   thread -> roster classification. Used by the daily handover-scanner
              on threads since last_scan: matched -> proposals, unmatched -> triage.
  --coverage  roster -> thread coverage. Used by the one-time setup BACKFILL: feed
              it every thread from a wide channel sweep and it reports, for each of
              the N roster projects, whether a handover thread was found (and which)
              or is missing. Same matcher, inverted grouping.

Usage:
  echo '<proposals json array>' | python3 scripts/handover-match.py --proposals-stdin
  echo '<proposals json array>' | python3 scripts/handover-match.py --proposals-stdin --coverage

Exit codes:
  0  classified (emitted to stdout) — always, on any input
  2  malformed JSON on stdin
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _name_match import clean_project_name, name_similarity, slugify  # noqa: E402

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"
SNAPSHOT_PATH = WORKSPACE_ROOT / "data" / "hubble-snapshot.json"

NAME_MATCH_THRESHOLD = 0.6
OPP_ID_RE = re.compile(r"(006[A-Za-z0-9]{12,15})")
GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "icloud.com", "hotmail.com", "outlook.com", "yahoo.com",
    "stripe.com",
}


def email_domain(email: str | None) -> str | None:
    """Lowercased domain of an email, or None for blank/generic providers."""
    if not email or "@" not in email:
        return None
    d = email.rsplit("@", 1)[-1].strip().lower()
    return d if d and d not in GENERIC_EMAIL_DOMAINS else None


def opp_key(opp_id: str | None) -> str | None:
    """Normalize an SFDC opp id for comparison (15-char prefix, lowercased).

    SFDC 18-char ids are the 15-char id + a 3-char case-insensitive checksum, so
    the 15-char prefix uniquely identifies the opportunity."""
    if not opp_id:
        return None
    m = OPP_ID_RE.search(opp_id)
    if m:
        return m.group(1)[:15].lower()
    # Bare id that the regex missed (e.g. a short test id); never fall back to
    # slicing a URL, which would yield "https://stripe…".
    s = opp_id.strip()
    if s.lower().startswith("006") and s.replace("_", "").isalnum():
        return s[:15].lower()
    return None


def load_hubble_rows(snapshot_path: Path = SNAPSHOT_PATH) -> list[dict]:
    if not snapshot_path.exists():
        return []
    try:
        snap = json.loads(snapshot_path.read_text())
        return snap.get("projects", []) or []
    except (json.JSONDecodeError, ValueError):
        return []


def active_slug_by_project_id() -> dict[int, str]:
    """Map Hubble project_id -> existing active slug (via each folder's hubble.json)."""
    out: dict[int, str] = {}
    if not ACTIVE_DIR.exists():
        return out
    for child in ACTIVE_DIR.iterdir():
        hj = child / "hubble.json"
        if not hj.exists():
            continue
        try:
            pid = json.loads(hj.read_text()).get("project_id")
            if pid is not None:
                out[int(pid)] = child.name
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return out


def match_proposal(prop: dict, hubble_rows: list[dict], active_by_pid: dict[int, str]) -> dict:
    """Return the proposal annotated with match fields."""
    prop_opp = opp_key(prop.get("sfdc_opp_id"))
    merchant = prop.get("merchant_name") or ""

    best_row: dict | None = None
    method: str | None = None
    score = 0.0

    # 1. SFDC opp id — exact
    if prop_opp:
        for row in hubble_rows:
            if opp_key(row.get("sfdc_opp_link") or row.get("sfdc_opp_id")) == prop_opp:
                best_row, method, score = row, "sfdc", 1.0
                break

    # 2. Normalized name — best similarity above threshold
    if best_row is None and merchant:
        for row in hubble_rows:
            s = name_similarity(merchant, row.get("project_name", ""))
            if s > score:
                best_row, score = row, s
        if best_row is not None and score >= NAME_MATCH_THRESHOLD:
            method = "name"
        else:
            best_row, method, score = None, None, 0.0

    # 3. Contact email domain — the thread carries a merchant-domain email that
    # matches the roster row's primary_contact_email domain. Recovers handovers
    # that use a Salesforce account id / different opp and have no clean name.
    if best_row is None:
        prop_domains = {d.lower() for d in (prop.get("email_domains") or [])}
        if prop_domains:
            for row in hubble_rows:
                rdom = email_domain(row.get("primary_contact_email"))
                if rdom and rdom in prop_domains:
                    best_row, method, score = row, "email", 0.9
                    break

    annotated = dict(prop)
    if best_row is not None:
        canonical = clean_project_name(best_row.get("project_name", "")) or merchant
        pid = best_row.get("project_id")
        try:
            pid = int(pid) if pid is not None else None
        except (ValueError, TypeError):
            pid = None
        resolved_slug = active_by_pid.get(pid) if pid is not None else None
        annotated.update({
            "matched": True,
            "match_method": method,
            "match_score": round(score, 3),
            "matched_project_id": pid,
            "merchant_name": canonical,
            "slug": resolved_slug or slugify(canonical),
        })
    else:
        annotated.update({
            "matched": False,
            "match_method": None,
            "match_score": round(score, 3),
            "triage_reason": "no roster match (SFDC opp + name)"
            if merchant else "no merchant name / SFDC opp to match on",
        })
    return annotated


def roster_coverage(proposals: list[dict], hubble_rows: list[dict],
                    active_by_pid: dict[int, str]) -> dict:
    """Invert the matcher: for each roster project, did any thread match it?

    Reuses match_proposal (thread -> project) and groups the hits by project, then
    diffs against the full roster to surface the misses. This is the backfill view.
    """
    by_pid: dict[int, list[dict]] = {}
    for prop in proposals:
        a = match_proposal(prop, hubble_rows, active_by_pid)
        if a["matched"] and a.get("matched_project_id") is not None:
            by_pid.setdefault(a["matched_project_id"], []).append(a)

    covered, missing = [], []
    for row in hubble_rows:
        try:
            pid = int(row.get("project_id"))
        except (TypeError, ValueError):
            pid = None
        name = clean_project_name(row.get("project_name", "")) or row.get("project_name")
        hits = by_pid.get(pid, [])
        if hits:
            best = max(hits, key=lambda h: h.get("match_score", 0.0))
            covered.append({
                "project_id": pid,
                "merchant_name": name,
                "slug": best.get("slug"),
                "match_method": best.get("match_method"),
                "sfdc_opp_id": best.get("sfdc_opp_id"),
                "thread_permalink": best.get("thread_permalink"),
            })
        else:
            missing.append({"project_id": pid, "merchant_name": name})

    return {
        "covered": covered,
        "missing": missing,
        "counts": {
            "roster": len(hubble_rows),
            "covered": len(covered),
            "missing": len(missing),
            "threads_in": len(proposals),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--proposals-stdin", action="store_true",
                    help="Read a JSON array of proposals from stdin.")
    ap.add_argument("--coverage", action="store_true",
                    help="Backfill mode: report per-roster-project coverage "
                         "(covered/missing) instead of thread-level matched/triage.")
    ap.add_argument("--snapshot", default=None,
                    help="Override path to the Hubble snapshot (defaults to "
                         "data/hubble-snapshot.json). Mainly for tests.")
    args = ap.parse_args()

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed JSON on stdin: {e}", file=sys.stderr)
        sys.exit(2)

    proposals = payload if isinstance(payload, list) else [payload]
    hubble_rows = load_hubble_rows(Path(args.snapshot) if args.snapshot else SNAPSHOT_PATH)
    active_by_pid = active_slug_by_project_id()

    if args.coverage:
        result = roster_coverage(proposals, hubble_rows, active_by_pid)
        if not hubble_rows:
            result["warning"] = (
                "hubble-snapshot.json missing or empty — no roster to compute "
                "coverage against. Refresh Hubble and re-run."
            )
        print(json.dumps(result, indent=2))
        return

    matched, triage = [], []
    for prop in proposals:
        annotated = match_proposal(prop, hubble_rows, active_by_pid)
        (matched if annotated["matched"] else triage).append(annotated)

    result = {
        "matched": matched,
        "triage": triage,
        "counts": {
            "input": len(proposals),
            "matched": len(matched),
            "triage": len(triage),
            "hubble_rows": len(hubble_rows),
        },
    }
    if not hubble_rows:
        result["warning"] = (
            "hubble-snapshot.json missing or empty — no roster to match against; "
            "all candidates routed to triage. Refresh Hubble and re-run."
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
