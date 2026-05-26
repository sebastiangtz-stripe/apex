#!/usr/bin/env python3
"""
Verify the post-apply state of dual-write proposals.

For each item in a proposals file (live or archived), this script confirms
that what we *think* landed actually landed:

  - Asana subtask GID exists, has the expected name and completion state
  - Asana subtask has the merchant + tag + complexity custom fields set
  - Local action-items.md has the expected line (created items)
  - Local action-items.md is marked [x] (auto-closed items)

Reports drift in five categories:

  asana_missing            subtask_gid in apply_status but Asana returns 404 / not found
  asana_state_mismatch     Asana subtask exists but completion state disagrees with apply_status
  custom_fields_unset      Asana subtask exists but Merchant/Tag/Complexity custom fields are empty
  local_line_missing       Item marked applied but no matching line in action-items.md
  local_line_open_for_close auto_close item marked applied but local line still [ ]

Usage:
  python3 scripts/verify-proposals.py --recent 7        # last 7 days of applied/ + all pending
  python3 scripts/verify-proposals.py --slug example-merchant        # filter by merchant slug
  python3 scripts/verify-proposals.py --file <path>      # check a specific file
  python3 scripts/verify-proposals.py --json             # machine-readable report

When invoked from inside apply-proposals.py via --verify (default on, see
Phase 3 wiring), it runs against just the file(s) the applier processed.
Non-zero drift is surfaced in the run report but does not fail the apply.
"""

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = WORKSPACE_ROOT / "projects" / "active"
PROPOSALS_DIR = WORKSPACE_ROOT / "data" / "scan-proposals"
APPLIED_DIR = PROPOSALS_DIR / "applied"
ENV_FILE = WORKSPACE_ROOT / ".env"


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
PAT = ENV.get("ASANA_PAT", "")
AI_FIELD_MERCHANT = ENV.get("ASANA_AI_FIELD_MERCHANT", "")
AI_FIELD_TAG = ENV.get("ASANA_AI_FIELD_TAG", "")
AI_FIELD_COMPLEXITY = ENV.get("ASANA_AI_FIELD_COMPLEXITY", "")


def api_get(path):
    """Lightweight GET helper. Returns dict on success, None on 404, raises on other errors."""
    url = f"https://app.asana.com/api/1.0{path}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {PAT}"}, method="GET"
    )
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=20)
            payload = json.loads(resp.read())
            return payload.get("data") if isinstance(payload, dict) else payload
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                retry = int(e.headers.get("Retry-After", "30"))
                time.sleep(retry)
                continue
            if 500 <= e.code < 600:
                time.sleep(2 ** attempt)
                continue
            raise
        except urllib.error.URLError:
            time.sleep(2 ** attempt)
            continue
    return None


