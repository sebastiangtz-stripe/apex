#!/usr/bin/env python3
"""
Bidirectional reconciliation between local markdown and Asana.

Syncs in both directions:
  Asana -> Local: completed subtasks mark local action items as [x]
  Local -> Asana: new local action items create Asana subtasks

Usage:
  python3 scripts/asana-reconcile.py                # reconcile all projects
  python3 scripts/asana-reconcile.py --slug example-merchant     # reconcile one project
  python3 scripts/asana-reconcile.py --dry-run       # show what would change
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = WORKSPACE_ROOT / "projects" / "active"
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

# Action Items cross-project
AI_PROJECT = ENV.get("ASANA_AI_PROJECT_GID", "")
AI_SECTIONS = {
    "today":     ENV.get("ASANA_AI_SECTION_TODAY", ""),
    "this_week": ENV.get("ASANA_AI_SECTION_THIS_WEEK", ""),
    "later":     ENV.get("ASANA_AI_SECTION_LATER", ""),
    "waiting":   ENV.get("ASANA_AI_SECTION_WAITING", ""),
}
AI_FIELD_MERCHANT   = ENV.get("ASANA_AI_FIELD_MERCHANT", "")
AI_FIELD_TAG        = ENV.get("ASANA_AI_FIELD_TAG", "")
AI_FIELD_COMPLEXITY = ENV.get("ASANA_AI_FIELD_COMPLEXITY", "")
AI_COMPLEXITY = {
    "low":    ENV.get("ASANA_AI_COMPLEXITY_LOW", ""),
    "medium": ENV.get("ASANA_AI_COMPLEXITY_MEDIUM", ""),
    "high":   ENV.get("ASANA_AI_COMPLEXITY_HIGH", ""),
}
AI_TAG_OPTIONS = {
    "email":    ENV.get("ASANA_AI_TAG_EMAIL", ""),
    "reply":    ENV.get("ASANA_AI_TAG_REPLY", ""),
    "research": ENV.get("ASANA_AI_TAG_RESEARCH", ""),
    "prep":     ENV.get("ASANA_AI_TAG_PREP", ""),
    "schedule": ENV.get("ASANA_AI_TAG_SCHEDULE", ""),
    "track":    ENV.get("ASANA_AI_TAG_TRACK", ""),
    "log":      ENV.get("ASANA_AI_TAG_LOG", ""),
    "waiting":  ENV.get("ASANA_AI_TAG_WAITING", ""),
}

def api(method, path, data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        f"https://app.asana.com/api/1.0{path}",
        data=body,
        headers={"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req)
        if resp.status == 204:
            return {}
        return json.loads(resp.read()).get("data", {})
    except urllib.error.HTTPError as e:
        print(f"    API error {e.code}: {e.read().decode()[:200]}")
        return None

def score_complexity(local_raw):
    """Auto-score complexity from action-items.md line tags: #log/#track/#schedule=Low, #email/#reply/#prep=Medium, #research=High."""
    raw_lower = local_raw.lower()
    if "#research" in raw_lower:
        return "high"
    if any(f"#{t}" in raw_lower for t in ("email", "reply", "prep")):
        return "medium"
    if any(f"#{t}" in raw_lower for t in ("log", "track", "schedule", "waiting")):
        return "low"
    return "medium"


