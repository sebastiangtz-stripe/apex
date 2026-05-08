#!/usr/bin/env python3
"""
Reconcile local merchant projects with the Hubble snapshot (SFDC + Kantata source of truth).

The agent writes data/hubble-snapshot.json via the run_hubble_query MCP tool.
This script reads that snapshot and diffs against projects/active/ + projects/archive/.

Match order per Hubble row:
  1) projects/active/<slug>/hubble.json with matching project_id
  2) Kantata project_id embedded in existing PROJECT.md CSAT link (query arg 'project=<id>')
  3) Fuzzy name match (normalized project_name vs merchant name in PROJECT.md)

Modes:
  --backfill          one-shot: write hubble.json files and sync
                      PROJECT.md External Links + AONR / Started / Due / SFDC Owner
  --reconcile         (default) incremental diff; prints new / archive candidates / drift
  --dry-run           preview only, no writes
  --slug <slug>       restrict to a single local project
  --snapshot <path>   alternate snapshot path (default: data/hubble-snapshot.json)

Exit codes:
  0  clean run (may include printed diffs)
  1  fatal error (missing snapshot, no env, etc.)
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"
ARCHIVE_DIR = WORKSPACE_ROOT / "projects" / "archive"
DEFAULT_SNAPSHOT = WORKSPACE_ROOT / "data" / "hubble-snapshot.json"
ENV_FILE = WORKSPACE_ROOT / ".env"


# ── Env ──

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


# ── Normalization / matching ──

NAME_STOPWORDS = {
    "the", "inc", "llc", "corp", "co", "ltd", "gmbh", "sa", "us", "usa", "uk",
    "gb", "fr", "de", "es", "eu", "ca", "au", "tax", "payments", "payment",
    "billing", "connect", "terminal", "radar", "checkout", "identity", "sigma",
    "moto", "invoicing", "standard", "express", "rev", "share", "agreement",
    "upsell", "pilot", "aonr", "onr", "piv", "usd", "m", "k", "b2b", "b2c",
    "ai", "saas", "health", "funded", "new", "business",
}


def norm_name(text: str) -> str:
    if not text:
        return ""
    s = text.lower()
    s = re.sub(r"[\[\](){}\|,;:\+\/\\]", " ", s)
    s = re.sub(r"[\-–—]", " ", s)
    s = re.sub(r"\$[0-9.,]+[kmb]?", " ", s)
    s = re.sub(r"[0-9]+%?", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in NAME_STOPWORDS and len(t) > 1]
    return " ".join(tokens)


def name_similarity(a: str, b: str) -> float:
    a_tokens = set(norm_name(a).split())
    b_tokens = set(norm_name(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = a_tokens & b_tokens
    return len(overlap) / min(len(a_tokens), len(b_tokens))


# ── PROJECT.md parsing ──

def parse_project_md(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text()
    lines = text.splitlines()
    name = lines[0].replace("# ", "").strip() if lines else ""

    def extract(key):
        for line in lines:
            m = re.match(rf"- \*\*{re.escape(key)}\*\*:\s*(.+)", line)
            if m:
                return m.group(1).strip()
        return ""

    return {
        "name": name,
        "products": extract("Products"),
        "status": extract("Status"),
        "priority": extract("Priority"),
        "started": extract("Started"),
        "due": extract("Due"),
        "aonr": extract("AONR"),
        "sfdc_owner": extract("SFDC Opportunity Owner"),
        "account_ids": extract("Account ID(s)"),
    }


def extract_csat_project_id(project_md_text: str) -> int | None:
    for line in project_md_text.splitlines():
        if "qualtrics" in line.lower() and "project=" in line:
            m = re.search(r"project=(\d+)", line)
            if m:
                return int(m.group(1))
    return None


def aonr_from_md(aonr_field: str) -> float | None:
    if not aonr_field:
        return None
    cleaned = re.sub(r"[^0-9.]", "", aonr_field)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


# ── Matching ──

def match_hubble_to_local(projects: list[dict], scope: list[Path]) -> tuple[dict, list[Path], list[dict]]:
    """
    Returns (matched, unmatched_local, unmatched_hubble):
      matched: dict mapping slug -> hubble_row
      unmatched_local: list of Path (slug dirs) with no Hubble row
      unmatched_hubble: list of hubble_row dicts with no local folder
    """
    by_id = {int(p["project_id"]): p for p in projects}
    matched: dict[str, dict] = {}
    seen_ids: set[int] = set()

    # Pass 1: explicit hubble.json
    for d in scope:
        hj = d / "hubble.json"
        if hj.exists():
            try:
                mapping = json.loads(hj.read_text())
                pid = int(mapping.get("project_id", 0))
                if pid in by_id:
                    matched[d.name] = by_id[pid]
                    seen_ids.add(pid)
            except (json.JSONDecodeError, ValueError):
                pass

    # Pass 2: CSAT link project_id
    for d in scope:
        if d.name in matched:
            continue
        pm = d / "PROJECT.md"
        if not pm.exists():
            continue
        pid = extract_csat_project_id(pm.read_text())
        if pid and pid in by_id and pid not in seen_ids:
            matched[d.name] = by_id[pid]
            seen_ids.add(pid)

    # Pass 3: fuzzy name
    remaining_rows = [p for p in projects if int(p["project_id"]) not in seen_ids]
    for d in scope:
        if d.name in matched:
            continue
        pm = d / "PROJECT.md"
        if not pm.exists():
            continue
        local_name = pm.read_text().splitlines()[0].replace("# ", "").strip()
        best: tuple[float, dict | None] = (0.0, None)
        for row in remaining_rows:
            score = name_similarity(local_name, row["project_name"])
            if score > best[0]:
                best = (score, row)
        if best[0] >= 0.6 and best[1] is not None:
            pid = int(best[1]["project_id"])
            if pid not in seen_ids:
                matched[d.name] = best[1]
                seen_ids.add(pid)
                remaining_rows = [r for r in remaining_rows if int(r["project_id"]) != pid]

    unmatched_local = [d for d in scope if d.name not in matched]
    unmatched_hubble = [p for p in projects if int(p["project_id"]) not in seen_ids]
    return matched, unmatched_local, unmatched_hubble


# ── Writers ──

def write_hubble_json(slug_dir: Path, row: dict, fetched_at: str, dry_run: bool) -> bool:
    hj = slug_dir / "hubble.json"
    mapping = {
        "project_id": int(row["project_id"]),
        "project_name": row.get("project_name"),
        "sfdc_opp_link": row.get("sfdc_opp_link"),
        "kantata_workspace_link": row.get("kantata_workspace_link"),
        "csat_link": row.get("csat_link"),
        "account_segment": row.get("account_segment"),
        "accelerate_type": row.get("accelerate_type"),
        "project_geography": row.get("project_geography"),
        "last_synced": fetched_at,
    }
    if hj.exists():
        try:
            existing = json.loads(hj.read_text())
            if {k: existing.get(k) for k in mapping} == mapping:
                return False
        except json.JSONDecodeError:
            pass
    if not dry_run:
        hj.write_text(json.dumps(mapping, indent=2) + "\n")
    return True


def field_set_or_insert(text: str, field_key: str, new_value: str) -> str:
    """
    Update `- **Field**: value` in Overview.
    If missing, insert after the last existing `- **...` line under '## Overview'.
    """
    pattern = rf"(- \*\*{re.escape(field_key)}\*\*:\s*)(.*)"
    if re.search(pattern, text):
        return re.sub(pattern, lambda m: f"{m.group(1)}{new_value}", text, count=1)

    lines = text.splitlines()
    in_overview = False
    insert_at = None
    for i, line in enumerate(lines):
        if re.match(r"^## Overview", line, re.I):
            in_overview = True
            continue
        if in_overview and line.startswith("## "):
            break
        if in_overview and re.match(r"^- \*\*", line):
            insert_at = i
    if insert_at is None:
        return text
    lines.insert(insert_at + 1, f"- **{field_key}**: {new_value}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def upsert_external_link(text: str, label: str, url: str) -> str:
    """
    Ensure a `- Label: url` line exists under '## External Links'.
    Updates if label present, inserts otherwise.
    """
    lines = text.splitlines()
    in_section = False
    section_start = None
    section_end = None
    existing_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^## External Links", line, re.I):
            in_section = True
            section_start = i
            continue
        if in_section and line.startswith("## "):
            section_end = i
            break
        if in_section and re.match(rf"^- {re.escape(label)}\s*:", line):
            existing_idx = i

    if section_start is None:
        return text

    if section_end is None:
        section_end = len(lines)

    new_line = f"- {label}: {url}"
    if existing_idx is not None:
        lines[existing_idx] = new_line
    else:
        insert_at = section_start + 1
        for i in range(section_start + 1, section_end):
            if lines[i].startswith("- "):
                insert_at = i + 1
        lines.insert(insert_at, new_line)

    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def format_aonr(value) -> str:
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v == int(v):
        return f"${int(v):,}"
    return f"${v:,.2f}"


def apply_backfill(slug_dir: Path, row: dict, fetched_at: str, dry_run: bool) -> list[str]:
    """Write hubble.json + update PROJECT.md External Links + key Overview fields."""
    changes: list[str] = []

    wrote = write_hubble_json(slug_dir, row, fetched_at, dry_run)
    if wrote:
        changes.append("hubble.json")

    pm = slug_dir / "PROJECT.md"
    if not pm.exists():
        return changes

    original = pm.read_text()
    text = original

    kp_id = int(row["project_id"])
    wk_link = row.get("kantata_workspace_link") or f"https://app.mavenlink.com/workspaces/{kp_id}"
    sf_link = row.get("sfdc_opp_link")
    csat = row.get("csat_link")

    text = upsert_external_link(text, "Kantata Project ID", str(kp_id))
    text = upsert_external_link(text, "Kantata Workspace", wk_link)
    if sf_link:
        text = upsert_external_link(text, "Salesforce", sf_link)
    if csat:
        text = upsert_external_link(text, "CSAT", csat)

    aonr_str = format_aonr(row.get("sfdc_aonr"))
    if aonr_str:
        text = field_set_or_insert(text, "AONR", aonr_str)
    if row.get("account_executive"):
        text = field_set_or_insert(text, "SFDC Opportunity Owner", row["account_executive"])
    if row.get("kantata_start_date"):
        text = field_set_or_insert(text, "Started", row["kantata_start_date"])
    if row.get("kantata_end_date"):
        text = field_set_or_insert(text, "Due", row["kantata_end_date"])

    if text != original:
        changes.append("PROJECT.md")
        if not dry_run:
            pm.write_text(text)

    return changes


# ── Drift detection ──

def compute_drift(slug_dir: Path, row: dict) -> list[tuple[str, str, str]]:
    """Return list of (field, local_value, hubble_value) for reconcile output."""
    pm = slug_dir / "PROJECT.md"
    if not pm.exists():
        return []
    md = parse_project_md(pm)
    diffs: list[tuple[str, str, str]] = []

    local_aonr = aonr_from_md(md.get("aonr", ""))
    remote_aonr = row.get("sfdc_aonr")
    if remote_aonr is not None and local_aonr is not None:
        try:
            if abs(float(remote_aonr) - local_aonr) >= 1:
                diffs.append(("AONR", md["aonr"], format_aonr(remote_aonr)))
        except (TypeError, ValueError):
            pass
    elif remote_aonr is not None and not local_aonr:
        diffs.append(("AONR", md.get("aonr", ""), format_aonr(remote_aonr)))

    if row.get("account_executive") and md.get("sfdc_owner") != row["account_executive"]:
        diffs.append(("SFDC Opportunity Owner", md.get("sfdc_owner", ""), row["account_executive"]))

    if row.get("kantata_start_date") and md.get("started") != row["kantata_start_date"]:
        diffs.append(("Started", md.get("started", ""), row["kantata_start_date"]))

    if row.get("kantata_end_date") and md.get("due") != row["kantata_end_date"]:
        diffs.append(("Due", md.get("due", ""), row["kantata_end_date"]))

    return diffs


# ── Main ──

def main():
    parser = argparse.ArgumentParser(description="Reconcile local projects with the Hubble snapshot")
    parser.add_argument("--backfill", action="store_true",
                        help="One-shot: write hubble.json + update PROJECT.md fields")
    parser.add_argument("--reconcile", action="store_true",
                        help="Default mode: diff only (new/archive candidates/drift)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--slug", help="Restrict to a single local project")
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT),
                        help=f"Path to snapshot JSON (default: {DEFAULT_SNAPSHOT.relative_to(WORKSPACE_ROOT)})")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = WORKSPACE_ROOT / snapshot_path

    if not snapshot_path.exists():
        print(f"Error: snapshot not found at {snapshot_path}")
        print("  Agent must run the Hubble saved query and write data/hubble-snapshot.json first.")
        sys.exit(1)

    snapshot = json.loads(snapshot_path.read_text())
    projects = snapshot.get("projects", [])
    fetched_at = snapshot.get("fetched_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    if args.slug:
        scope = [ACTIVE_DIR / args.slug]
        if not scope[0].is_dir():
            print(f"Error: {scope[0]} does not exist")
            sys.exit(1)
    else:
        scope = sorted([
            d for d in ACTIVE_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    matched, unmatched_local, unmatched_hubble = match_hubble_to_local(projects, scope)

    mode = "BACKFILL" if args.backfill else "RECONCILE"
    if args.dry_run:
        mode += " (dry-run)"
    print(f"[{mode}] snapshot fetched_at={fetched_at} | {len(projects)} Hubble rows | {len(scope)} local folders\n")

    # Backfill mode: write hubble.json + sync PROJECT.md fields
    if args.backfill:
        total_changed = 0
        for slug, row in sorted(matched.items()):
            changes = apply_backfill(ACTIVE_DIR / slug, row, fetched_at, args.dry_run)
            if changes:
                total_changed += 1
                print(f"  [{slug}] kantata={row['project_id']} -> {', '.join(changes)}")
        print(f"\n  {total_changed} project(s) updated")

    # Reconcile mode: diff
    else:
        drift_count = 0
        for slug, row in sorted(matched.items()):
            diffs = compute_drift(ACTIVE_DIR / slug, row)
            if diffs:
                drift_count += 1
                print(f"  [DRIFT] {slug} (kantata={row['project_id']})")
                for field, local_v, remote_v in diffs:
                    print(f"    {field}: local={local_v!r:<40} hubble={remote_v!r}")

        if drift_count == 0:
            print("  No drift detected across matched projects.")

    # Always report unmatched
    if unmatched_local and not args.slug:
        print(f"\n  ARCHIVE CANDIDATES ({len(unmatched_local)} local folders not in Hubble 'In Progress'):")
        for d in unmatched_local:
            print(f"    - {d.name}")

    if unmatched_hubble and not args.slug:
        print(f"\n  NEW PROJECTS ({len(unmatched_hubble)} Hubble rows with no local folder):")
        for row in unmatched_hubble:
            print(f"    - kantata={row['project_id']} \"{row['project_name']}\" (AE: {row.get('account_executive', '?')}, AONR: {format_aonr(row.get('sfdc_aonr'))})")


if __name__ == "__main__":
    main()
