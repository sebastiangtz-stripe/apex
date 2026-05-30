# Kai Schedule Prompt — Hubble → Google Sheets Daily Dump (v4 — Final)

**Sheet**: https://docs.google.com/spreadsheets/d/1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw/edit
**Schedule**: Daily at 5:00 AM PT
**Purpose**: Pre-bake the Accelerate 2.0 AMER roster into a Google Sheet for downstream agent consumption.

---

## Prompt (copy into kai schedule)

````
You are an unattended data pipeline agent. Run a Hubble query and write results to a Google Sheet. No human in the loop. Follow steps exactly, in order.

## Hard Rules

1. Never fabricate, paraphrase, summarize, or modify any cell value. All values come from Hubble results exactly as returned. Truncated strings are valid data — preserve them byte-for-byte.
2. Never type cell data directly into a tool call. Always write formatted data to a file using the Python script below, then read that file and pass its contents verbatim as `data_json`.
3. Never delete the live tab until the new run is fully verified. Use staging tab + atomic swap.
4. All dates use UTC. "Today" = current UTC date YYYY-MM-DD.
5. Continue on recoverable errors; never abort silently. Log every failure to the status block.
6. Emit the JSON status block at the end (Step 7).

## Constants

- SPREADSHEET_ID = `1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw`
- LIVE_TAB = `<today YYYY-MM-DD>`
- STAGING_TAB = `<today YYYY-MM-DD>-staging`
- BATCH_SIZE = 50
- MAX_TAB_RETENTION = 8 (date-named tabs, excluding Sheet1)
- COLUMNS (19, in order): project_id, project_name, project_lead_user_name, account_executive, project_geography, account_segment, project_status, accelerate_type, overall_project_health, sfdc_aonr, kantata_start_date, kantata_end_date, sfdc_opp_link, kantata_workspace_link, primary_contact_email, csat_link, stripe_account_ids, days_since_last_health_report, last_health_report_text

## Step 1 — Get expected row count

Run on Hubble:

```sql
WITH accel_type AS (
  SELECT project_id, accelerate_type
  FROM mavenlink.custom_fld_project_proserv_four
  WHERE day = date_to_day(CURRENT_DATE)
)
SELECT COUNT(*) AS total_rows
FROM communia_sales.agg_mavenlink_projects p
JOIN accel_type at ON p.project_id = at.project_id
WHERE p.department = 'ISD'
  AND p.ds_deployment_start >= DATE '2025-02-01'
  AND p.project_status = 'In Progress'
  AND p.project_geography = 'AMER'
  AND at.accelerate_type = 'Accelerate 2.0'
```

Save as `total_expected`. Retry once on failure (5s backoff). If second attempt fails → status "failed", failed_step "step_1_count", exit.

## Step 2 — Prepare the staging tab (non-destructive)

1. List all tabs in spreadsheet.
2. If STAGING_TAB already exists (prior failed run), delete it.
3. Create fresh STAGING_TAB.
4. Write header row to `STAGING_TAB!A1:S1` using COLUMNS order.
5. Do NOT touch LIVE_TAB in this step.

## Step 3 — Fetch and write data in batches

Loop: OFFSET = 0, 50, 100, ... until batch returns < 50 rows.

### 3a. Run the batch query

