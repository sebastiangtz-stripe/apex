---
name: scan-review
description: >-
  Runs incremental email and Slack scans across all active merchants by fanning out
  fetch-relay subagents, ingesting via Python script, then running /comms-analyst per
  merchant to propose auto-closures and new action items. Use when the user says "scan
  email", "scan Slack", "check all projects", "review open items", or "what's new".
---

# Scan & Review Pipeline

Four stages: Handover sweep, Fetch + Ingest (split into LLM fetch relay and Python ingest), Review (comms-analyst proposals), Apply (script-driven dual-write).

The key architectural principle: **the LLM never writes to project files during scanning.** All file writes are deterministic Python scripts. The LLM does two things: (1) call MCP tools to fetch raw data, (2) reason about what action items to create. Everything else is code.

## Phase 0: Handover sweep

Before per-merchant scanning, check the handover channel(s) for any new
merchants that need a project bootstrapped.

1. Invoke `/handover-scanner` with no args. It reads `.env` for the channel
   IDs + the user's Slack handle, dedups against
   `data/handover-state.json::processed_threads`, parses any new candidate
   threads through `scripts/handover-parse.py`, and returns
   `{ proposals: [], skipped: [], errors: [], headline }`.
2. If `proposals` is non-empty, hand them to the `handover-bootstrap` skill
   (scan mode — proposals are already structured, no re-parsing). The skill
   surfaces a one-line preview per proposal, then runs
   `scripts/handover-create.py` against each.
3. Hold the per-bootstrap results for the final summary's `New Handovers`
   section. If `proposals` is empty, this phase is a silent no-op.

Important: Phase 0 runs **before** Phase 1's `ls projects/active/` so any
projects bootstrapped here are immediately visible to the per-merchant scan.

**Guardrail**: If Phase 0 returns 0 proposals, no new projects are created during this
scan run — regardless of what appears in Phase 1a output. Phase 1a fetch results
(including messages visible in handover channels) MUST NOT trigger `handover-bootstrap`.
Only Phase 0 proposals can trigger project bootstrapping.

## Phase 0.5: Cross-merchant contamination check

Run `python3 scripts/cross-merchant-audit.py --json` (~2s, read-only). If it
returns suspect entries, surface them in the final triage summary under a
`Cross-merchant Contamination` section. Do not auto-move entries.

## Phase 1a-pre: Case Studio fetch (Core projects only)

Pull email messages from Case Studio (Hubble) for all Accelerate Core projects.
This runs BEFORE the Gmail fan-out so cross-source dedup works correctly.

1. Check if any active project has `scan_source: core` in PROJECT.md (quick grep).
   If none → skip this phase entirely.
2. Read `data/cs-scan-state.json` for `last_scan` timestamp.
   If `last_scan` is null, default to 30 days ago.
3. Read `templates/cs-incremental.sql`, substitute:
   - `{{consultant_username}}` from `.env` `CONSULTANT_USERNAME`
   - `{{since_timestamp}}` from `last_scan` (format: `YYYY-MM-DD HH:MM:SS`, space separator — NOT `T`)
4. Execute the rendered SQL via `run_hubble_query` MCP tool.
   Save raw results to `data/cs-raw-results.json` with schema:
   `{ "query_id": "...", "query_status": "success", "row_count": N, "results": [...] }`
5. Run `python3 scripts/fetch-cs.py` — splits results into per-merchant staging files
   (`data/staging/<slug>-<YYYY-MM-DD>-cs.json`).
6. If `fetch-cs.py` reports unmapped cases, surface them in the triage summary under
   "Unmapped CS Cases — run `manage-case-map.py --add <case_id> <slug>`".
7. Continue to Phase 1a (Gmail fan-out) for ALL projects — Gmail remains the source
   for managed projects and the gap-fill for core projects.

Notes:
- The Hubble query is ONE query for ALL cases (bulk). It joins `mongo.sfdcemailmessages`
  to `analytics.userops_cases` filtered by `assignee_email` and `latest_queue = 'Accelerate Core'`.
- `message_date` returns as Unix epoch integer — `fetch-cs.py` converts to ISO.
- `is_incoming=false` means sent via accelerate@ (definitive outbound). `is_incoming=true`
  still needs from_address check (consultant-sent-via-Gmail appears as incoming in CS).
