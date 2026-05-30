# Kai Schedule Prompt — Hubble → Google Sheets Daily Dump (v3)

**Sheet**: https://docs.google.com/spreadsheets/d/1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw/edit
**Schedule**: Daily at 5:00 AM PT
**Purpose**: Pre-bake the Accelerate 2.0 AMER roster from Hubble into a Google Sheet so downstream agents can read it without depending on the Hubble MCP.

---

## Prompt (copy into kai schedule)

```
You are a data pipeline agent. Your job is to run a Hubble query and write the results to a Google Sheet using a deterministic Python formatter. Follow these steps exactly. Do not skip steps, do not improvise formatting — use the script provided.

## Step 1: Run the row count query

Run this SQL on Hubble:

WITH accel_type AS (
  SELECT project_id, accelerate_type
  FROM mavenlink.custom_fld_project_proserv_four
  WHERE day = date_to_day(CURRENT_DATE)
)
SELECT COUNT(*) AS total_rows
FROM communia_sales.agg_mavenlink_projects p
JOIN accel_type at ON p.project_id = at.project_id
WHERE
  p.department = 'ISD'
  AND p.ds_deployment_start >= DATE '2025-02-01'
  AND p.project_status = 'In Progress'
  AND p.project_geography = 'AMER'
  AND at.accelerate_type = 'Accelerate 2.0'

Save the total_rows value. If this query fails, retry once. If it fails again, stop and report the error.

## Step 2: Create a new tab in the Google Sheet

Target spreadsheet ID: 1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw

First, list the existing tabs. If a tab with today's date (YYYY-MM-DD) already exists, delete it.

Create a new tab named with today's date (YYYY-MM-DD).

Write the header row to A1:
project_id, project_name, project_lead_user_name, account_executive, project_geography, account_segment, project_status, accelerate_type, overall_project_health, sfdc_aonr, kantata_start_date, kantata_end_date, sfdc_opp_link, kantata_workspace_link, primary_contact_email, csat_link, stripe_account_ids, days_since_last_health_report, last_health_report_text

## Step 3: Fetch and write data in batches of 50

Loop with OFFSET starting at 0, incrementing by 50 each iteration.

### 3a. Run the data query

For each batch, run this SQL (replace {OFFSET} with 0, 50, 100, ...):

WITH custom_fields AS (
  SELECT
    p1.project_id,
    p1.email_user_primary AS primary_contact_email,
    p3."project csat link" AS csat_link,
    p3.sfdc_opportunity_owner AS account_executive
  FROM mavenlink.custom_fld_project_proserv p1
  LEFT JOIN mavenlink.custom_fld_project_proserv_three p3
    ON p1.project_id = p3.project_id
    AND p3.day = date_to_day(CURRENT_DATE)
  WHERE p1.day = date_to_day(CURRENT_DATE)
),
accel_type AS (
  SELECT project_id, accelerate_type
  FROM mavenlink.custom_fld_project_proserv_four
  WHERE day = date_to_day(CURRENT_DATE)
),
opp_merchant AS (
  SELECT
    opportunity_id,
    array_agg(merchant_id ORDER BY ts_merchant_created) AS stripe_account_ids
  FROM communia_sales.dim_opportunity_merchant
  WHERE is_real_merchant_id = true
    AND is_current = true
  GROUP BY opportunity_id
)
SELECT
  p.project_id,
  p.project_name,
  p.project_lead_user_name,
  cf.account_executive,
  p.project_geography,
  p.segment AS account_segment,
  p.project_status,
  at.accelerate_type,
  p.overall_project_health,
  p.incremental_onr AS sfdc_aonr,
  format_datetime(p.ds_deployment_start, 'yyyy-MM-dd') AS kantata_start_date,
  format_datetime(p.ds_deployment_end, 'yyyy-MM-dd') AS kantata_end_date,
  concat('https://stripe.lightning.force.com/lightning/r/Opportunity/',
    p.opportunity_id, '/view') AS sfdc_opp_link,
  concat('https://app.mavenlink.com/workspaces/',
    CAST(p.project_id AS varchar)) AS kantata_workspace_link,
  cf.primary_contact_email,
  cf.csat_link,
  om.stripe_account_ids,
  COALESCE(date_diff('day', p.ds_health_report_date, CURRENT_DATE), 999)
    AS days_since_last_health_report,
  p.overall_health_description AS last_health_report_text
FROM communia_sales.agg_mavenlink_projects p
LEFT JOIN custom_fields cf ON p.project_id = cf.project_id
LEFT JOIN accel_type at ON p.project_id = at.project_id
LEFT JOIN opp_merchant om ON p.opportunity_id = om.opportunity_id
WHERE
  p.department = 'ISD'
  AND p.ds_deployment_start >= DATE '2025-02-01'
  AND p.project_status = 'In Progress'
  AND p.project_geography = 'AMER'
  AND at.accelerate_type = 'Accelerate 2.0'
ORDER BY p.project_lead_user_name, days_since_last_health_report DESC, p.project_id
OFFSET {OFFSET} LIMIT 50

### 3b. Format results using this script

After getting results from Hubble, execute this Python code to format them. Pass the results JSON (the "results" array from the Hubble response) as the variable `results`:

```python
import json

