# Kai Schedule Prompt — Hubble → Google Sheets Daily Dump (v2)

**Sheet**: https://docs.google.com/spreadsheets/d/1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw/edit
**Schedule**: Daily at 5:00 AM PT
**Purpose**: Pre-bake the Accelerate 2.0 AMER roster from Hubble into a Google Sheet so downstream agents can read it without depending on the Hubble MCP.

---

## Prompt (copy into kai schedule)

```
You are a data pipeline agent. Your job is to run a Hubble query and write the results to a Google Sheet. Follow these steps exactly. Do not skip steps or summarize — execute each one.

## Step 1: Run the row count query

Run this SQL on Hubble to get the total number of rows:

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

Save the total_rows value. You will use it to know when you are done.

If this query fails, retry once. If it fails again, stop and report the error.

## Step 2: Create a new tab in the Google Sheet

Target spreadsheet ID: 1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw

First, list the existing tabs in the spreadsheet. If a tab with today's date (YYYY-MM-DD format) already exists, delete it — this is a re-run and you will recreate it fresh.

Then create a new tab named with today's date in YYYY-MM-DD format (e.g., 2026-05-29).

Write the header row to cell A1 with these 19 columns:

project_id, project_name, project_lead_user_name, account_executive, project_geography, account_segment, project_status, accelerate_type, overall_project_health, sfdc_aonr, kantata_start_date, kantata_end_date, sfdc_opp_link, kantata_workspace_link, primary_contact_email, csat_link, stripe_account_ids, days_since_last_health_report, last_health_report_text

## Step 3: Fetch and write data in batches of 50

You will fetch data in batches of 50 rows using OFFSET/LIMIT. Start with OFFSET 0 and increment by 50 each iteration.

For each batch, run this SQL on Hubble (replacing {OFFSET} with the current offset value: 0, then 50, then 100, etc.):

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
ORDER BY p.project_lead_user_name, days_since_last_health_report DESC, p.project_id
OFFSET {OFFSET} LIMIT 50

After each batch query returns results:

1. Format each row as a string array with 19 elements (one per column), in the same order as the headers.
2. Formatting rules:
   - stripe_account_ids: This column contains JSON arrays like ["acct_123","acct_456"]. Parse the JSON array and write the values as comma-separated in a single cell (e.g., "acct_123,acct_456"). If null or empty, write empty string "".
   - Numeric values (project_id, sfdc_aonr, days_since_last_health_report): convert to their string representation.
   - Null values: write as empty string "".
   - All other values: write as-is, converting to string.
3. Append the formatted rows to the tab using append_to_google_drive_sheet with sheet_name set to today's date (YYYY-MM-DD).
4. Keep a running count of total rows written.

Then increment OFFSET by 50 and run the next batch.

**Stop condition**: Stop when a batch returns fewer than 50 rows (including 0 rows). After writing that final partial batch, you are done with data.

**Error handling**: If a batch query fails, retry it once with the same OFFSET. If it fails again, skip that batch (log the OFFSET that failed), increment OFFSET by 50, and continue to the next batch. Do not abort the entire pipeline for one failed batch.

## Step 4: Verify row count

After all batches are written, compare total rows written to the total_rows from Step 1. They should match within 5%. If the difference is larger, note it in the report but do not fail.

## Step 5: Clean up old tabs

List all tabs in the spreadsheet. Excluding any tab named "Sheet1", count the remaining tabs. If there are 8 or more date-named tabs, delete the one whose name (interpreted as a YYYY-MM-DD date) is the earliest. Never delete a tab named today or "Sheet1".

## Step 6: Report

Report exactly this format:
"Done. Wrote [N] rows to tab [YYYY-MM-DD] in [B] batches. Total expected: [T]. [Deleted tab YYYY-MM-DD / No tabs deleted]."

Where:
- N = total rows written across all batches
- B = number of batch queries executed (including the final partial/empty one)
- T = total_rows from Step 1
```

---

## Changelog from v1

| Issue in v1 | Fix in v2 |
|---|---|
| Single query returns 500K+ chars, triggers character-split pagination that breaks JSON records at page boundaries | LIMIT/OFFSET batching (50 rows per query, ~60KB each) ensures every response contains complete, parseable JSON |
| No explicit loop structure — agent must infer iteration | Explicit batch loop with OFFSET increment and stop condition |
| Non-deterministic ORDER BY causes duplicate/missing rows across batches | Added `p.project_id` tiebreaker to ORDER BY clause for deterministic pagination |
| No row count verification | Count query upfront + post-write verification |
| "Delete oldest tab" ambiguous when "Sheet1" exists | Explicit "not counting Sheet1" exclusion |
| No idempotency on re-run (duplicate tab name fails) | Check for existing tab with today's date, delete if exists before creating fresh |
| No partial failure handling | Per-batch retry + continue on failure (don't abort entire pipeline) |
| No reporting of batch count | Report includes batch count for debuggability |

## Testing results

| Test | Result |
|---|---|
| LIMIT 50 response size | 60KB — fits in single MCP response, no character-split pagination |
| LIMIT 50 record completeness | All 50 records are valid, complete JSON objects |
| ORDER BY without tiebreaker | **BUG**: duplicate rows at OFFSET boundaries (ECCO appeared in both batch 1 and 2) |
| ORDER BY with `p.project_id` tiebreaker | **FIXED**: zero overlap, deterministic sort confirmed across batches |
| Total row count | 545 rows → 11 batches (10×50 + 1×45) |
| Final batch (OFFSET 540) | Returns 5 rows → stop condition triggers correctly |
| Sheet tab creation | Works, returns sheet_id |
| Sheet append after headers | Data lands in A2 onward, headers preserved |
| Multi-batch sequential append | Rows accumulate correctly |
| stripe_account_ids formatting | JSON array `["acct_1","acct_2"]` → `acct_1,acct_2` |
| Null handling | null → empty string "" |
| Numeric formatting | 195761.68 → "195761.68", 999 → "999" |

## Performance estimate for Kai

- 1 count query + 11 data queries + 11 append calls + 1 tab create + 1 tab list + possible 1 tab delete
- ~26 tool calls total
- Expected runtime: 3-5 minutes