- CS staging files are processed first by `ingest-comms.py` (sorted by filename).

## Phase 1a: Fetch (LLM fan-out)

1. List all active merchants: `ls projects/active/`.
2. For each merchant, read:
   - `PROJECT.md` → extract `Email search` query and `Slack channels`
   - `scan-state.json` → extract `last_email_scan`, `last_slack_scan`, and `slack_thread_state`
   - `hubble.json` → extract `primary_contact_email` (fallback for email query construction)
   - **Slack channel extraction rule**: Parse ONLY the literal `**Slack channels**:` field
     line in PROJECT.md. Never regex the entire file. The file contains channel IDs embedded
     in Handover: URLs and other contexts — those MUST be ignored. If the field value is
     "TBD" or empty, pass an empty `slack_channels` list.
3. Apply 4-hour TTL: skip merchants whose `last_email_scan` < 4 hours old.
   Exception: if the user explicitly requested this scan (e.g. "scan email", "scan all",
   "re-scan") rather than auto-startup, bypass the TTL and scan all eligible projects
   regardless of `last_email_scan` timestamp.
4. **Construct email_query** (never skip a merchant just because Email search is TBD):
   - If `Email search` in PROJECT.md is populated and not "TBD" → use it as-is.
   - Else if `primary_contact_email` exists in `hubble.json`:
     - Non-generic domain (not gmail/icloud/hotmail/outlook/yahoo) → `from:<domain> OR to:<domain>`
     - Generic/personal domain → `from:"<Merchant Name>" OR from:<email> OR to:<email>`
   - Else → fall back to merchant name search: `"<Merchant Name>"` (H1 from PROJECT.md).
   - **Always invoke the scanner** — even a name-based fallback surfaces contacts that can be added to PROJECT.md later.
5. Build `active_threads` list from `slack_thread_state`: include threads where `last_message_ts` is within the last 30 days (skip ancient threads to avoid quota waste). Format: `[{ channel_id, thread_ts }]`.
6. Fan out one `/merchant-scanner` invocation per eligible merchant **in parallel** (single message with N tool calls). Pass each subagent:
   - `slug`
   - `email_query` (constructed per step 4 — never "TBD")
   - `slack_channels` (list of channel IDs)
   - `email_since` (from scan-state.json)
   - `slack_since` (from scan-state.json)
   - `active_threads` (from step 5 — enables re-fetch of known threads for new replies)
7. Each subagent writes a staging file to `data/staging/<slug>-<YYYY-MM-DD>.json` and returns `{ slug, emails_fetched, slack_threads_fetched, errors }`.
   Each staging file MUST include a top-level `"fetched_at"` field set to the ISO timestamp
   (UTC, seconds precision, Z suffix) of when the MCP tool responses were received.
8. Aggregate returns — note any errors for the triage summary.

The fetch subagent does NO filtering, NO dedup, NO file writes to project folders. It only calls MCP tools and dumps raw results to staging.

## Phase 1b: Ingest (Python script)

Run `python3 scripts/ingest-comms.py` to process all staging files at once.

The script handles deterministically:
- **Source detection**: branches on `"source": "case_studio"` vs Gmail (default)
- **CS email dedup**: against `logged_cs_message_ids` in scan-state (by sfdc_id)
- **Gmail email dedup**: against `logged_email_ids` in scan-state (by message_id)
- **Cross-source dedup** (batch-level): for Core projects, Gmail messages are checked against CS messages ingested in the same batch. Matching messages are skipped (CS wins).
- **Hybrid direction detection** for CS: `is_incoming=false` → Outbound; `is_incoming=true` + from matches outbound addresses → Outbound; else → Inbound
- Slack dedup at message level: tracks `slack_thread_state` with `last_message_ts` per thread
- Identity gate (quarantines messages that don't match the merchant's identity model)
- Writes to `raw/comms.md` (full verbatim entry)
- Writes to `timeline.md` (structured metadata with `_pending_` summary)
- Contact discovery (patches PROJECT.md email query + Key Contacts)
- Updates `scan-state.json`

Processing order: CS staging files (`*-cs.json`) are sorted first, then Gmail files. This ensures cross-source dedup works correctly.