COLUMNS = [
    "project_id", "project_name", "project_lead_user_name", "account_executive",
    "project_geography", "account_segment", "project_status", "accelerate_type",
    "overall_project_health", "sfdc_aonr", "kantata_start_date", "kantata_end_date",
    "sfdc_opp_link", "kantata_workspace_link", "primary_contact_email", "csat_link",
    "stripe_account_ids", "days_since_last_health_report", "last_health_report_text",
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

formatted_rows = []
for r in results:
    row = [format_cell(r.get(c), c) for c in COLUMNS]
    formatted_rows.append(row)

# formatted_rows is now the 2D string array to write to the sheet
```

### 3c. Write to sheet

Pass `formatted_rows` as the `data_json` parameter to `append_to_google_drive_sheet` with `sheet_name` set to today's date (YYYY-MM-DD).

Keep a running count of rows written.

### 3d. Loop control

- Increment OFFSET by 50
- **Stop when**: a batch returns fewer than 50 rows (including 0). Write the final partial batch, then exit the loop.
- **On failure**: retry the same OFFSET once. If it fails again, skip it, log the offset, increment by 50, continue.

## Step 4: Verify

Compare total rows written to total_rows from Step 1. Should match within 5%. Note discrepancy if larger but do not fail.

## Step 5: Clean up old tabs

List all tabs. Excluding "Sheet1", if there are 8+ date-named tabs, delete the one with the earliest date. Never delete today's tab or "Sheet1".

## Step 6: Report

"Done. Wrote [N] rows to tab [YYYY-MM-DD] in [B] batches. Total expected: [T]. [Deleted tab YYYY-MM-DD / No tabs deleted]."
```

---

## Key design decisions

| Decision | Why |
|---|---|
| LIMIT/OFFSET batching (50 rows) | Prevents response truncation. Each batch fits in a single MCP response (~60KB) |
| `ORDER BY ... p.project_id` tiebreaker | Eliminates duplicate/missing rows at batch boundaries (tested & confirmed) |
| Python formatter script | Deterministic — no LLM inference on data transformation. Same input always produces same output |
| Idempotent re-run (delete existing tab) | Safe to re-trigger without duplicating data |
| Per-batch error handling | One failed batch doesn't abort the entire pipeline |

## Follow-up optimization (optional)

Save the data query as a Hubble Query Template with an `offset` input parameter. Then the prompt shrinks to:
- "Run template ID {TEMPLATE_ID} with inputs `{"offset": "0"}`"
- This removes ~40 lines of SQL from the prompt
- SQL changes happen in Hubble, not in the Kai config
