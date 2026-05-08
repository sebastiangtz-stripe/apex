#!/usr/bin/env python3
"""
Sync local merchant projects to the Asana board.

NOTE: Custom field and enum option GIDs are tagged "REPLACE" — they must be
overridden via .env (preferred) or by editing the constants below to point at
your Asana workspace's actual GIDs. See SETUP.md §2 for how to discover them.
Creates one task per merchant with custom fields and subtasks for action items.
Saves mapping to projects/active/<slug>/asana.json.

Usage:
  python3 scripts/sync-to-asana.py                # sync all unsynced projects
  python3 scripts/sync-to-asana.py --slug example-merchant     # sync a single project
  python3 scripts/sync-to-asana.py --resync        # re-sync all (overwrite existing)
"""

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = WORKSPACE_ROOT / "projects" / "active"
ENV_FILE = WORKSPACE_ROOT / ".env"

# ── Config ──

def load_env():
    env = {}
    for f in [ENV_FILE, WORKSPACE_ROOT / "dashboard" / ".env.local"]:
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env

ENV = load_env()
PAT = ENV.get("ASANA_PAT", "")
WORKSPACE_GID = ENV.get("ASANA_WORKSPACE_GID", "")
PROJECT_GID = ENV.get("ASANA_PROJECT_GID", "REPLACE")

# Action Items cross-project
AI_PROJECT = ENV.get("ASANA_AI_PROJECT_GID", "")
AI_SECTIONS = {
    "today":     ENV.get("ASANA_AI_SECTION_TODAY", ""),
    "this_week": ENV.get("ASANA_AI_SECTION_THIS_WEEK", ""),
    "later":     ENV.get("ASANA_AI_SECTION_LATER", ""),
    "waiting":   ENV.get("ASANA_AI_SECTION_WAITING", ""),
}
AI_FIELD_MERCHANT = ENV.get("ASANA_AI_FIELD_MERCHANT", "")
AI_FIELD_TAG = ENV.get("ASANA_AI_FIELD_TAG", "")
AI_FIELD_COMPLEXITY = ENV.get("ASANA_AI_FIELD_COMPLEXITY", "")
AI_COMPLEXITY = {
    "low":    ENV.get("ASANA_AI_COMPLEXITY_LOW", ""),
    "medium": ENV.get("ASANA_AI_COMPLEXITY_MEDIUM", ""),
    "high":   ENV.get("ASANA_AI_COMPLEXITY_HIGH", ""),
}

# Sections
SECTION_MAP = {
    "Discovery":   ENV.get("ASANA_SECTION_RECEIVED", "REPLACE"),
    "Integration": ENV.get("ASANA_SECTION_GREEN", "REPLACE"),
    "Testing":     ENV.get("ASANA_SECTION_GREEN", "REPLACE"),
    "Go-Live":     ENV.get("ASANA_SECTION_GREEN", "REPLACE"),
    "Live":        ENV.get("ASANA_SECTION_COMPLETED", "REPLACE"),
    "On Hold":     ENV.get("ASANA_SECTION_YELLOW", "REPLACE"),
}
DEFAULT_SECTION = "REPLACE"  # [GREEN]

# Custom field GIDs
CF = {
    "onr":                  "REPLACE",
    "products":             "REPLACE",
    "account_exec":         "REPLACE",
    "status":               "REPLACE",
    "acct_id":              "REPLACE",
    "active_on_accelerate": "REPLACE",
    "is_platform":          "REPLACE",
    "activation_quarter":   "REPLACE",
    "gld":                  "REPLACE",
}