The script outputs JSON to stdout with per-merchant stats. Parse this to know which merchants have new content for Phase 2. Trigger condition: `new_emails + new_cs_emails + new_slack_threads > 0`.

## Phase 2: Review

For each merchant where `new_emails + new_cs_emails + new_slack_threads > 0` in the ingest report:

1. Fan out one `/comms-analyst` invocation per merchant **in parallel**.
2. Each subagent returns proposals: `{ auto_close[], new_items[], waiting_on_merchant[], commitments[], dedupe_skipped[], inline_gaps[], asana_comments[], timeline_summaries[] }`.
3. **Persist each proposal to disk BEFORE any writes**, then invoke the script-driven applier:
   - Write each analyst return to `data/scan-proposals/<slug>-<YYYY-MM-DD>.json`.
     Set `"apply_status": {}` (empty dict) when persisting. Never write it as a string.
     The applier expects a dict keyed by proposal item ID.
   - Once all JSONs are on disk, run `python3 scripts/apply-proposals.py --resume`.
   - For `commitments[]`: persist to `projects/active/<slug>/commitments.md` (see below).
   - Surface the applier's run report in the triage summary.

### Commitments persistence

After Phase 2, for each merchant whose analyst returned `commitments[]`, upsert into `projects/active/<slug>/commitments.md`. Each commitment is one line:

```
- [ ] <YYYY-MM-DD made_on> — Promised: <stated due or "+5bd"> — "<promise>" — Source: <comms.md ref> — Status: open|fulfilled|overdue
```

Upsert rules:
- Match by normalized promise text. If same promise exists, update `Status` only.
- Mark `Status: fulfilled` when an outbound matches the promise.
- Mark `Status: overdue` when `today > due_date` and Status is still `open`.
- Mark `[x]` when `Status: fulfilled`.

If `commitments.md` doesn't exist, create with the standard header.

### Subtask notes body

Compose the `notes` field from the analyst's `notes` + `suggested_resources`:

```
<analyst notes — 1-2 sentence context>

Suggested Resources:
- Email — "Re: proration" — https://mail.google.com/...
- Docs (verify) — Billing — Upgrade/downgrade proration — https://docs.stripe.com/...
```

Render rules:
- Skip "Suggested Resources:" section if empty.
- Kind maps: `email` → `Email`, `slack` → `Slack`, `doc` → `Docs`, `ref` → `Ref`.
- `verify: true` → append ` (verify)` to kind label.
- `url: null` → drop trailing URL, render as plain text.

## Phase 3: Triage Summary

Before rendering, run `python3 scripts/stale-drafts.py --threshold-days 7 --json`.

Present in this format:

```
## Scan & Review Summary — YYYY-MM-DD

### New Handovers (N bootstrapped, M skipped)
- [Merchant] (`<slug>`) — AE @<ae>, AONR <aonr>

### Ingest Report
- [Merchant]: N emails, M slack threads ingested, K quarantined, J contacts added

### Auto-Closed (N items)
- [Merchant] #reply — description — matched outbound "subject" on YYYY-MM-DD

### New Action Items Created (N items)
- [Merchant] #tag — description — Complexity: X — Due: YYYY-MM-DD

### Needs Human Review (medium/low confidence auto-closes)
- [Merchant] proposed close: ... — review and confirm

### Waiting on Merchant
- [Merchant]: last contacted YYYY-MM-DD (N days silent)

### New Contacts Discovered
- [Merchant]: <addr> — added to Key Contacts and Email query

### Stale Drafts (>7d unsent)
- [Merchant]: <draft-name> — Nd old

### Cross-merchant Contamination (if any)
- [Merchant]: entry likely belongs to <other-slug>

### No Activity
- [Merchant list]
```

## Hard rules

- **Fan out fetch subagents in parallel** (single message with N invocations).
- **The LLM never writes to project files during scan.** All writes go through `ingest-comms.py` or `apply-proposals.py`.
- **Scans never create action items.** That is exclusively Phase 2's job (comms-analyst).
- **Dedup and identity gate happen in Python** (`ingest-comms.py`), not in subagents or main thread.
- **Asana writes happen via apply-proposals.py** — never inline in the main thread.
- **comms-analyst is read-only.** It proposes; the script applies.
