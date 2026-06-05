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

## Phase 0.5: Cross-merchant contamination check

Run `python3 scripts/cross-merchant-audit.py --json` (~2s, read-only). If it
returns suspect entries, surface them in the final triage summary under a
`Cross-merchant Contamination` section. Do not auto-move entries.

## Phase 1a: Fetch (LLM fan-out)

1. List all active merchants: `ls projects/active/`.
2. For each merchant, read:
   - `PROJECT.md` → extract `Email search` query and `Slack channels`
   - `scan-state.json` → extract `last_email_scan`, `last_slack_scan`, and `slack_thread_state`
   - `hubble.json` → extract `primary_contact_email` (fallback for email query construction)
3. Apply 4-hour TTL: skip merchants whose `last_email_scan` < 4 hours old.
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
8. Aggregate returns — note any errors for the triage summary.

The fetch subagent does NO filtering, NO dedup, NO file writes to project folders. It only calls MCP tools and dumps raw results to staging.

## Phase 1b: Ingest (Python script)

Run `python3 scripts/ingest-comms.py` to process all staging files at once.

The script handles deterministically:
- Email dedup against `scan-state.json` (by message_id)
- Slack dedup at message level: tracks `slack_thread_state` with `last_message_ts` per thread — only new replies are appended (not the full thread again)
- Identity gate (quarantines messages that don't match the merchant's identity model)
- Writes to `raw/comms.md` (full verbatim entry)
- Writes to `timeline.md` (structured metadata with `_pending_` summary)
- Contact discovery (patches PROJECT.md email query + Key Contacts)
- Updates `scan-state.json`

The script outputs JSON to stdout with per-merchant stats. Parse this to know which merchants have new content for Phase 2.

## Phase 2: Review

For each merchant where `new_emails + new_slack_threads > 0` in the ingest report:

1. Fan out one `/comms-analyst` invocation per merchant **in parallel**.
2. Each subagent returns proposals: `{ auto_close[], new_items[], waiting_on_merchant[], commitments[], dedupe_skipped[], inline_gaps[], asana_comments[], timeline_summaries[] }`.
3. **Persist each proposal to disk BEFORE any writes**, then invoke the script-driven applier:
   - Write each analyst return to `data/scan-proposals/<slug>-<YYYY-MM-DD>.json`.
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