# Enum option GIDs
PRODUCT_OPTIONS = {
    "payments":  "REPLACE",
    "billing":   "REPLACE",
    "radar":     "REPLACE",
    "terminal":  "REPLACE",
    "connect":   "REPLACE",
    "invoicing": "REPLACE",
    "tax":       "REPLACE",
    "sigma":     "REPLACE",
    "identity":  "REPLACE",
}
STATUS_OPTIONS = {
    "green":      "REPLACE",
    "yellow":     "REPLACE",
    "red":        "REPLACE",
    "live":       "REPLACE",
    "terminated": "REPLACE",
    "completed":  "REPLACE",
}
PLATFORM_OPTIONS = {"yes": "REPLACE", "no": "REPLACE"}
ACTIVE_OPTIONS = {"yes": "REPLACE", "no": "REPLACE"}
QUARTER_OPTIONS = {"Q2": "REPLACE", "Q3": "REPLACE", "Q4": "REPLACE"}

# ── Action Items tag options ──
AI_TAG_OPTIONS = {
    "email": "REPLACE",
    "reply": "REPLACE",
    "research": "REPLACE",
    "prep": "REPLACE",
    "schedule": "REPLACE",
    "track": "REPLACE",
    "log": "REPLACE",
    "waiting": "REPLACE",
}

