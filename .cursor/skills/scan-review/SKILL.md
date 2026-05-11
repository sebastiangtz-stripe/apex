---
name: scan-review
description: >-
  Runs incremental email and Slack scans across all active merchants by fanning out
  to /merchant-scanner subagents, then runs /comms-analyst per merchant to propose
  auto-closures and new action items. Use when the user says "scan email",
  "scan Slack", "check all projects", "review open items", or "what's new".
---

# Scan & Review Pipeline

Three phases: Handover sweep (find new merchants), Scan (log comms via `/merchant-scanner`), Review (analyze via `/comms-analyst`, then dual-write to Asana + local).

The skill is an orchestrator. The heavy lifting (Gmail/Slack fetches, full `raw/comms.md` reads, dedup logic) lives in the subagents so the main thread stays clean.

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
   `scripts/handover-create.py` against each. Each successful bootstrap
   creates `projects/active/<slug>/`, the Asana task, runs
   `hubble-reconcile.py --backfill`, and updates state.
3. Hold the per-bootstrap results for the final summary's `New Handovers`
   section. If `proposals` is empty, this phase is a silent no-op.

Important: Phase 0 runs **before** Phase 1's `ls projects/active/` so any
projects bootstrapped here are immediately visible to the per-merchant scan.

## Phase 1: Scan

1. List all active merchants: `ls projects/active/`.
2. Fan out one `/merchant-scanner` invocation per merchant **in parallel** (single message with N tool calls).
3. Each subagent returns a small JSON summary `{ slug, new_emails, new_slack_threads, new_contacts, headline, errors }`. The full message bodies stay inside the subagent — they never enter your context.
4. Aggregate the headlines into a one-block report: `<merchant>: <headline>`. Surface any errors and any `new_contacts` for human review.

## Phase 2: Review

For each merchant where `new_emails + new_slack_threads > 0` (or where the user explicitly asks to review):

1. Fan out one `/comms-analyst` invocation per merchant **in parallel**.
2. Each subagent returns proposals (read-only): `{ auto_close[], new_items[], waiting_on_merchant[], commitments[], dedupe_skipped[] }`.
3. **Main thread executes the dual-write** (the analyst is read-only, so writes happen here):
   - For each `auto_close` with `confidence: high`: mark `[x]` in `action-items.md` with ` — Completed: YYYY-MM-DD (sent via <alias>)` suffix; `PUT /tasks/{subtask_gid}` in Asana with `{ completed: true }`.
   - For each `auto_close` with `confidence: medium|low`: surface for human confirmation, do not auto-apply.
   - For each `new_items[]`: append to `action-items.md` (format `- [ ] #tag — Description — Complexity: L/M/H — Owner — Due — Source`); create Asana subtask via `POST /tasks/{parent_gid}/subtasks` with `name` set to the plain action-verb description (no `#tag` prefix), plus due_on and notes (see "Subtask notes body" below); multi-home to Action Items project + section by urgency; set Tag and Complexity custom fields (Tag derived from the local `#tag`); persist new `subtask_gid` to `asana.json`.
   - For each `commitments[]` from the analyst: persist to `projects/active/<slug>/commitments.md` (see "Commitments persistence" below).

### Commitments persistence

After Phase 2, for each merchant whose analyst returned `commitments[]`, upsert into `projects/active/<slug>/commitments.md`. Each commitment is one line:

```
- [ ] <YYYY-MM-DD made_on> — Promised: <stated due or "+5bd"> — "<promise>" — Source: <comms.md ref> — Status: open|fulfilled|overdue
```

Upsert rules:
- Match by normalized promise text (lowercased, whitespace-collapsed). If the same promise already exists, update `Status` only.
- Mark `Status: fulfilled` when an outbound email matches the promise (use the same fuzzy-match logic the analyst uses for `auto_close`).
- Mark `Status: overdue` when `today > due_date` and Status is still `open`.
- Mark `[x]` when `Status: fulfilled`. Leave `[ ]` for `open` and `overdue`.

If `commitments.md` doesn't exist, create with the header:

```
# Commitments — <Merchant>

Tracks explicit promises [YOUR_NAME] made in outbound comms. Auto-maintained by the
scan-review skill. Surfaced at startup when any are `overdue`.

## Open / Overdue

## Fulfilled
```

Move fulfilled items to the bottom section to keep the open list short.

### Subtask notes body

Compose the `notes` field of the Asana subtask from the analyst's `notes` plus any `suggested_resources`. Format:

```
<analyst notes — 1-2 sentence context>

Suggested Resources:
- Email — "Re: proration on plan change" — https://mail.google.com/mail/u/0/#inbox/abc123
- Slack — #proj-example-merchant thread 2026-04-23 — https://stripe.slack.com/archives/C0XXXX/p1714000000000000
- Docs (verify) — Billing — Upgrade/downgrade proration — https://docs.stripe.com/billing/subscriptions/upgrade-downgrade
```

Render rules:
- Skip the entire "Suggested Resources:" section if `suggested_resources` is empty or absent.
- Kind label maps to: `email` → `Email`, `slack` → `Slack`, `doc` → `Docs`, `ref` → `Ref`.
- For `kind: "doc"` items with `verify: true`, append ` (verify)` to the kind label (e.g. `Docs (verify)`).
- For items with `url: null` (the `ref` fallback when no permalink exists), drop the trailing `— <url>` and render as plain text after the label (e.g. `- Ref — comms.md 2026-04-23 — re: proration`).
- Local `action-items.md` is unchanged — resources live only in the Asana subtask body.

## Phase 3: Triage Summary

Before rendering, run the **stale-draft sweep** (script-driven, ~1s):

```
python3 scripts/stale-drafts.py --threshold-days 7 --json
```

Parse the JSON; group by slug. Surface in the summary as a `Stale Drafts` section so
forgotten drafts don't accumulate.

Present in this format:

```
## Scan & Review Summary — YYYY-MM-DD

### New Handovers (N bootstrapped, M skipped)
- [Merchant] (`<slug>`) — AE @<ae>, AONR <aonr> — Asana created, Hubble <ok|skipped>
- (or: "no new handovers")

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
- [Merchant]: <draft-name> — Nd old — `<path>`

### No Activity
- [Merchant list]
```

If the stale-drafts sweep returns empty, omit that section entirely.

## Hard rules

- **Always fan out in parallel** (single tool-call message with N invocations). Never iterate sequentially.
- **Scans never create action items.** That is exclusively Phase 2's job.
- **Dedup happens inside subagents.** Don't re-check it in the main thread.
- **Asana writes only happen in the main thread.** `comms-analyst` is read-only.
