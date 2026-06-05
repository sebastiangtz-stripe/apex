#!/usr/bin/env python3
"""
Fetch Hubble roster data from a Google Sheet and write data/hubble-snapshot.json.

The Google Sheet is populated daily by a Kai schedule that runs the Hubble query
and outputs CSV. This script reads the sheet data (passed as a JSON 2D array from
the get_google_drive_sheet_in_spreadsheet MCP tool), transforms it back into the
snapshot schema that hubble-reconcile.py expects, and writes to disk.

Usage:
  # Agent pipes MCP tool output (JSON 2D array):
  echo '<json>' | python3 scripts/fetch-hubble-sheet.py --stdin

  # Or from a file:
  python3 scripts/fetch-hubble-sheet.py --file /tmp/sheet_data.json

  # With explicit tab name (for metadata):
  python3 scripts/fetch-hubble-sheet.py --stdin --tab-name 2026-05-30

Reads .env for:
  HUBBLE_LEAD_FILTER   — filters rows to this user's projects (required)
  HUBBLE_SHEET_ID      — written to snapshot metadata (optional)

Output: data/hubble-snapshot.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = WORKSPACE_ROOT / "data" / "hubble-snapshot.json"
ENV_FILE = WORKSPACE_ROOT / ".env"

COLUMNS = [
    "project_id",
    "project_name",
    "project_lead_user_name",
    "account_executive",
    "project_geography",
    "account_segment",
    "project_status",
    "accelerate_type",
    "overall_project_health",
    "sfdc_aonr",
    "kantata_start_date",
    "kantata_end_date",
    "sfdc_opp_link",
    "kantata_workspace_link",
    "primary_contact_email",
    "csat_link",
    "stripe_account_ids",
    "days_since_last_health_report",
    "last_health_report_text",
]

INT_FIELDS = {"project_id", "days_since_last_health_report"}
FLOAT_FIELDS = {"sfdc_aonr"}


def load_env() -> dict[str, str]:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def parse_cell(col: str, val: str):
    """Convert a sheet cell string back to the typed value hubble-reconcile expects."""
    if val is None or str(val).strip() == "":
        return None

    val = str(val).strip()

    if col in INT_FIELDS:
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None

    if col in FLOAT_FIELDS:
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    if col == "stripe_account_ids":
        if not val:
            return None
        try:
            arr = json.loads(val)
            if isinstance(arr, list):
                return [str(a) for a in arr if a]
        except (json.JSONDecodeError, TypeError):
            pass
        parts = [s.strip() for s in val.split(",") if s.strip()]
        return parts if parts else None

    return val if val else None


def rows_to_projects(rows: list[list[str]], header: list[str]) -> list[dict]:
    """Convert 2D array (minus header) into list of project dicts."""
    col_indices = {}
    for i, h in enumerate(header):
        h_clean = h.strip().lower()
        for col in COLUMNS:
            if h_clean == col.lower():
                col_indices[col] = i
                break

    projects = []
    for row in rows:
        project = {}
        for col in COLUMNS:
            idx = col_indices.get(col)
            if idx is not None and idx < len(row):
                project[col] = parse_cell(col, row[idx])
            else:
                project[col] = None
        if project.get("project_id") is not None:
            projects.append(project)
    return projects


def filter_by_lead(projects: list[dict], lead_filter: str) -> list[dict]:
    """Filter projects to those matching the lead filter (full name, case-insensitive)."""
    lead_lower = lead_filter.strip().lower() if lead_filter else ""
    if not lead_lower:
        return projects
    return [
        p for p in projects
        if p.get("project_lead_user_name")
        and lead_lower in p["project_lead_user_name"].lower()
    ]


def main():
    parser = argparse.ArgumentParser(description="Convert Google Sheet data to hubble-snapshot.json")
    parser.add_argument("--stdin", action="store_true", help="Read JSON 2D array from stdin")
    parser.add_argument("--file", type=str, help="Read JSON 2D array from file")
    parser.add_argument("--tab-name", type=str, default="", help="Sheet tab name (for metadata)")
    parser.add_argument("--no-filter", action="store_true", help="Skip lead filter (include all rows)")
    args = parser.parse_args()

    if args.stdin:
        raw = sys.stdin.read()
    elif args.file:
        raw = Path(args.file).read_text()
    else:
        print("Error: provide --stdin or --file", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    if not data or not isinstance(data, list) or not isinstance(data[0], list):
        print("Error: expected a 2D array with a header row", file=sys.stderr)
        sys.exit(1)

    header = data[0]
    rows = data[1:]

    env = load_env()
    lead_filter = env.get("HUBBLE_LEAD_FILTER", "")
    sheet_id = env.get("HUBBLE_SHEET_ID", "")

    all_projects = rows_to_projects(rows, header)

    if args.no_filter:
        filtered = all_projects
    else:
        if not lead_filter or lead_filter == "Your Full Name":
            print("Error: HUBBLE_LEAD_FILTER not configured in .env", file=sys.stderr)
            sys.exit(1)
        filtered = filter_by_lead(all_projects, lead_filter)

    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "lead_filter": lead_filter,
        "source": "google_sheet",
        "sheet_id": sheet_id,
        "tab_name": args.tab_name,
        "row_count": len(filtered),
        "projects": filtered,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(snapshot, indent=2, default=str) + "\n")

    print(json.dumps({
        "status": "ok",
        "output": str(OUTPUT_PATH),
        "total_rows_in_sheet": len(all_projects),
        "rows_after_filter": len(filtered),
        "lead_filter": lead_filter,
        "tab_name": args.tab_name,
    }))


if __name__ == "__main__":
    main()