```sql
WITH custom_fields AS (
  SELECT
    p1.project_id,
    p1.email_user_primary AS primary_contact_email,
    p3."project csat link" AS csat_link,
    p3.sfdc_opportunity_owner AS account_executive
  FROM mavenlink.custom_fld_project_proserv p1
  LEFT JOIN mavenlink.custom_fld_project_proserv_three p3
    ON p1.project_id = p3.project_id AND p3.day = date_to_day(CURRENT_DATE)
  WHERE p1.day = date_to_day(CURRENT_DATE)
),
accel_type AS (
  SELECT project_id, accelerate_type
  FROM mavenlink.custom_fld_project_proserv_four
  WHERE day = date_to_day(CURRENT_DATE)
),
opp_merchant AS (
  SELECT opportunity_id,
         array_agg(merchant_id ORDER BY ts_merchant_created) AS stripe_account_ids
  FROM communia_sales.dim_opportunity_merchant
  WHERE is_real_merchant_id = true AND is_current = true
  GROUP BY opportunity_id
)
SELECT
  p.project_id, p.project_name, p.project_lead_user_name, cf.account_executive,
  p.project_geography, p.segment AS account_segment, p.project_status,
  at.accelerate_type, p.overall_project_health, p.incremental_onr AS sfdc_aonr,
  format_datetime(p.ds_deployment_start, 'yyyy-MM-dd') AS kantata_start_date,
  format_datetime(p.ds_deployment_end, 'yyyy-MM-dd') AS kantata_end_date,
  concat('https://stripe.lightning.force.com/lightning/r/Opportunity/', p.opportunity_id, '/view') AS sfdc_opp_link,
  concat('https://app.mavenlink.com/workspaces/', CAST(p.project_id AS varchar)) AS kantata_workspace_link,
  cf.primary_contact_email, cf.csat_link, om.stripe_account_ids,
  COALESCE(date_diff('day', p.ds_health_report_date, CURRENT_DATE), 999) AS days_since_last_health_report,
  p.overall_health_description AS last_health_report_text
FROM communia_sales.agg_mavenlink_projects p
LEFT JOIN custom_fields cf ON p.project_id = cf.project_id
LEFT JOIN accel_type at ON p.project_id = at.project_id
LEFT JOIN opp_merchant om ON p.opportunity_id = om.opportunity_id
WHERE p.department = 'ISD'
  AND p.ds_deployment_start >= DATE '2025-02-01'
  AND p.project_status = 'In Progress'
  AND p.project_geography = 'AMER'
  AND at.accelerate_type = 'Accelerate 2.0'
ORDER BY p.project_lead_user_name, days_since_last_health_report DESC, p.project_id
OFFSET {OFFSET} LIMIT 50
```

### 3b. Format batch using this Python script

Execute this script, passing the Hubble JSON results as input. The script writes chunk files to disk. You then read each file and pass its contents to the sheet tool.

```python
import json, os, sys

COLS = [
    "project_id","project_name","project_lead_user_name","account_executive",
    "project_geography","account_segment","project_status","accelerate_type",
    "overall_project_health","sfdc_aonr","kantata_start_date","kantata_end_date",
    "sfdc_opp_link","kantata_workspace_link","primary_contact_email","csat_link",
    "stripe_account_ids","days_since_last_health_report","last_health_report_text"
]
NULLS = {"", "nan", "none", "null", "None"}
MAX_CHUNK_BYTES = 4500

def to_cell(col, val):
    if val is None:
        return ""
    s = str(val)
    if s.strip().lower() in NULLS:
        return ""
    if col == "stripe_account_ids":
        try:
            arr = json.loads(s)
            return ",".join(arr) if isinstance(arr, list) else ""
        except Exception:
            return ""
    return s

def format_and_chunk(results, offset, out_dir):
    rows = [[to_cell(c, r.get(c)) for c in COLS] for r in results]
    os.makedirs(out_dir, exist_ok=True)
    chunks, cur, cur_bytes = [], [], 2  # 2 for outer []
    for row in rows:
        row_json = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        row_bytes = len(row_json.encode("utf-8"))
        if cur and cur_bytes + 1 + row_bytes > MAX_CHUNK_BYTES:
            chunks.append(cur)
            cur, cur_bytes = [], 2
        cur.append(row)
        cur_bytes += row_bytes + (1 if len(cur) > 1 else 0)
    if cur:
        chunks.append(cur)
    paths = []
    for i, chunk in enumerate(chunks):
        path = os.path.join(out_dir, f"b{offset:05d}_c{i:02d}.json")
        with open(path, "w") as f:
            json.dump(chunk, f, ensure_ascii=False, separators=(",", ":"))
        paths.append(path)
    return len(rows), paths

if __name__ == "__main__":
    results_json = sys.stdin.read()
    offset = int(sys.argv[1])
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "/tmp/hubble_chunks"
    results = json.loads(results_json)
    if isinstance(results, dict) and "results" in results:
        results = results["results"]
    n, paths = format_and_chunk(results, offset, out_dir)
    print(json.dumps({"rows": n, "chunk_paths": paths}))
```

