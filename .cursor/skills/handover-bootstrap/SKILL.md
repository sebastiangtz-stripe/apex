---
name: handover-bootstrap
description: >-
  End-to-end pipeline for turning a Slack handover into a live project:
  parses the thread, creates projects/active/<slug>/ with PROJECT.md
  (HO/MAN/contact pre-filled), creates the Asana task, backfills Hubble,
  and updates handover-state.json. Use when the user pastes a Slack
  handover permalink or thread text, says "here's a handover", "new
  handover from Slack", "set up project from handover", "/handover",
  when scan-review Phase 0 surfaces unprocessed handovers from the
  handover-scanner subagent, OR when the user says "find handovers",
  "backfill handovers", "search for handover threads" (batch backfill
  mode for Hubble-scaffolded projects).
---

# Handover Bootstrap

Two entry points (paste vs. scan), one bootstrap path. Both end with
`scripts/handover-create.py` writing the project and chaining the Asana +
Hubble work.

## When to invoke

- **Paste mode**: user pastes a Slack permalink (matches
  `stripe\.slack\.com/archives/`) or raw thread text in chat, or says any
  of: "here's a handover", "new handover from Slack", "set up project from
  handover", "/handover".
- **Scan mode**: invoked by [scan-review](../scan-review/SKILL.md) Phase 0
  after `/handover-scanner` returns proposals. The skill receives the
  proposals directly; no parsing needed.

## Workflow

### Phase 1 — Get the proposal(s)

Depending on the entry point:

- **Paste of a permalink** — fetch the thread via Slack MCP, build the
  expected JSON shape, then pipe to `scripts/handover-parse.py --from-stdin`:

  ```
  {
    "channel_id": "<C...>",
    "thread_ts": "<ts>",
    "permalink": "<url>",
    "messages": [{ "user_name": "...", "text": "...", "ts": "..." }, ...]
  }
  ```