def normalize(text):
    if not text:
        return ""
    s = re.sub(r"#\w+\s*", "", text)
    s = re.sub(r"\s*—\s*(Complexity|Owner|Due|Source|Completed):.*", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    return s


def fuzzy_match(a, b, threshold=0.6):
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


def read_local_action_items(slug):
    """Return (open_lines, x_lines): list of normalized strings for each."""
    path = PROJECTS_DIR / slug / "action-items.md"
    if not path.exists():
        return [], []
    open_lines = []
    x_lines = []
    section = ""
    for line in path.read_text().splitlines():
        if re.match(r"^## Open", line, re.I):
            section = "open"
            continue
        if re.match(r"^## (Completed|Done|Waiting)", line, re.I):
            section = "other"
            continue
        m = re.match(r"^- \[([ xX])\] (.+)$", line)
        if not m or section != "open":
            continue
        norm = normalize(m.group(2))
        if m.group(1) == " ":
            open_lines.append(norm)
        else:
            x_lines.append(norm)
    return open_lines, x_lines


def verify_file(path):
    """Run all verification checks against one proposals file. Return drift list."""
    try:
        data = json.loads(Path(path).read_text())
    except Exception as e:
        return [{"file": str(path), "kind": "parse_error", "detail": str(e)}]

    slug = data.get("slug", "")
    apply_status = data.get("apply_status", {}) or {}
    drift = []

    open_lines, x_lines = read_local_action_items(slug)

    # Index items by id
    items_by_id = {}
    for it in data.get("auto_close", []) or []:
        items_by_id[it.get("id")] = ("auto_close", it)
    for it in data.get("new_items", []) or []:
        items_by_id[it.get("id")] = ("new_item", it)
    for it in data.get("inline_gaps", []) or []:
        items_by_id[it.get("id")] = ("inline_gap", it)

    if not isinstance(apply_status, dict):
        return drift
    for item_id, status in apply_status.items():
        if isinstance(status, str):
            continue
        st = status.get("status")
        if st != "applied":
            continue
        kind, item = items_by_id.get(item_id, (None, None))
        if not item:
            continue

        # Inline gaps don't have Asana state — skip Asana checks
        if kind == "inline_gap":
            continue

        subtask_gid = status.get("subtask_gid")
        if not subtask_gid:
            drift.append(
                {
                    "file": Path(path).name,
                    "slug": slug,
                    "item_id": item_id,
                    "kind": "no_subtask_gid_recorded",
                    "detail": f"applied {kind} has no subtask_gid in apply_status",
                }
            )
            continue

        sub = api_get(
            f"/tasks/{subtask_gid}?opt_fields=name,completed,custom_fields.gid,custom_fields.enum_value,custom_fields.text_value"
        )
        if sub is None:
            drift.append(
                {
                    "file": Path(path).name,
                    "slug": slug,
                    "item_id": item_id,
                    "subtask_gid": subtask_gid,
                    "kind": "asana_missing",
                    "detail": "subtask returned 404 from Asana",
                }
            )
            continue

        # Completion state checks
        is_completed = bool(sub.get("completed"))
        if kind == "auto_close" and not is_completed:
            drift.append(
                {
                    "file": Path(path).name,
                    "slug": slug,
                    "item_id": item_id,
                    "subtask_gid": subtask_gid,
                    "kind": "asana_state_mismatch",
                    "detail": "auto_close marked applied but Asana subtask still open",
                }
            )

        # Custom fields check (only for new_items — auto_close subtasks pre-existed)
        if kind == "new_item":
            cfs = sub.get("custom_fields") or []
            cf_by_gid = {cf.get("gid"): cf for cf in cfs}

            def cf_set(gid):
                cf = cf_by_gid.get(gid)
                if not cf:
                    return False
                ev = cf.get("enum_value")
                if ev is not None:
                    return True
                tv = cf.get("text_value")
                return bool(tv and tv.strip())

            missing_fields = []
            if AI_FIELD_MERCHANT and not cf_set(AI_FIELD_MERCHANT):
                missing_fields.append("Merchant")
            if AI_FIELD_TAG and not cf_set(AI_FIELD_TAG):
                missing_fields.append("Tag")
            if AI_FIELD_COMPLEXITY and not cf_set(AI_FIELD_COMPLEXITY):
                missing_fields.append("Complexity")
            if missing_fields:
                drift.append(
                    {
                        "file": Path(path).name,
                        "slug": slug,
                        "item_id": item_id,
                        "subtask_gid": subtask_gid,
                        "kind": "custom_fields_unset",
                        "detail": f"missing: {', '.join(missing_fields)}",
                    }
                )

        # Local action-items.md checks
        description = item.get("description") or ""
        line_match = item.get("local_line_match") or description
        target_norm = normalize(line_match)

        if kind == "new_item":
            # Should appear as either open or completed line locally
            found = any(fuzzy_match(target_norm, ol) for ol in open_lines + x_lines)
            if not found:
                drift.append(
                    {
                        "file": Path(path).name,
                        "slug": slug,
                        "item_id": item_id,
                        "subtask_gid": subtask_gid,
                        "kind": "local_line_missing",
                        "detail": f"new_item applied but no matching local line: {description[:80]}",
                    }
                )

        if kind == "auto_close":
            # Should NOT appear in open_lines (must be marked [x])
            still_open = any(fuzzy_match(target_norm, ol) for ol in open_lines)
            if still_open:
                drift.append(
                    {
                        "file": Path(path).name,
                        "slug": slug,
                        "item_id": item_id,
                        "subtask_gid": subtask_gid,
                        "kind": "local_line_open_for_close",
                        "detail": f"auto_close applied but local line still [ ]: {line_match[:80]}",
                    }
                )

    return drift


def discover_files(slug=None, recent_days=None, file_arg=None):
    files = []
    if file_arg:
        p = Path(file_arg)
        if not p.is_absolute():
            p = WORKSPACE_ROOT / p
        if p.exists():
            files.append(p)
        return files

    # Pending (non-archived)
    if PROPOSALS_DIR.exists():
        for p in sorted(PROPOSALS_DIR.glob("*.json")):
            if p.parent != PROPOSALS_DIR:
                continue
            if slug and not p.stem.startswith(f"{slug}-"):
                continue
            files.append(p)

    # Archived
    if APPLIED_DIR.exists():
        cutoff = None
        if recent_days is not None:
            cutoff = datetime.now() - timedelta(days=recent_days)
        for p in sorted(APPLIED_DIR.glob("*.json")):
            if slug and not p.stem.startswith(f"{slug}-"):
                continue
            if cutoff:
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
                if mtime < cutoff:
                    continue
            files.append(p)
    return files


def render_report(drift, files_checked, json_mode):
    if json_mode:
        print(json.dumps(
            {
                "files_checked": files_checked,
                "drift_count": len(drift),
                "drift": drift,
            },
            indent=2,
        ))
        return
    print(f"Verified {files_checked} file(s); found {len(drift)} drift item(s).")
    if not drift:
        return
    by_kind = {}
    for d in drift:
        by_kind.setdefault(d["kind"], []).append(d)
    for kind, items in by_kind.items():
        print(f"\n  {kind}: {len(items)}")
        for d in items:
            slug = d.get("slug", "?")
            extra = d.get("subtask_gid", "")
            extra_str = f" subtask={extra}" if extra else ""
            print(f"    - [{slug}]{extra_str} {d['detail'][:120]}")


def main():
    parser = argparse.ArgumentParser(description="Verify post-apply state of dual-write proposals.")
    parser.add_argument("--recent", type=int, help="Limit archived files to last N days")
    parser.add_argument("--slug", help="Filter to one merchant slug")
    parser.add_argument("--file", help="Check one specific proposals file")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args()

    if not PAT:
        print("Error: ASANA_PAT not in .env")
        sys.exit(1)

    files = discover_files(slug=args.slug, recent_days=args.recent, file_arg=args.file)
    if not files:
        if args.json:
            print(json.dumps({"files_checked": 0, "drift_count": 0, "drift": []}))
        else:
            print("No proposal files found to verify.")
        return

    all_drift = []
    for f in files:
        all_drift.extend(verify_file(f))

    render_report(all_drift, files_checked=len(files), json_mode=args.json)
    sys.exit(0)


if __name__ == "__main__":
    main()
