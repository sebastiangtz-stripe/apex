#!/usr/bin/env python3
"""
Regenerate projects/INDEX.md from filesystem + per-project PROJECT.md + Hubble snapshot.

Replaces the hand-maintained INDEX.md (which rotted between 2026-04-11 and 2026-05-07).

Behavior:
  - Reads every projects/active/<slug>/PROJECT.md (Priority, Status, Products, Due, AONR).
  - Groups by Priority: High → Medium → Low → Unspecified.
  - Computes Flag column from Due relative to today (user's local TZ via system clock).
  - Detects projects/archive/<slug> entries — never listed in active sections (eliminates the
    the historical archived-but-listed-as-active class of leaks).
  - Optional Hubble cross-check: surfaces any active/<slug> not present in the snapshot
    (archive candidate) and any Hubble In Progress row not present locally (new project).
  - Writes projects/INDEX.md with `Last reconciliation: YYYY-MM-DD` header.

Usage:
  python3 scripts/regenerate-index.py
  python3 scripts/regenerate-index.py --dry-run        # print to stdout, don't write
  python3 scripts/regenerate-index.py --no-hubble      # skip the Hubble cross-check section

Exit codes:
  0 success
  1 fatal error (e.g. cannot read projects/active/)
"""

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"
ARCHIVE_DIR = WORKSPACE_ROOT / "projects" / "archive"
INDEX_PATH = WORKSPACE_ROOT / "projects" / "INDEX.md"
HUBBLE_PATH = WORKSPACE_ROOT / "data" / "hubble-snapshot.json"


# ── Field extraction from PROJECT.md ──

# Field: any of these layouts appear across the workspace:
#   - **Status**: Integration
#   - **Status**:  Integration  (extra spaces)
#   - **Status**: Integration — note (we strip trailing notes after first em-dash optionally)
FIELD_RE = re.compile(r"^\s*-\s*\*\*([A-Za-z][^*]+?)\*\*\s*:\s*(.+?)\s*$", re.MULTILINE)


def parse_project_md(path: Path) -> dict:
    """Extract Overview fields from a PROJECT.md. Returns dict with normalized keys."""
    fields: dict = {}
    if not path.exists():
        return fields
    text = path.read_text(encoding="utf-8", errors="replace")

    # Take the first H1 as the display name; fall back to slug.
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        fields["name"] = m.group(1).strip()

    for m in FIELD_RE.finditer(text):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        # Normalize key
        if key.startswith("account id"):
            fields["account_id"] = val
        elif key == "products":
            fields["products"] = val
        elif key == "status":
            fields["status"] = val
        elif key == "priority":
            fields["priority"] = val
        elif key == "started":
            fields["started"] = val
        elif key == "due":
            fields["due"] = val
        elif key == "aonr":
            fields["aonr"] = val
        elif key.startswith("sfdc opportunity owner") or key == "ae":
            fields["ae"] = val
    return fields


# ── Date / flag helpers ──

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_due(due_raw: str):
    """Return (date|None, raw_display)."""
    if not due_raw:
        return None, "TBD"
    raw = due_raw.strip()
    m = DATE_RE.search(raw)
    if not m:
        return None, raw  # e.g. "TBD", "TBD (resolve P0 → archive)"
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), raw
    except ValueError:
        return None, raw


def compute_flag(due_date, status: str | None) -> str:
    """Return the Flag cell content based on Due vs today and Status."""
    status_lc = (status or "").lower()
    if "on hold" in status_lc:
        return "On Hold"
    if "p0" in status_lc:
        return f"**P0** — {status}"
    if due_date is None:
        return ""
    today = date.today()
    delta = (due_date - today).days
    if delta < 0:
        return f"OVERDUE ({-delta}d)"
    if delta == 0:
        return "Due today"
    if delta == 1:
        return "Due tomorrow"
    if delta <= 7:
        return f"Due in {delta}d"
    return ""


# ── Priority bucketing ──

PRIORITY_ORDER = ["High", "Medium", "Low", "Unspecified"]


