# Kai Schedule Prompt — Hubble → Google Sheets Daily Dump

**Sheet**: https://docs.google.com/spreadsheets/d/1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw/edit
**Schedule**: Daily at 5:00 AM PT
**Purpose**: Pre-bake the Accelerate 2.0 AMER roster from Hubble into a Google Sheet so downstream agents can read it without depending on the Hubble MCP.

---

## Prompt (copy into kai schedule)

```
You are a data pipeline agent. Your job is to run a Hubble query and write the results to a Google Sheet. Follow these steps exactly.

## Step 1: Run the Hubble query

Run this SQL on Hubble:

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
LEFT JOIN custom_fields cf
  ON p.project_id = cf.project_id
LEFT JOIN accel_type at
  ON p.project_id = at.project_id
LEFT JOIN opp_merchant om
  ON p.opportunity_id = om.opportunity_id
WHERE
  p.department = 'ISD'
  AND p.ds_deployment_start >= DATE '2025-02-01'
  AND p.project_status = 'In Progress'
  AND p.project_geography = 'AMER'
  AND at.accelerate_type = 'Accelerate 2.0'
ORDER BY p.project_lead_user_name, days_since_last_health_report DESC

If the query fails or times out, retry once. If it fails again, stop and report the error.

## Step 2: Create a new tab in the Google Sheet

Target sheet: https://docs.google.com/spreadsheets/d/1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw/edit

Create a new tab named with today's date in YYYY-MM-DD format (e.g., 2026-05-29).

## Step 3: Write headers and data

In the new tab, write:

Row 1 (headers): project_id, project_name, project_lead_user_name, account_executive, project_geography, account_segment, project_status, accelerate_type, overall_project_health, sfdc_aonr, kantata_start_date, kantata_end_date, sfdc_opp_link, kantata_workspace_link, primary_contact_email, csat_link, stripe_account_ids, days_since_last_health_report, last_health_report_text

Row 2 onward: one row per query result.

Formatting rules:
- The stripe_account_ids column contains arrays. Write them as comma-separated values in a single cell (e.g., "acct_123,acct_456"). If null or empty, leave the cell blank.
- All other values: write as-is. Nulls become blank cells.
- Do NOT apply any formatting, colors, or conditional formatting to the sheet.

## Step 4: Clean up old tabs

After writing the new tab, count the total number of tabs in the spreadsheet. If there are 8 or more tabs, delete the oldest one (the tab whose name, interpreted as a date, is the earliest). Never delete a tab named today.

## Step 5: Confirm

Report: "Done. Wrote [N] rows to tab [YYYY-MM-DD]. [Deleted tab YYYY-MM-DD / No tabs deleted]."
```

---

## Notes for workspace integration (Phase 2)

Once this schedule is confirmed working, the workspace changes are:
1. New script `scripts/fetch-hubble-sheet.py` — reads the latest tab from the sheet, writes `data/hubble-snapshot.json` in the existing format.
2. `hubble-analyst` subagent calls `fetch-hubble-sheet.py` instead of the Hubble MCP.
3. `HUBBLE_SNAPSHOT_TTL_HOURS` logic stays — if the latest tab date is >24h old, surface a warning.
4. The `.env` gets a new key: `HUBBLE_SHEET_ID=1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw`.