- **Paste of raw text** — pipe to `python3 scripts/handover-parse.py --text`
  via stdin (or use `--file <path>` if it's already on disk).

- **Scan mode** — proposals are already structured; skip parsing.

Capture the JSON proposal(s).

### Phase 2 — Preview each proposal

Before creating anything, surface a one-line preview per proposal:

> About to create `<slug>` from handover by `@<ae>` — `<merchant_name>`
> (`<products_hint>`), contact `<contact_name>` `<contact_email>`,
> AONR `<aonr or TBD>`. Manifest `<found|TBD>`, SFDC `<found|TBD>`.

If `missing` includes any of `merchant_name`, `slug`, or `thread_permalink`,
stop and tell the user — these are required. Ask them to either re-paste
with more context, or fill in the gaps manually before re-running.

For scan mode where you may have 1–N proposals, surface all previews in one
block. The full-auto policy means proceed without explicit confirmation,
but the previews must be visible so the user can interrupt if something
looks wrong.

### Phase 3 — Bootstrap

For each proposal, run:

```
python3 scripts/handover-create.py --proposal-stdin
```

piping the proposal JSON. (For batched scan output, use `--proposals-stdin`
with a JSON array.) The script:

1. Creates `projects/active/<slug>/` with PROJECT.md (HO+MAN+contact+AE
   pre-filled, External Links in canonical label order), empty
   action-items.md, raw/comms.md, timeline.md (seed entry), and a fresh
   scan-state.json.
2. Chains to `sync-to-asana.py --slug <slug>`.
3. Chains to `hubble-reconcile.py --backfill --slug <slug>` (best-effort;
   if Hubble has no matching row yet, SF/KAN stay TBD).
4. Appends `{channel_id, thread_ts, slug, processed_at}` to
   `data/handover-state.json::processed_threads`.

Handle the exit codes:

- `0` clean — surface the per-result JSON's `slug`, `merchant_name`,
  `aonr`, `ae`, `asana`, `hubble_backfill` fields.
- `1` slug collision — the project already exists. Surface a one-liner:
  "Skipped: `<slug>` already exists. If this is a different deal for the
  same merchant, see `data/runbooks/merge-slugs.md` for the related-projects
  cross-reference pattern."
- `2` chained-step failure — the folder was created but Asana or Hubble
  failed. Surface the stderr tail verbatim. The folder stays; the state
  file was NOT updated; a retry is safe.
- `3` filesystem error — rare; surface the error and ask the user to
  check disk permissions / paths.
- `4` proposal missing required fields — back to Phase 2 (re-paste).

### Phase 4 — Summary

End with one block:

```
## Handover bootstrap — <date>

- <slug> — <merchant_name>, AE @<ae>, AONR <aonr>
  - Asana: created
  - Hubble: <ok | skipped (reason)>
  - Folder: projects/active/<slug>/

(repeat per bootstrapped proposal, or "No new handovers bootstrapped.")
```

If any proposals were skipped or errored, list them separately under
`Skipped` / `Errors`.

---

## Backfill Mode

**This is the one-time, setup-side process — distinct from the daily
`/handover-scanner`.** Backfill is *roster-driven*: it sweeps the whole channel
and matches each **existing** project to its handover thread (coverage ~25/27).
The daily `/handover-scanner` is *channel-driven*: it reads only since the last
scan and surfaces **new** handovers (matched → bootstrap, unmatched → triage).
Same parser + matcher underneath; different window, direction, and goal.

Use backfill for projects that were already scaffolded from Hubble (via
`scaffold-from-hubble.py`) and still have `Handover: TBD` / missing contacts.

### When to invoke

- User says: "find handovers", "backfill handovers", "search for handover
  threads", or "find handover threads for all projects"
- After `scaffold-from-hubble.py --apply` completes (step 2 in its output)
- During initial workspace setup when projects exist but lack handover data

### Phase B1 — Prepare match manifest

Run `python3 scripts/handover-search.py` and parse the JSON output
(`{ channel_id, searches: [{ slug, project_name, ae_handle, ... }], skipped, errors }`).

If `searches` is empty, surface: "All projects already have handover links
or are in processed_threads. Nothing to search." — then stop.

Otherwise surface: "Looking for handover threads for N projects (M skipped)."

### Phase B1.5 — MCP connectivity gate

Before reading Slack, probe connectivity with ONE call:
`read_slack_channel_history` on `HANDOVER_CHANNEL_ID` with `limit: 1`.

If it fails (tool not found, connection error, timeout):
- Abort the backfill. Surface the MCP error and remediation steps
  (check Cursor MCP settings, re-authorize at go/toolshed-auth).
- Do NOT proceed to Phase B2 — every read would fail identically.

If it succeeds (even empty results) → proceed.

### Phase B2 — Read the full channel history (by ID, wide window)

Backfill is roster-driven: we already know all N projects (and their SFDC opp
ids), so sweep the whole channel once and match each project to a thread. The
window is **not** tied to any project's start date — read from a fixed floor that
covers every current project:

```
read_slack_channel_history(channel_id_or_name="{channel_id}", oldest="2025-02-01T00:00:00Z")
```

- `2025-02-01` matches the floor of the Hubble roster query (`ds_deployment_start
  >= 2025-02-01`), so no current project's handover can predate it.
- `channel_id` comes from the manifest. Read `HANDOVER_CHANNEL_ID_LEGACY` too if set.
- Paginate via `cursor` until the floor is reached. Hold every root message
  (with its `attachments`) for B3. This single wide read replaces the old N×2
  per-merchant `search_slack_messages` calls — and because opp-id matching is
  exact, a wide window carries no false-positive risk.

### Phase B3 — Match projects to threads (deterministic coverage)

Parse every message from B2 through `handover-parse.py --from-stdin` (one thread
JSON per message, including `attachments`), collect the proposals into a JSON
array, then run the **coverage** matcher:

```bash
echo '<proposals json array>' | python3 scripts/handover-match.py --proposals-stdin --coverage
```

It reports, against the full roster: `{ covered: [{ project_id, merchant_name,
slug, match_method, sfdc_opp_id, thread_permalink }], missing: [...], counts: {
roster, covered, missing, threads_in } }`. Matching tries three keys in order:
SFDC opp id (exact — the primary signal) → merchant name (fuzzy ≥ 0.6) →
contact-email domain (the thread's merchant-domain email vs the roster's
`primary_contact_email`, which recovers legacy "manifest review" handovers that
carry a Salesforce account id and no clean name). Across a full two-channel sweep
this lands ~24/27 in practice; `missing` are projects with no clean signal in the
channel (no opp, noisy/absent name, and no roster contact email) — surface them
for manual lookup, not as a failure.

### Phase B4 — Build proposals and apply (covered projects only)

For each entry in `covered`, read its full thread by ID
(`read_slack_message_thread(channel={channel_id}, thread_ts={thread_ts})`) to
capture `thread_body`, then build a proposal JSON:

```json
{
  "slug": "<slug>",
  "merchant_name": "<from PROJECT.md H1 title>",
  "thread_permalink": "https://stripe.slack.com/archives/{channel_id}/p{thread_ts_no_dot}",
  "channel_id": "<from manifest>",
  "thread_ts": "<from search result>",
  "thread_body": "<full thread content as plain text — all messages concatenated>",
  "primary_contact": { "name": "...", "email": "..." },
  "ae": "<ae_handle or ae_display_name>",
  "products_hint": "<from thread content if parseable, else null>",
  "manifest_url": "<from thread content if present, else null>"
}
```

The `thread_body` field is built from the `read_slack_message_thread` response
(already fetched in Phase B3). Concatenate all message texts with sender
attribution: `@user: message text\n`. This is stored verbatim in `raw/comms.md`
so comms-analyst has real content for summary generation. If thread fetch failed
or was truncated, set `thread_body` to null — the script falls back to
permalink-only entry with `Summary: _pending_`.

Extract `primary_contact`, `products_hint`, and `manifest_url` from the thread
content. Look for patterns like:
- Contact: `Name - user@company` or `Name (user@company)`
- Manifest: URL containing `account-manifest` or `admin.corp.stripe.com`
- Products: text after "products:" or in bracket notation `[Connect; Billing]`

If extraction is uncertain, leave fields as null — `handover-create.py` handles
missing fields gracefully.

Pipe all proposals as a JSON array to:

```bash
echo '<json_array>' | python3 scripts/handover-create.py --proposals-stdin --update-existing
```

Handle exit codes per the standard table (0=clean, 5=slug not found, etc.).

### Phase B5 — Update ae-handles.json

If a covered thread reveals an AE handle (the `ae` field on the proposal) for a
display name not yet in `data/ae-handles.json`, add `{"<ae_display_name>":
"<ae_handle>"}` and write it back. This grows the confirmed-handle lookup that
`handover-search.py` uses on future runs.

### Phase B6 — Summary

Report the coverage counts from B3 directly:

```
## Handover backfill — <date>

Covered: <covered>/<roster> projects matched to a handover thread
- <merchant_name> — <thread_permalink> (via <sfdc|name>)
- …

Missing (<missing>): no handover thread found in the channel
- <merchant_name>
- …

Skipped (<from B1>): already had a handover link
- <slug>
```

`missing` is expected to be small (a couple of projects whose handover never went
through the channel, or that predate it) — not a failure.

---

## Hard rules

- **Never write project files yourself.** Always go through
  [`scripts/handover-create.py`](../../../scripts/handover-create.py). It's
  the single chokepoint that keeps `data/handover-state.json` consistent
  with what's on disk.
- **Never bypass the slug-collision check.** If `--proposal-stdin` exits
  with 1, the project exists — do not delete it, do not rename it, do not
  merge into it. Surface the message and stop. Slug merges go through
  [`data/runbooks/merge-slugs.md`](../../../data/runbooks/merge-slugs.md)
  under human supervision.
- **Show the preview before bootstrap.** Even in full-auto, the one-line
  preview per proposal must precede the create. This is the user's last
  chance to interrupt if a parse went wrong.
- **Don't paraphrase the Asana/Hubble exit messages.** When step 5 or 6
  fails, surface the stderr tail verbatim — the user shouldn't have to
  read the script's output to know what broke.
- **Email-search queries follow the rules in CLAUDE.md.** The
  `handover-create.py` template uses the standard domain-or-name format
  the rest of the workspace expects; do not edit `PROJECT.md` post-creation
  to change it unless the user asks.
- **Scan-mode dedup is the scanner's job.** This skill does not re-check
  `data/handover-state.json::processed_threads` — by the time it sees a
  proposal, the scanner has already dedupped. (Paste mode bypasses dedup
  intentionally — the user is explicitly asking, so a re-paste of an
  already-processed thread should still hit the slug-collision check and
  exit cleanly.)