# ── API ──

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
    """Auto-score complexity based on local-line tags: #log/#track/#schedule=Low, #email/#reply/#prep=Medium, #research=High."""
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

    Tag and complexity are derived from `local_raw` (the original `action-items.md` line, which
    still carries `#tag` markers). The Asana subtask name itself is plain natural language.
    """
    if not AI_PROJECT:
        return
    
    # Pick section
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
    
    # Add to project
    api("POST", f"/tasks/{subtask_gid}/addProject", {
        "data": {"project": AI_PROJECT, "section": section}
    })
    
    # Set custom fields (tag, merchant, complexity)
    custom = {}
    if AI_FIELD_MERCHANT:
        custom[AI_FIELD_MERCHANT] = merchant_name
    if AI_FIELD_TAG:
        for tag, gid in AI_TAG_OPTIONS.items():
            if f"#{tag}" in raw_lower:
                custom[AI_FIELD_TAG] = gid
                break
    if AI_FIELD_COMPLEXITY:
        level = score_complexity(local_raw)
        complexity_gid = AI_COMPLEXITY.get(level, "")
        if complexity_gid:
            custom[AI_FIELD_COMPLEXITY] = complexity_gid
    if custom:
        api("PUT", f"/tasks/{subtask_gid}", {"data": {"custom_fields": custom}})

# ── Parsers ──

def parse_project_md(path):
    text = path.read_text()
    lines = text.splitlines()
    name = lines[0].replace("# ", "").strip() if lines else ""

    def extract(key):
        for line in lines:
            m = re.match(rf"- \*\*{re.escape(key)}\*\*:\s*(.+)", line)
            if m:
                return m.group(1).strip()
        return ""

    def extract_section(header):
        in_section = False
        result = []
        for line in lines:
            if re.match(rf"^## {re.escape(header)}", line, re.I):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section and line.strip():
                result.append(line)
        return result

    return {
        "name": name,
        "products": extract("Products"),
        "status": extract("Status"),
        "priority": extract("Priority"),
        "due": extract("Due"),
        "started": extract("Started"),
        "aonr": extract("AONR"),
        "sfdc_owner": extract("SFDC Opportunity Owner"),
        "account_ids": extract("Account ID(s)"),
        "contacts": extract_section("Key Contacts"),
        "communication": extract_section("Communication"),
        "external_links": extract_section("External Links"),
        "product_activation": extract_section("Product Activation"),
        "notes": extract_section("Notes"),
    }

def normalize_for_dedup(text):
    """Strip tags, metadata, separators, and whitespace for dedup comparison."""
    s = re.sub(r"#\w+\s*", "", text)
    s = re.sub(r"\s*—\s*(Owner|Due|Source|Completed):.*", "", s)
    s = re.sub(r"^[\s—\-]+", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def parse_action_items(path):
    if not path.exists():
        return []
    items = []
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
        if m and section == "open" and m.group(1) == " ":
            raw = m.group(2)
            norm = normalize_for_dedup(raw)
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)
            due_match = re.search(r"Due:\s*(\d{4}-\d{2}-\d{2}|ASAP)", raw)
            due = due_match.group(1) if due_match else None
            if due == "ASAP":
                due = None
            # Extract clean description (drops trailing metadata fields, all #tags,
            # and any leading "—" separator). Used as the Asana subtask name.
            desc = re.sub(r"\s*—\s*(Complexity|Owner|Due|Source|Completed):.*", "", raw)
            desc = re.sub(r"^#\w+\s*", "", desc)
            desc = re.sub(r"#\w+\s*", "", desc).strip()
            desc = re.sub(r"^[—\-\s]+", "", desc).strip()
            items.append({"raw": raw, "due": due, "line": line, "notes": desc})
    return items

def parse_aonr(aonr_str):
    """Parse AONR string to number: '$408K' -> 408000, '$3M' -> 3000000, '$1.2B' -> 1200000000, '$138' -> 138"""
    if not aonr_str or aonr_str == "TBD":
        return None
    cleaned = aonr_str.replace("$", "").replace(",", "").strip()
    suffix_multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    if cleaned and cleaned[-1].upper() in suffix_multipliers:
        try:
            return float(cleaned[:-1]) * suffix_multipliers[cleaned[-1].upper()]
        except ValueError:
            return None
    try:
        return float(re.sub(r"[^0-9.]", "", cleaned))
    except ValueError:
        return None

def detect_products(products_str):
    """Match product names to Asana enum GIDs."""
    gids = []
    lower = products_str.lower()
    for name, gid in PRODUCT_OPTIONS.items():
        if name in lower:
            gids.append(gid)
    return gids

def detect_quarter(due_str):
    """Derive activation quarter from due date."""
    if not due_str or due_str == "TBD":
        return None
    m = re.match(r"(\d{4})-(\d{2})", due_str)
    if not m:
        return None
    month = int(m.group(2))
    if month <= 6:
        return QUARTER_OPTIONS.get("Q2")
    elif month <= 9:
        return QUARTER_OPTIONS.get("Q3")
    else:
        return QUARTER_OPTIONS.get("Q4")

def extract_acct_id(account_str):
    """Extract acct_ ID if present."""
    m = re.search(r"(acct_\w+)", account_str)
    return m.group(1) if m else None

def is_date(s):
    """Check if string is a valid YYYY-MM-DD date."""
    return bool(s and s != "TBD" and re.match(r"\d{4}-\d{2}-\d{2}$", s))

# ── Description builder ──

def build_description(info):
    parts = []

    # Links header
    sf = ""
    for line in info["external_links"]:
        m = re.match(r"- Salesforce:\s*(.+)", line)
        if m:
            sf = m.group(1).strip()
    parts.append(f"HO: ")
    parts.append(f"MAN: ")
    parts.append(f"SF: {sf}")
    parts.append(f"KAN: ")
    parts.append("")
    parts.append("---")
    parts.append("")

    # Key contacts
    if info["contacts"]:
        parts.append("Key Contacts:")
        for c in info["contacts"]:
            parts.append(c)
        parts.append("")

    # Communication
    if info["communication"]:
        parts.append("Communication:")
        for c in info["communication"]:
            parts.append(c)
        parts.append("")

    # Product activation
    if info["product_activation"]:
        parts.append("Product Activation:")
        for p in info["product_activation"]:
            parts.append(p)
        parts.append("")

    # Notes
    if info["notes"]:
        parts.append("Notes:")
        for n in info["notes"]:
            parts.append(n)
        if info["priority"]:
            parts.append(f"- Priority: {info['priority']}")

    return "\n".join(parts)

# ── Main ──

def sync_project(slug, resync=False):
    project_dir = PROJECTS_DIR / slug
    asana_json = project_dir / "asana.json"

    if asana_json.exists() and not resync:
        mapping = json.loads(asana_json.read_text())
        print(f"  [{slug}] Already synced (task {mapping['task_gid']}), skipping")
        return "skipped"

    project_md = project_dir / "PROJECT.md"
    if not project_md.exists():
        print(f"  [{slug}] No PROJECT.md, skipping")
        return "skipped"

    info = parse_project_md(project_md)
    status = info["status"] or "Integration"
    section_gid = SECTION_MAP.get(status, DEFAULT_SECTION)

    # Build custom fields
    custom_fields = {}

    onr = parse_aonr(info["aonr"])
    if onr is not None:
        custom_fields[CF["onr"]] = onr

    products = detect_products(info["products"])
    if products:
        custom_fields[CF["products"]] = products

    if info["sfdc_owner"]:
        custom_fields[CF["account_exec"]] = info["sfdc_owner"]

    custom_fields[CF["status"]] = STATUS_OPTIONS["green"]
    custom_fields[CF["active_on_accelerate"]] = ACTIVE_OPTIONS["yes"]

    acct_id = extract_acct_id(info["account_ids"])
    if acct_id:
        custom_fields[CF["acct_id"]] = acct_id

    is_connect = "connect" in info["products"].lower()
    custom_fields[CF["is_platform"]] = PLATFORM_OPTIONS["yes"] if is_connect else PLATFORM_OPTIONS["no"]

    quarter = detect_quarter(info["due"])
    if quarter:
        custom_fields[CF["activation_quarter"]] = quarter

    # Build task data
    task_data = {
        "name": info["name"],
        "notes": build_description(info),
        "workspace": WORKSPACE_GID,
        "projects": [PROJECT_GID],
        "memberships": [{"project": PROJECT_GID, "section": section_gid}],
        "custom_fields": custom_fields,
    }

    if is_date(info["started"]):
        task_data["start_on"] = info["started"]
    if is_date(info["due"]):
        task_data["due_on"] = info["due"]

    print(f"  [{slug}] Creating '{info['name']}' in {status}...", end=" ")

    task = api("POST", "/tasks", {"data": task_data})
    if not task:
        print("FAILED")
        return "failed"

    task_gid = task["gid"]
    print(f"OK (task {task_gid})")

    # Create subtasks for open action items
    action_items = parse_action_items(project_dir / "action-items.md")
    subtask_gids = {}
    for item in action_items:
        # Subtask name is the plain action-verb description (no #tag prefix).
        # Tag + complexity are derived from item["raw"] inside multi_home_subtask.
        clean_name = (item.get("notes") or item["raw"])[:1000]
        sub_data = {
            "name": clean_name,
            "due_on": item["due"],
        }
        sub = api("POST", f"/tasks/{task_gid}/subtasks", {"data": sub_data})
        if sub:
            subtask_gids[item["raw"][:80]] = sub["gid"]
            multi_home_subtask(sub["gid"], info["name"], item["raw"], item["due"])
        time.sleep(0.15)

    # Save mapping
    mapping = {
        "task_gid": task_gid,
        "project_gid": PROJECT_GID,
        "section": status,
        "subtask_gids": subtask_gids,
    }
    asana_json.write_text(json.dumps(mapping, indent=2) + "\n")

    time.sleep(0.2)
    return "created"

def main():
    if not PAT:
        print("Error: ASANA_PAT not found in .env")
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="Sync projects to Asana")
    parser.add_argument("--slug", help="Sync a single project by slug")
    parser.add_argument("--resync", action="store_true", help="Re-sync even if already synced")
    args = parser.parse_args()

    if args.slug:
        slugs = [args.slug]
    else:
        slugs = sorted([
            d.name for d in PROJECTS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    print(f"Syncing {len(slugs)} project(s) to Asana board {PROJECT_GID}\n")

    stats = {"created": 0, "skipped": 0, "failed": 0}
    for slug in slugs:
        result = sync_project(slug, resync=args.resync)
        stats[result] = stats.get(result, 0) + 1

    subtotal = sum(stats.values())
    print(f"\nDone: {stats['created']} created, {stats['skipped']} skipped, {stats['failed']} failed ({subtotal} total)")

if __name__ == "__main__":
    main()
