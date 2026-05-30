#!/usr/bin/env python3
"""
Formats Hubble query results JSON into a 2D string array ready for Google Sheets.

Usage:
    echo '<hubble_results_json>' | python3 format-hubble-to-sheets.py
    python3 format-hubble-to-sheets.py < results.json

Input:  JSON array of objects (the "results" field from a Hubble query response)
Output: JSON 2D array of strings, one sub-array per row, ready for append_to_google_drive_sheet
"""
import json
import sys

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


def format_cell(val, col):
    if val is None:
        return ""
    if col == "stripe_account_ids":
        try:
            ids = json.loads(val)
            if isinstance(ids, list):
                return ",".join(ids)
        except (json.JSONDecodeError, TypeError):
            pass
        return str(val)
    return str(val)


def format_results(results):
    rows = []
    for r in results:
        row = [format_cell(r.get(c), c) for c in COLUMNS]
        rows.append(row)
    return rows


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON input: {e}"}))
        sys.exit(1)

    if isinstance(data, dict) and "results" in data:
        results = data["results"]
    elif isinstance(data, list):
        results = data
    else:
        print(json.dumps({"error": "Expected JSON array or object with 'results' key"}))
        sys.exit(1)

    formatted = format_results(results)
    print(json.dumps(formatted))