### 3c. Append chunks to staging tab

For each chunk path:
1. Read the file.
2. Pass contents verbatim as `data_json` to `append_to_google_drive_sheet(spreadsheet_id=SPREADSHEET_ID, sheet_name=STAGING_TAB)`.
3. Do NOT edit, reformat, or "clean up" the JSON between read and write.

Track: `total_written`, `batches_run`.

### 3d. Error handling

- Query fails → retry once (5s backoff). Second failure → record `{offset, error}` in `batches_failed`, increment OFFSET, continue.
- Script fails → same as query failure.
- Append fails → retry once. Second failure → record and continue.
- If OFFSET 0 (first batch) fails → systemic problem. Status "failed", exit.

### 3e. Stop condition

Stop when batch returns < 50 rows. Process final partial batch, exit loop.

## Step 4 — Verify the staging tab

### 4a. Row count
Read `STAGING_TAB!A:A`, count non-empty data rows (exclude header). Compare to `total_expected`:
- |written - expected| / expected ≤ 0.05 → pass
- Otherwise → warning (continue)

### 4b. Non-null check
Confirm column A (project_id) is non-empty for every data row. If any row has empty project_id → status "failed" (no swap).

If Step 4 fails, leave STAGING_TAB for inspection. Do not swap.

## Step 5 — Atomic swap (only if Step 4 passed)

1. If LIVE_TAB exists, delete it.
2. Rename STAGING_TAB → LIVE_TAB.

This is the only point where live data changes. If anything before failed, prior day's data remains intact.

## Step 6 — Clean up old tabs

1. List all tabs.
2. Filter to YYYY-MM-DD date names (exclude Sheet1, exclude *-staging).
3. If count > MAX_TAB_RETENTION, delete the oldest.
4. Never delete LIVE_TAB or Sheet1.

## Step 7 — Emit status block

```json
{
  "run_date_utc": "YYYY-MM-DD",
  "spreadsheet_id": "1LvjzgPleT3Uz6dG6Z6Rl4r0knv7Qkw-P4yc19ipnutw",
  "live_tab": "YYYY-MM-DD",
  "total_expected": 0,
  "total_written": 0,
  "batches_run": 0,
  "batches_failed": [],
  "deleted_tabs": [],
  "warnings": [],
  "status": "success | partial | failed",
  "failed_step": null
}
```

Status values:
- **success**: all steps passed, fidelity OK, row count within 5%
- **partial**: some batches failed but ≥95% written
- **failed**: Step 1 failed, OR first batch failed, OR Step 4 failed (no swap)

One-line human summary after the block:
`Done. Wrote [N] rows to tab [YYYY-MM-DD] in [B] batches. Expected: [T]. [Deleted tab X / No tabs deleted].`

## Operational Notes

- Hubble queries take 30-60s; do not treat slow responses as failures.
- Sheets API rate-limits at ~60 writes/min; on 429s sleep 5s and retry.
- Each chunk is ≤4500 bytes — fits in a single read_file output without truncation.
- Read-only verification (Step 4) reads FROM the spreadsheet, not from memory, to catch write corruption.
````

---

## Changelog v3 → v4

| From Kai's trial | Incorporated |
|---|---|
| Staging tab + atomic swap | ✅ Prevents serving partial data on mid-run failure |
| "Never fabricate/paraphrase cell values" | ✅ Hard rule #1 |
| "Never type data directly into tool call" | ✅ Hard rule #2 — always via file |
| JSON status block for monitoring | ✅ Step 7 with structured output |
| Fidelity verification (non-null check) | ✅ Step 4b (simplified — dropped random sampling which is fragile) |
| Chunk size ≤4500 bytes | ✅ Prevents read_file truncation |
| Rate limit handling (429 → sleep 5s) | ✅ Operational notes |
| First-batch failure = abort | ✅ Systemic problem detection |
| pandas dependency | ❌ Kept stdlib `json` only — fewer failure modes |
| CSV references | ❌ Fixed — Hubble returns JSON, not CSV |
| Random fidelity sampling | ❌ Simplified to non-null check — random sampling adds complexity with marginal value |
