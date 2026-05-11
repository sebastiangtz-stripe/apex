---
name: handover-bootstrap
description: >-
  End-to-end pipeline for turning a Slack handover into a live project:
  parses the thread, creates projects/active/<slug>/ with PROJECT.md
  (HO/MAN/contact pre-filled), creates the Asana task, backfills Hubble,
  and updates handover-state.json. Use when the user pastes a Slack
  handover permalink or thread text, says "here's a handover", "new
  handover from Slack", "set up project from handover", "/handover", or
  when scan-review Phase 0 surfaces unprocessed handovers from the
  handover-scanner subagent.
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