def multi_home_subtask(subtask_gid, merchant_name, local_raw, due_on):
    """Add a subtask to the Action Items cross-project with correct section, fields, and complexity.

    Tag and complexity are derived from `local_raw` (the original action-items.md line,
    which still carries #tag markers). The Asana subtask name itself is plain natural language.
    """
    if not AI_PROJECT:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    week_end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    raw_lower = local_raw.lower()
    if "#waiting" in raw_lower:
        section = AI_SECTIONS["waiting"]
    elif not due_on:
        section = AI_SECTIONS["later"]
    elif due_on <= today:
        section = AI_SECTIONS["today"]
    elif due_on <= week_end:
        section = AI_SECTIONS["this_week"]
    else:
        section = AI_SECTIONS["later"]

    api("POST", f"/tasks/{subtask_gid}/addProject", {
        "data": {"project": AI_PROJECT, "section": section}
    })

    custom = {}
    if AI_FIELD_MERCHANT:
        custom[AI_FIELD_MERCHANT] = merchant_name
    if AI_FIELD_TAG:
        for tag, gid in AI_TAG_OPTIONS.items():
            if f"#{tag}" in raw_lower and gid:
                custom[AI_FIELD_TAG] = gid
                break
    if AI_FIELD_COMPLEXITY:
        level = score_complexity(local_raw)
        complexity_gid = AI_COMPLEXITY.get(level, "")
        if complexity_gid:
            custom[AI_FIELD_COMPLEXITY] = complexity_gid
    if custom:
        api("PUT", f"/tasks/{subtask_gid}", {"data": {"custom_fields": custom}})


def get_asana_subtasks(task_gid):
    result = api("GET", f"/tasks/{task_gid}/subtasks?opt_fields=name,completed,due_on")
    return result if isinstance(result, list) else []

def normalize_for_dedup(text):
    """Strip tags, metadata, separators, and whitespace for dedup comparison."""
    s = re.sub(r"#\w+\s*", "", text)
    s = re.sub(r"\s*—\s*(Complexity|Owner|Due|Source|Completed):.*", "", s)
    s = re.sub(r"^[\s—\-]+", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def clean_description(raw):
    """Extract the plain action-verb description from a local action-items.md line.

    Strips trailing metadata fields (Complexity/Owner/Due/Source/Completed) and any
    `#tag` markers, leaving just the natural-language description used for the
    Asana subtask name.
    """
    desc = re.sub(r"\s*—\s*(Complexity|Owner|Due|Source|Completed):.*", "", raw)
    desc = re.sub(r"^#\w+\s*", "", desc)
    desc = re.sub(r"#\w+\s*", "", desc).strip()
    desc = re.sub(r"^[—\-\s]+", "", desc).strip()
    return desc

def fuzzy_match(a, b):
    """Check if two normalized strings refer to the same action item."""
    if a == b:
        return True
    # Extract key terms (dates, merchant names, core verbs)
    a_words = set(a.split())
    b_words = set(b.split())
    if not a_words or not b_words:
        return False
    overlap = a_words & b_words
    smaller = min(len(a_words), len(b_words))
    return len(overlap) / smaller >= 0.6

def parse_local_actions(path):
    if not path.exists():
        return [], []
    open_items = []
    completed_items = []
    seen_normalized = set()
    section = ""
    for line in path.read_text().splitlines():
        if re.match(r"^## Open", line, re.I):
            section = "open"
            continue
        if re.match(r"^## (Completed|Done)", line, re.I):
            section = "completed"
            continue
        if re.match(r"^## Waiting", line, re.I):
            section = "open"
            continue
        m = re.match(r"^- \[([ xX])\] (.+)", line)
        if m and section:
            is_done = m.group(1) != " "
            raw = m.group(2)
            norm = normalize_for_dedup(raw)
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)
            item = {"raw": raw, "completed": is_done, "line": line}
            if is_done:
                completed_items.append(item)
            else:
                open_items.append(item)
    return open_items, completed_items