def normalize_priority(p: str | None) -> str:
    if not p:
        return "Unspecified"
    p = p.strip().lower()
    if "high" in p:
        return "High"
    if "med" in p:
        return "Medium"
    if "low" in p:
        return "Low"
    return "Unspecified"


# ── Hubble cross-check (optional) ──

def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s


def load_hubble_in_progress():
    if not HUBBLE_PATH.exists():
        return None
    try:
        snap = json.loads(HUBBLE_PATH.read_text())
    except json.JSONDecodeError:
        return None
    return [p for p in snap.get("projects", []) if p.get("project_status") == "In Progress"]


STOPWORDS = {"the", "co", "us", "inc", "llc", "corp", "ltd", "io",
             "payments", "connect", "billing", "tax", "checkout",
             "standard", "express", "platform", "labs", "health"}


def _norm_tokens(text: str) -> set[str]:
    s = re.sub(r"\[.*?\]|\(.*?\)|\{.*?\}", " ", text)
    s = re.sub(r"\$[0-9.,]+[kmKMbB]?", " ", s)
    s = re.sub(r"[0-9]+%?", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    return {t for t in s.split() if t and t not in STOPWORDS and len(t) > 1}


def hubble_cross_check(active_slugs: list[str]) -> dict:
    rows = load_hubble_in_progress()
    if rows is None:
        return {"available": False}
    snap_meta = json.loads(HUBBLE_PATH.read_text())

    # Step 1: authoritative match via per-project hubble.json project_id
    local_pid_to_slug: dict[int, str] = {}
    pid_unknown_slugs: list[str] = []
    for slug in active_slugs:
        h = ACTIVE_DIR / slug / "hubble.json"
        if h.exists():
            try:
                pid = json.loads(h.read_text()).get("project_id")
                if pid is not None:
                    local_pid_to_slug[int(pid)] = slug
                    continue
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        pid_unknown_slugs.append(slug)

    matched_pids: set[int] = set()
    matched_slugs: set[str] = set()

    for row in rows:
        pid = row.get("project_id")
        if pid is not None and int(pid) in local_pid_to_slug:
            matched_pids.add(int(pid))
            matched_slugs.add(local_pid_to_slug[int(pid)])

    # Step 2: fuzzy match for slugs lacking hubble.json (or whose project_id missed)
    unmatched_rows = [r for r in rows if int(r.get("project_id") or -1) not in matched_pids]

    for slug in pid_unknown_slugs:
        if slug in matched_slugs:
            continue
        slug_tokens = _norm_tokens(slug.replace("-", " "))
        if not slug_tokens:
            continue
        for row in unmatched_rows:
            hub_tokens = _norm_tokens(row.get("project_name", ""))
            if slug_tokens & hub_tokens:
                matched_slugs.add(slug)
                pid = row.get("project_id")
                if pid is not None:
                    matched_pids.add(int(pid))
                break

    archive_candidates = sorted(s for s in active_slugs if s not in matched_slugs)
    new_projects = [
        {
            "project_name": r.get("project_name"),
            "project_id": r.get("project_id"),
            "ae": r.get("account_executive"),
            "aonr": r.get("sfdc_aonr"),
        }
        for r in rows if int(r.get("project_id") or -1) not in matched_pids
    ]

    return {
        "available": True,
        "fetched_at": snap_meta.get("fetched_at"),
        "archive_candidates": archive_candidates,
        "new_projects": new_projects,
    }


# ── Markdown rendering ──

def render_table(rows: list[dict]) -> str:
    if not rows:
        return "_(none)_\n"
    header = "| Merchant | Products | Status | Due | AONR | Flag |\n|---|---|---|---|---|---|\n"
    lines = []
    for r in rows:
        lines.append(
            f"| [{r['name']}](active/{r['slug']}/PROJECT.md) | {r['products']} | "
            f"{r['status']} | {r['due_display']} | {r['aonr']} | {r['flag']} |"
        )
    return header + "\n".join(lines) + "\n"


def build_index(dry_run: bool, do_hubble: bool) -> int:
    if not ACTIVE_DIR.exists():
        print(f"FATAL: {ACTIVE_DIR} does not exist", file=sys.stderr)
        return 1

    active_slugs = sorted(p.name for p in ACTIVE_DIR.iterdir()
                          if p.is_dir() and not p.name.startswith("."))
    archive_slugs = sorted(p.name for p in ARCHIVE_DIR.iterdir()
                           if ARCHIVE_DIR.exists() and p.is_dir() and not p.name.startswith(".")) if ARCHIVE_DIR.exists() else []

    # Detect any archive slug accidentally also in active (shouldn't happen, but report)
    collisions = set(active_slugs) & set(archive_slugs)

    buckets: dict[str, list[dict]] = {p: [] for p in PRIORITY_ORDER}
    parse_errors = []

    for slug in active_slugs:
        proj_md = ACTIVE_DIR / slug / "PROJECT.md"
        fields = parse_project_md(proj_md)
        if not fields:
            parse_errors.append(slug)
            continue
        due_date, due_display = parse_due(fields.get("due", "TBD"))
        flag = compute_flag(due_date, fields.get("status"))
        priority = normalize_priority(fields.get("priority"))
        buckets[priority].append({
            "slug": slug,
            "name": fields.get("name", slug),
            "products": fields.get("products", "TBD"),
            "status": fields.get("status", "Integration"),
            "due_display": due_display,
            "aonr": fields.get("aonr", "TBD"),
            "flag": flag,
            "due_date": due_date,
        })

    # Sort each bucket by Due ascending (None last), then name
    for p in PRIORITY_ORDER:
        buckets[p].sort(key=lambda r: (r["due_date"] is None, r["due_date"] or date.max, r["name"].lower()))

    today_str = date.today().isoformat()

    out_lines = [f"# Projects Dashboard\n",
                 f"**Last reconciliation**: {today_str}",
                 f"**Active**: {len(active_slugs)}    **Archived**: {len(archive_slugs)}\n"]

    if collisions:
        out_lines.append("> WARNING: slugs present in BOTH active/ and archive/: "
                         + ", ".join(sorted(collisions)) + "\n")

    for p in PRIORITY_ORDER:
        if not buckets[p] and p == "Unspecified":
            continue
        out_lines.append(f"## {p} Priority\n")
        out_lines.append(render_table(buckets[p]))

    # Hubble cross-check
    if do_hubble:
        hubble = hubble_cross_check(active_slugs)
        if hubble["available"]:
            arc = hubble.get("archive_candidates", [])
            new = hubble.get("new_projects", [])
            if arc or new:
                out_lines.append("## Hubble Cross-Check\n")
                out_lines.append(f"_(Snapshot fetched {hubble.get('fetched_at')})_\n")
                if arc:
                    out_lines.append("**Archive candidates** (active locally, not in Hubble `In Progress`):\n")
                    for slug in arc:
                        out_lines.append(f"- `{slug}` — confirm archive or update Hubble status")
                    out_lines.append("")
                if new:
                    out_lines.append("**New Hubble rows without a local folder**:\n")
                    for n in new:
                        out_lines.append(f"- {n['project_name']} (project_id `{n.get('project_id')}`, AE: {n.get('ae')}, AONR: {n.get('aonr')})")
                    out_lines.append("")
        else:
            out_lines.append("> Hubble snapshot missing or unparseable — skipping cross-check.\n")

    if parse_errors:
        out_lines.append("## Parse Errors\n")
        out_lines.append("Could not extract Overview fields from these PROJECT.md files:\n")
        for slug in parse_errors:
            out_lines.append(f"- `{slug}`")
        out_lines.append("")

    rendered = "\n".join(out_lines).rstrip() + "\n"

    if dry_run:
        sys.stdout.write(rendered)
        return 0

    INDEX_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {INDEX_PATH} ({len(active_slugs)} active, {len(archive_slugs)} archived, "
          f"{sum(len(b) for b in buckets.values())} rows, {len(parse_errors)} parse errors).")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print to stdout, don't write")
    parser.add_argument("--no-hubble", action="store_true", help="skip Hubble cross-check")
    args = parser.parse_args()
    sys.exit(build_index(args.dry_run, not args.no_hubble))


if __name__ == "__main__":
    main()