def reconcile_project(slug, dry_run=False):
    project_dir = PROJECTS_DIR / slug
    asana_json = project_dir / "asana.json"
    actions_md = project_dir / "action-items.md"

    if not asana_json.exists():
        return {"status": "no_mapping"}

    mapping = json.loads(asana_json.read_text())
    task_gid = mapping["task_gid"]

    if not task_gid or task_gid == "REPLACE" or not task_gid.isdigit():
        print(f"  [{slug}] Skipping — task_gid is a placeholder")
        return {"status": "placeholder_skip"}

    project_md = project_dir / "PROJECT.md"
    merchant_name = ""
    if project_md.exists():
        first = project_md.read_text().splitlines()[0]
        merchant_name = first.lstrip("# ").strip()

    asana_subtasks = get_asana_subtasks(task_gid)
    local_open, local_completed = parse_local_actions(actions_md)

    changes = []

    # Direction 1: Asana -> Local
    # If a subtask is completed in Asana but open locally, mark it done locally
    for sub in asana_subtasks:
        if not sub.get("completed"):
            continue
        sub_norm = normalize_for_dedup(sub["name"])
        for item in local_open:
            item_norm = normalize_for_dedup(item["raw"])
            if fuzzy_match(sub_norm, item_norm):
                changes.append({
                    "direction": "asana->local",
                    "action": "complete",
                    "item": item["raw"][:80],
                })
                if not dry_run:
                    content = actions_md.read_text()
                    content = content.replace(item["line"], item["line"].replace("- [ ]", "- [x]"))
                    actions_md.write_text(content)
                break

    # Direction 2: Local -> Asana
    # If a local open item has no matching Asana subtask, create one
    asana_norms = [normalize_for_dedup(s["name"]) for s in asana_subtasks]
    for item in local_open:
        item_norm = normalize_for_dedup(item["raw"])
        matched = any(fuzzy_match(item_norm, an) for an in asana_norms)
        if not matched:
            due_match = re.search(r"Due:\s*(\d{4}-\d{2}-\d{2})", item["raw"])
            due = due_match.group(1) if due_match else None

            changes.append({
                "direction": "local->asana",
                "action": "create_subtask",
                "item": item["raw"][:80],
            })
            if not dry_run:
                # Subtask name is the plain action-verb description (no #tag prefix);
                # tag still lives on the local line and the Asana Tag custom field.
                clean_name = (clean_description(item["raw"]) or item["raw"])[:1000]
                result = api("POST", f"/tasks/{task_gid}/subtasks", {
                    "data": {
                        "name": clean_name,
                        "due_on": due,
                    }
                })
                if result:
                    gid = result["gid"]
                    mapping["subtask_gids"][item["raw"][:80]] = gid
                    multi_home_subtask(gid, merchant_name, item["raw"], due)
                time.sleep(0.1)

    # Direction 1b: Local completed -> Asana
    # If a local item is marked [x] but the Asana subtask is still open, complete it
    for item in local_completed:
        item_norm = normalize_for_dedup(item["raw"])
        for sub in asana_subtasks:
            if sub.get("completed"):
                continue
            sub_norm = normalize_for_dedup(sub["name"])
            if fuzzy_match(item_norm, sub_norm):
                changes.append({
                    "direction": "local->asana",
                    "action": "complete",
                    "item": item["raw"][:80],
                })
                if not dry_run:
                    api("PUT", f"/tasks/{sub['gid']}", {"data": {"completed": True}})
                    time.sleep(0.1)
                break

    if not dry_run and changes:
        asana_json.write_text(json.dumps(mapping, indent=2) + "\n")

    return {"status": "ok", "changes": changes}

def main():
    if not PAT:
        print("Error: ASANA_PAT not found in .env")
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="Reconcile local files with Asana")
    parser.add_argument("--slug", help="Reconcile a single project")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    args = parser.parse_args()

    if args.slug:
        slugs = [args.slug]
    else:
        slugs = sorted([
            d.name for d in PROJECTS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Reconciling {len(slugs)} project(s) [{mode}]\n")

    total_changes = 0
    for slug in slugs:
        result = reconcile_project(slug, dry_run=args.dry_run)
        if result["status"] == "no_mapping":
            continue
        changes = result.get("changes", [])
        if changes:
            print(f"  [{slug}] {len(changes)} change(s):")
            for c in changes:
                print(f"    {c['direction']} | {c['action']} | {c['item']}")
            total_changes += len(changes)

    if total_changes == 0:
        print("  Everything in sync!")
    else:
        print(f"\nTotal: {total_changes} change(s) {'(would be applied)' if args.dry_run else 'applied'}")

if __name__ == "__main__":
    main()
