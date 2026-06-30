# Stripe Accelerate — Project Management Workspace

> **Template note**: Strings like `[YOUR_NAME]`, `[YOUR_TIMEZONE]`, `[YOUR_BOARD_NAME]` are placeholders — replaced by `update-from-apex.py` using `data/update-config.json`. On a fresh workspace, run `/setup` before relying on any of these values.

Conversational PM system for [YOUR_NAME], a Stripe Accelerate consultant managing 25-35 merchant projects. [YOUR_NAME] talks naturally, Claude interprets intent and manages files.

**Architecture**: Asana is the project and task management layer (board: "[YOUR_BOARD_NAME]"). Local markdown stores raw communications, timelines, issues, drafts, and session logs. Cursor agents are the primary interface — no custom dashboard.

**Shared with Diego via apex**: This workspace is the upstream of a peer-shared agent template at `~/Documents/accelerate-apex-template/`, mirrored to GitHub at [`sebastiangtz-stripe/apex`](https://github.com/sebastiangtz-stripe/apex). [YOUR_NAME] and Diego both run their own live workspaces with their own merchant data, both publish improvements (skills, agents, scripts, runbooks, CLAUDE.md changes) to apex via `python3 scripts/sync-template.py --push`, and both pull updates from apex into their own workspaces. Full protocol + roles + conflict rules in [`data/runbooks/template-sync.md`](data/runbooks/template-sync.md). **Never** push merchant data, sessions, or `.env` to apex — the sync script enforces this with a leak scan that fails hard on any merchant token.

---

## Subagent Inventory

Specialized Cursor subagents live in `.cursor/agents/`. Each has its own context window, so noisy intermediate output (full email bodies, internal search results, Hubble JSON) never bloats the main thread. Invoke with `/name` or by mentioning naturally.

| Subagent | Use for | Model |
|----------|---------|-------|
| `merchant-scanner` | Lightweight fetch relay — calls Gmail/Slack MCP, dumps raw results to `data/staging/`. No dedup, no writes to project files. | claude-4.6-sonnet |
| `comms-analyst` | Read-only review of one merchant's full `raw/comms.md` to propose auto-closures, new action items, Asana comments, and timeline summaries. | claude-4.6-sonnet |
| `hubble-analyst` | Refresh `data/hubble-snapshot.json` if stale, run `scripts/hubble-reconcile.py`, return structured diff. Used by Auto-Startup Agent E. | fast |
| `handover-scanner` | Read Slack handover channel(s) **by channel ID**, parse new threads via `scripts/handover-parse.py`, classify against the roster via `scripts/handover-match.py`, and return matched `proposals` + an unmatched `triage` list. Used by scan-review Phase 0. | claude-4.6-sonnet |
| `quick-context` | Per-merchant health snapshot: status, products, AONR, engagement (days silent), action items, recent activity. Returns structured JSON. | fast |
| `stripe-jarvis` | Any Stripe technical question (Tier 1/2/3 owned by Jarvis itself). Self-contained: searches internal docs, Trailhead, Sourcegraph, Jira, Slack, public docs. | claude-opus-4-7 |

**Skills vs. subagents**: Skills (`.cursor/skills/*`) orchestrate sequential PM workflows. Subagents (`.cursor/agents/*`) do heavy reading/research in isolated contexts. Skills delegate to subagents, never the other way around.

### Skill Inventory

| Skill | Use for | Frequency |
|-------|---------|-----------|
| `scan-review` | Full email/Slack scan pipeline (fetch → ingest → review → apply) | Daily |
| `handover-bootstrap` | Bootstrap new project from Slack handover thread or paste | On new merchant |
| `email-agent` | Draft and send merchant-facing emails with context | Daily |
| `meeting-prep` | Pre-meeting briefing doc + verbal summary | Before calls |
| `action-items` | Portfolio-level action item rollup with filters (tag, due, overdue) | Daily |
| `catchup` | Sequences asana-reconcile → hubble → scan-review → index-reconciler | Mid-day re-sync |
| `log-comms` | Manually log pasted email/Slack/meeting to raw/comms.md + timeline.md | Daily |
| `health` | One-screen merchant dashboard (silence, items, AONR, drift, commitments) | Ad-hoc |
| `drift-audit` | Workspace consistency audit (INDEX rot, slug collisions, hygiene) | Weekly |
| `specialist-prompt` | Generate structured investigation prompt from canonical template | Complex research |
| `contact-gap-audit` | Scan comms for addresses missing from PROJECT.md email query | Monthly |
| `index-reconciler` | Rebuild INDEX.md from filesystem + PROJECT.md + Hubble | After project changes |
| `weekly-metrics` | Aggregate session Stats into trend rollup | Weekly review |
| `lessons-extract` | Capture institutional knowledge at project archive time | On archive |
| `recall` | Search lessons-learned by tag/topic/product for prior patterns | Before new research |
| `setup` | Guided first-time workspace onboarding (identity, Asana, Hubble) | One-time |
| `test-subagents` | Validate all agent/skill/rule contracts (runs inside template sync) | Pre-push |
| `compass-update` | Generate Apex Project Compass update draft from git log + sessions | Biweekly |

---

## Auto-Startup Summary

**Step 0 — Fresh-workspace check (before anything else).** If the user's message is explicitly `/setup` or mentions "onboarding" or "first-time setup", skip this check and proceed directly to the setup skill. Otherwise: read `.env`. If the file is missing, OR if any value equals `REPLACE` / starts with `REPLACE_WITH`, OR if `ASANA_PAT` is empty: skip the entire auto-startup below and surface a single line — *"Workspace not configured. Run `/setup` to onboard."* Then stop and wait for the user. This prevents a cascade of agent failures on a fresh clone before the user even types anything. Do NOT run any of the steps below until `.env` is fully populated.

**Step 0.5 — MCP connectivity gate (full startup only).** After the `.env` gate passes and before running steps 1-11, probe MCP connectivity once. This is the single most common daily blocker: the `sc-2fa` token expires daily, and — separately — the MCP servers silently de-auth whenever a Cursor window has been left open for more than a day. Neither can be fixed from here, so catch it *before* fanning out child agents against dead MCPs. Make one lightweight call each: Gmail (`search_gmail`, query `"in:inbox"`, `max_results: 1`) and Slack (`read_slack_channel_history` on `HANDOVER_CHANNEL_ID`, `limit: 1`).

- **Pass** (success, including empty results) → proceed to steps 1-11.
- **Fail** (401/403, "tool not found", connection error, timeout) → **HALT the full protocol.** Do NOT run the scan, Hubble, calendar, Asana reconcile, or any child-agent fan-out. A partial startup that silently skips the scan paints a misleading "all quiet" picture — it is better to fix auth and re-run. Before halting, still run the two local-only safety signals (no MCP needed): the **pending dual-writes check** (step 7) and any `commitments.md` `Status: overdue` lines. Then surface this and wait:

  ```
  ⚠ MCP not connected — startup halted before running the daily protocol.

    Gmail: [connected | FAILED: <error>]
    Slack: [connected | FAILED: <error>]

  Safety check (no MCP needed):
    • Pending dual-writes from prior session: <N>  (run `python3 scripts/apply-proposals.py --resume` if >0)
    • Overdue commitments: <N>

  Fix MCP auth, then say "start the day" to run the full protocol:
    1. Run `sc-2fa` in a terminal (required daily).
    2. If this Cursor window has been open >1 day, the MCP servers silently
       de-auth — go to Settings > MCP and toggle the Gmail/Slack servers off,
       then back on.
    3. Re-run "start the day".
  ```

Skip this gate on weekends (Gmail/Slack/calendar are skipped anyway — note "Weekend mode") and in **"Quick status"** mode (local-only by design — if MCP is down it still returns useful local state, so never halt it).

If `.env` looks healthy and the MCP gate passes, run these in **parallel** where possible:

1. Run `TZ="[YOUR_TIMEZONE]" date '+%A %Y-%m-%d %H:%M:%S %Z'` for current date/time (includes day of week). **Always use your local timezone.** Never infer the day of week — read it from the command output.
2. **Asana reconcile**: If any `projects/active/*/asana.json` files exist, run `python3 scripts/asana-reconcile.py` to sync any changes made in Asana since last session (items completed on mobile, etc.). Skip on fresh workspace where no Asana tasks have been created yet.
3. **Silence scan**: Run `python3 scripts/last-activity.py --threshold-days 7 --json` to surface projects silent ≥7 days. Canonical helper parses both `## YYYY-MM-DD` and `## [YYYY-MM-DD]` H2 headers using `max()` (robust against out-of-order entries). Pass `--include-scan-state` if you want scanner activity to count as engagement. Never reimplement this with inline `re.findall + dates[-1]` — that silently inverts the silence direction since timelines are newest-at-top.
4. **Calendar**: Fetch today's calendar (skip on weekends — note "Weekend mode"). If any event within the next 2 hours matches a merchant name in `projects/active/`, note it in the summary: "Meeting with [merchant] in Xh — run `/meeting-prep <slug>` before the call."
5. **Session continuity**: Read most recent `sessions/*.md` for continuity.
6. **Hubble sync**: Invoke `/hubble-analyst`. It refreshes the snapshot if stale and returns a structured diff (new projects, archive candidates, drift). The verbose JSON stays inside the subagent.
7. **Pending dual-writes check** (cheap filesystem scan, ~1s): list `data/scan-proposals/*.json` (one level deep, NOT including `applied/`). If any non-archived files exist, the prior session ended with proposals that did not finish applying. Read each file's `apply_status`; count items still in non-terminal states (anything other than `applied`, `skipped_dedup`, `skipped_low_confidence`, `skipped_human_review`). Surface in the startup summary as a top-priority line. Run `python3 scripts/apply-proposals.py --resume` to apply them — the script is idempotent (re-running on already-applied items is a no-op) and respects a `--max-age-days 7` guard for stale proposals. NEVER skip this check; it is the recovery path for the 2026-05-12-class failure mode where 44 dual-writes were silently lost.
8. **Communication scan**: The MCP gate already ran at **Step 0.5** for full startup, so proceed directly — invoke the `scan-review` skill (full pipeline: handover sweep → fetch → ingest → review → apply). (When `scan-review` is invoked standalone — not via startup — it runs its own gate per `.cursor/rules/mcp-validation.mdc`.) This is the core daily value — fetches new emails and Slack for all active merchants. Respects the 4-hour TTL (merchants scanned recently are skipped automatically in Phase 1a). On weekends, skip unless explicitly requested. If a transient MCP error or timeout hits mid-scan (after the gate passed), log the error in the summary but don't block the rest of startup.
9. After steps 2-8 complete, read `action-items.md` files for overdue/upcoming items (3 days), and read `commitments.md` files (where present) for any `Status: overdue` lines. In steady state, Asana is the authority for open items; local files are the backup. (During initial setup, both are populated together for the first time via `apply-proposals.py`.)
10. Present the summary in **three tiers**: a digest table first, then only the details that need attention, then ops telemetry only when it requires action. The goal is a clean at-a-glance read for a non-technical user — surface what they need to act on, not the machinery. Full format spec in **[Response Formatting](#response-formatting--the-scan-digest)** below.

    **Tier 1 — Scan Digest (always, at the very top).** Render the canonical digest table (see Response Formatting). Core rows always present (even at 0); conditional rows appear only when non-zero/relevant.

    **Tier 2 — Needs attention (only populate non-empty items).** In priority order:
    - **Pending dual-writes** (if any): "N proposals across M merchants from prior session not yet applied. Run `python3 scripts/apply-proposals.py --resume` to apply." Always first if non-empty — work the prior session believed was committed but wasn't.
    - **Broken commitments**: ANY `commitments.md` line with `Status: overdue`. Higher priority than overdue action items — explicit promises to the merchant. Show as `[<slug>] promised <date>: "<promise>" (overdue Nd)`.
    - **Meetings within 2h**: flag with `/meeting-prep <slug>` suggestion.
    - **CRITICAL — Silent (14+ days)**: list with AONR. Suggest: ping contact, check with SFDC Opportunity Owner, consider escalation.
    - **New action items created** (from the scan): brief list.
    - **Due soon** (≤3 days) and **Overdue** action items.
    - **Waiting on merchant**: threads where the last message is ours, N days silent.
    - **Silent (7-13 days)**: list, sorted by AONR.
    - **On Hold**: paused projects with reason.
    - **Priority suggestions**: per rebalancing rules below.
    - **Hubble**: only if `/hubble-analyst` returned non-empty `new_projects`, `archive_candidates`, or material `drift`.
    - **Asana sync**: changes detected by reconcile, only if any.

    **Tier 3 — Ops (suppressed by default; surface a single line ONLY when action is needed).**
    - **Asana write health**: collapses into the digest table's *Asana writes* cell — `✅ healthy` when clean; `⚠ <reason>` (e.g. "1 pending review", "drift") when not. Run `python3 scripts/dual-write-health.py --oneliner` to evaluate. Never print the full breakdown in the default view.
    - **Template drift (apex)**: run `python3 scripts/sync-template.py --check`. If DRIFT, surface ONE line only: *"Template drift: N paths differ from apex — sync when you wrap up."* Otherwise silent.
    - **Apex updates (Mondays only)**: if Monday AND `data/update-check-state.json` missing or `last_check_date` ≠ today, run `python3 scripts/update-from-apex.py --check`. Surface only if `updates_available`: *"Apex updates: N files, M migration(s) pending. Say 'pull updates' to review."* Flag `env_migrations` / `pending_migrations` as action-required. Otherwise silent.
    - **Drift audit (weekly)**: if Monday OR `data/runbooks/drift-audit-last-run.txt` mtime >7d, run `python3 scripts/drift-audit.py`. Surface ONLY CRITICAL findings (Section A archived-but-listed, Section C hubble_pid_collisions, Section E future_timestamp). Otherwise silent.
    - **Tag distribution**: not in the default view — show only if the user asks "show details" / "full breakdown".

    **Footer**: **Last session** (date, 1-sentence summary, pending count) + **Quick actions** (1-2 concrete next steps).
11. At scale (35+): Cap each Tier-2 list at top 10 items, summarize the rest ("+N more"). The digest table stays full.

---

## Communication Style

Default to compact, outcome-first responses — full spec in [`.cursor/rules/response-style.mdc`](.cursor/rules/response-style.mdc). Three principles, always on:

1. **Length scales to the action** — trivial actions (log a comm, mark done, add a contact) get one line; reserve structured writeups for scans, rollups, and investigations.
2. **Outcome first, mechanics hidden** — lead with what happened; never narrate scripts, file paths, GIDs, phase numbers, or pipeline internals. Telemetry is opt-in (`show details`).
3. **Colleague tone** — no preamble/postamble, plain merchant-facing language, format matched to the data shape (inline fact / bullets / table).

**Guardrail — brevity yields to safety**: destructive or outward-facing actions (send, archive, delete, calendar writes), needed confirmations, and genuine risks always get the words they need.

Full conventions (plain-language errors, humanized dates/money, quiet states, the fixed status-symbol set, long-op expectation lines, disambiguation pick-lists, confirmation previews) live in [`.cursor/rules/response-style.mdc`](.cursor/rules/response-style.mdc).

### Help / capability discovery

When the user says **"help"**, **"what can you do"**, **"what can I say"**, or seems lost, return this plain-language cheat sheet (not the internal mappings table) — group by task, show the natural phrasing, keep it to one screen:

```
Here's what I can do — just talk naturally:

Mornings    "start the day"          full scan + your priorities
            "quick status"           fast check, no email/Slack scan
Email       "draft a follow-up for Acme about pricing"
            "check email for Acme"   ·  "log this email" (paste it)
Merchants   "what's happening with Acme?"  ·  "prep me for Acme"
            "new handover" (paste the Slack thread)
Work        "what are my priorities?"  ·  "show me all #reply items"
            "Acme is asking about webhooks"   (I'll research it)
Calendar    "what's on my calendar?"  ·  "am I free Thursday 2pm?"
Wrap-up     "wrap up"                 save a session summary

Say "show details" any time you want the full breakdown.
```

Adapt the merchant names to real active ones when possible. Don't dump the conversational-mappings table or internal skill names.

## Response Formatting — the Scan Digest

Both the daily startup summary (Auto-Startup step 10) and the standalone `scan-review` output (Phase 3) **lead with the same digest table**. It is a scannable, at-a-glance read for a user who just wants the outcome — not the machinery. Everything below the table is detail the user can act on; ops/dev telemetry is suppressed unless it needs action (see Auto-Startup Tier 3).

**Audience principle**: write for a consultant who does not know the agent's internals. No script names, GIDs, phase numbers, or pipeline jargon in the digest or Tier-2 details. Counts and merchant-facing nouns only. Internal mechanics belong in logs and the wrap-up, not the daily read.

### Digest table format

```
## Scan Summary — YYYY-MM-DD (Day)

|                              |              |
|------------------------------|--------------|
| Asana writes                 | ✅ healthy   |
| New handovers                | 2            |
| New emails ingested          | 14           |
| Emails awaiting your reply   | 5 (2 new)    |
| Waiting on merchant          | 3            |
| Meetings today               | 1 — Acme 2pm |
| Silent 14+ days              | 2 ⚠         |
| Action items due ≤3d         | 4            |
```

**Core rows — always present, even at 0** (a `0` is a meaningful "nothing new"):
- **Asana writes** — `✅ healthy`, or `⚠ <reason>` when `dual-write-health.py` reports pending review / drift / zero clean runs. This is the *only* place write-health surfaces by default.
- **New handovers** — count bootstrapped this run (Phase 0).
- **New emails ingested** — total emails + Slack threads ingested this run.
- **Emails awaiting your reply** — `<total> (<new> new)`. *Total* = currently-open `#reply` action items across active merchants. *New* = `#reply` items raised from mail ingested in this scan. This is inbound merchant mail **we** owe a response to (the inverse of *Waiting on merchant*).
- **Waiting on merchant** — threads where the last message is ours and the merchant hasn't replied (from the analyst's `waiting_on_merchant`).

**Conditional rows — show only when non-zero / relevant:**
- **Pending dual-writes** — `N ⚠` when prior-session proposals are unapplied. Show as the first row when present (recovery signal); omit when 0.
- **Meetings today** — `N — <merchant> <time>`; omit on weekends and when none.
- **Silent 14+ days** — `N ⚠`; omit when none.
- **Action items due ≤3d** — `N`; omit when none.

Keep the table to these rows. New signals get added here only after a deliberate decision — the value of the digest is that it stays short.

---

## Conversational Mappings

Interpret intent, not rigid commands. Key mappings:

### Project Management

| User says | Action |
|---|---|
| "Good morning" / "start the day" / "daily protocol" / "let's go" | Full auto-startup (steps 0-11 including communication scan). This is the default — every new conversation runs the full pipeline. |
| "Quick status" / "just status" | Run auto-startup WITHOUT step 8 (communication scan). Fast diagnostic only (~10s), no MCP calls to Gmail/Slack. Use when you just want to check state mid-day without waiting for scans. |
| "help" / "what can you do" / "what can I say" | Return the plain-language capability cheat sheet (see [Communication Style → Help](#help--capability-discovery)). Don't dump the mappings table or skill names. |
| "New project: [Merchant], [acct_id]" | Create `projects/active/<merchant-kebab>/` with template files, populate `## External Links` with the four canonical labels (`Handover:`, `Manifest:`, `Salesforce:`, `Kantata Workspace:`) — extract `Handover` (Slack permalink) + `Manifest` (admin URL) from the handover thread when handing-over from Slack; `Salesforce` + `Kantata Workspace` come from Hubble (via `hubble-reconcile.py --backfill`). Create Asana task in Integration section, save GID to `asana.json`, update INDEX.md. |
| User pastes a Slack handover permalink / thread text OR says "here's a handover", "new handover from Slack", "set up project from handover", "/handover" | Invoke the `handover-bootstrap` skill (paste mode). It parses the thread via `scripts/handover-parse.py`, surfaces a one-line preview, then runs `scripts/handover-create.py` which creates the folder, PROJECT.md (HO+MAN+contact+AE pre-filled), Asana task, Hubble backfill, and appends to `data/handover-state.json`. End-to-end automated. |
| "Here's the transcript from my call with [merchant]" | Save to `raw/comms.md`, summary to `timeline.md`, extract action items as Asana subtasks |
| "What's happening with [merchant]?" | Read PROJECT.md + timeline.md + Asana task (status, subtasks), concise status |
| "What are my priorities?" | Read Asana board for open subtasks + local timelines for silence detection |
| "Show me all #[tag] items" / "Batch my emails" | Read Asana subtasks, filter by tag prefix in name, grouped by parent task |
| "Draft an email for [merchant] about [topic]" | Read context, research if needed, draft to `drafts/<topic>.md` |
| "Archive [merchant]" / "[merchant] is live" | Complete Asana task, move to `archive/`, update INDEX.md |
| "Find handovers" / "backfill handovers" / "search for handover threads" | Invoke `handover-bootstrap` skill (backfill mode). Runs `handover-search.py` → parallel Slack searches → `handover-create.py`. Use `--update-existing` when `projects/active/<slug>/` already exists (merges contacts, upserts links, patches TBD fields only); omit the flag for fresh creates. |

### Issue Investigation

| User says | Action |
|---|---|
| "[Merchant] is asking about [issue]" | Apply research tier, log to `issues/<issue>.md` |
| "Check if there are incidents affecting [merchant]" | Search BRB |
| "Look up [topic] in Trailhead" | Search Trailhead, summarize |

### Email

| User says | Action |
|---|---|
| "Check email for [merchant]" | Incremental Query Protocol: check `raw/comms.md` for last date, fetch only newer |
| "Log that email about [merchant] re: [topic]" | Append to `raw/comms.md`, create timeline entry. Action items via Review only. |
| "What did [contact] say about [topic]?" | Search Gmail for emails from contact about topic, summarize |
| "Find the email where [merchant] confirmed [thing]" | Targeted Gmail search, return specific message |
| "Pull action items from that email thread" | Retrieve thread, extract items as Asana subtasks + local `action-items.md` (dual-write) |

### Slack

| User says | Action |
|---|---|
| "Check Slack for [merchant]" | Incremental Query Protocol, read full threads |
| "What's the latest in [channel]?" | Read recent channel history, summarize |
| "Read the thread about [topic] in [channel]" | Search for thread, read full thread, summarize |
| "What has [colleague] said about [merchant]?" | Search Slack for messages from that person |
| "Log that Slack conversation about [merchant]" | Read full thread, append to `raw/comms.md`, timeline entry |

### Calendar

| User says | Action |
|---|---|
| "What's on my calendar today/this week?" | Fetch and present events |
| "Schedule [type] with [merchant] for [date]" | Create event (requires human confirmation URL) |
| "When's my next call with [merchant]?" | Search upcoming events for merchant name |
| "Reschedule [merchant] to [new time]" | Find event, update (requires confirmation) |
| "Cancel the [merchant] meeting" | Find and delete (requires confirmation) |
| "Am I free [date/time]?" | Check calendar for conflicts |

### Session / Ideas

| User says | Action |
|---|---|
| "Wrap up" | Generate session summary to `sessions/YYYY-MM-DD.md`, update INDEX |
| "I have an idea about [topic]" | Create/update `ideas/<topic>.md` |

---

## Cross-Tool Workflows

### Hubble Sync Protocol
Runs at the start of every session as part of auto-startup (Agent A above), and on demand when the user says "sync Hubble" / "reconcile".
1. Check mtime of `data/hubble-snapshot.json`. If missing or older than `HUBBLE_SNAPSHOT_TTL_HOURS` (default 24h), read the query template from `templates/hubble-query.sql`, substitute `{{LEAD_FILTER}}` with the value of `HUBBLE_LEAD_FILTER` from `.env`, and execute via `run_hubble_query`. **Never modify the SQL beyond the `{{LEAD_FILTER}}` substitution** — the template is tested and tuned. The filter runs at the SQL level so only the consultant's projects are returned. Overwrite the snapshot with `{ fetched_at, lead_filter, template, row_count, projects }`.
2. Run `python3 scripts/hubble-reconcile.py` to diff. In the startup summary, surface only if non-empty: `NEW PROJECTS` (Hubble rows without a local folder), `ARCHIVE CANDIDATES` (local folders missing from Hubble `In Progress`), and material `DRIFT` (AONR / AE / dates).
3. On explicit user confirmation of drift, run `python3 scripts/hubble-reconcile.py --backfill` to apply PROJECT.md + `hubble.json` updates.
4. Never auto-archive or auto-create projects from a diff; these remain human-confirmed operations.

### Email / Slack Scan + Review

Triggered when the user says "scan email", "scan Slack", "check all projects", "review open items", or "what's new". Owned by the `scan-review` skill (`.cursor/skills/scan-review/SKILL.md`):

- **Phase 1a** — fans out `/merchant-scanner` fetch-relay subagents per active merchant. Each dumps raw MCP results to `data/staging/<slug>-<date>.json`. No writes to project files.
- **Phase 1b** — runs `python3 scripts/ingest-comms.py` which deterministically processes staging files: dedup, identity gate, writes to `raw/comms.md` + `timeline.md`, updates `scan-state.json`, contact discovery.
- **Phase 2** — fans out `/comms-analyst` per merchant with new content (read-only proposals: auto-closures, new items, Asana comments, timeline summaries, commitments).
- **Phase 2.5** — the **parent orchestrator** (main thread or skill runner) writes each analyst's JSON response to `data/scan-proposals/<slug>-<YYYY-MM-DD>.json`, then runs `python3 scripts/apply-proposals.py --resume` for all Asana + local writes. The comms-analyst never writes files itself — it returns JSON; the caller persists it.

The LLM never writes to project files during scanning — all writes go through deterministic Python scripts (`ingest-comms.py` for logging, `apply-proposals.py` for action items). This eliminates the class of bug where an LLM context crash drops in-flight writes.

### Meeting Prep ("Prep me for [merchant]")
Owned by the `meeting-prep` skill. Parallel reads: PROJECT.md + Asana subtasks + last 3 comms + fresh Gmail/Slack (only if last scan >4h). Output: prep doc to `drafts/prep-YYYY-MM-DD.md` + 30-second verbal summary.

### Weekly Review ("Weekly review" or "How did my week go?")
Fetch this week's calendar, scan all projects for timeline entries and action items from this week. Present: meetings held, progress, completed items, still open, stale projects.

### Weekly Planning ("Plan my week" or "What's coming up?")
Fetch upcoming calendar, pull project status for each merchant meeting. Flag projects with overdue items + upcoming meetings. Present: day-by-day schedule, prep needed, deadlines, suggested focus.

### Hubble Ingest / Reconciliation

Hubble is the single source of truth for roster, AONR, dates, AE, SFDC/Kantata links, account segment, and Accelerate type. The legacy Kantata CSV / Google Sheet workflow is retired.

Owned by `/hubble-analyst`. Invoke at startup (Auto-Startup Agent E) and on demand ("sync Hubble", "reconcile"). The subagent refreshes `data/hubble-snapshot.json` if older than `HUBBLE_SNAPSHOT_TTL_HOURS`, runs `python3 scripts/hubble-reconcile.py`, and returns a structured diff (`new_projects`, `archive_candidates`, `drift`). Surface only non-empty sections in the startup summary.

For drift backfill: invoke `/hubble-analyst` with `backfill: true` (or run `python3 scripts/hubble-reconcile.py --backfill --slug <slug>` directly for one project). Backfill is human-confirmed; never automatic.

**Not in Hubble** (stay manually populated in PROJECT.md): Stripe Account ID (`acct_xxx`), Account Manifest URL, Slack channels, Stripe internal contacts, Product Activation checklist, Key Contacts.

**Health report fields deliberately unused**: `overall_project_health`, `last_health_report_text`, and `days_since_last_health_report` are read but not surfaced. Current HR quality is unreliable; a separate workstream will improve it before wiring in.

For column mapping, saved query ID, and matching logic, see `scripts/hubble-reconcile.py` and `.cursor/agents/hubble-analyst.md`.

---

## Asana Integration

**Board**: "[YOUR_BOARD_NAME]" (project GID in `.env`)
**Sections**: Discovered by `setup-discover-asana.py` and stored in `.env` as `ASANA_SECTION_*` vars. Typical names: Discovery, Integration, Testing, Go-Live, Live, On Hold. Always read section GIDs from `.env` — never hardcode names. Status→section mapping is handled by `sync-to-asana.py`.
**Tasks**: One per merchant with custom fields.
**Subtasks**: Action items. Name format: plain action-verb description (e.g. `Send revised contract to ABC Co`). Tag is set as the Asana custom field on the subtask, not in the name. Due dates set on subtask.

### Dual-Write Protocol

Every change must update **both** Asana and local files. Asana is the management view; local is the agent context store. During scans, these writes happen through `ingest-comms.py` (logging) and `apply-proposals.py` (action items + comments) — not inline by the LLM.

| Event | Local update | Asana update |
|-------|-------------|--------------|
| New action item | Append to `action-items.md` | Create subtask on merchant's task (read `asana.json` for GID) |
| Complete action item | Mark `[x]` in `action-items.md` | `PUT /tasks/{subtask_gid}` with `completed: true` |
| New communication logged | Append to `raw/comms.md` + `timeline.md` | Add Asana comment **only for significant comms** (merchant replies, escalations, decisions). Skip automated notifications, bot messages, and routine Slack pings. |
| Status change | Update PROJECT.md Status field | Move task to appropriate section |
| New project | Create `projects/active/<slug>/` folder | Run `python3 scripts/sync-to-asana.py --slug <slug>` |
| Archive project | Move to `projects/archive/` | Complete the Asana task + set "Active on Accelerate?" to NO |

### Reconciliation
Run `python3 scripts/asana-reconcile.py` to sync both directions:
- Asana completions → mark local items `[x]`
- Local new items → create Asana subtasks
- Local completions → complete Asana subtasks
Use `--dry-run` to preview changes.

### Mapping files
Each project has `projects/active/<slug>/asana.json`:
```json
{ "task_gid": "123456789", "project_gid": "...", "section": "...", "subtask_gids": { "action-item-key": "987654321" } }
```

Each project also has `projects/active/<slug>/scan-state.json` for scan dedup:
```json
{ "last_email_scan": "2026-04-14T17:10:00Z", "last_slack_scan": null, "logged_email_ids": ["19d8ca9b..."], "logged_slack_thread_ids": [] }
```

### Asana API Reference
Full endpoint reference + JSON parsing gotcha: [`data/runbooks/asana-api.md`](data/runbooks/asana-api.md).

---

## Template Sync Protocol (apex)

This live workspace is the upstream of a peer-shareable agent template at
`~/Documents/accelerate-apex-template/`, mirrored to GitHub at
[`sebastiangtz-stripe/apex`](https://github.com/sebastiangtz-stripe/apex).
[YOUR_NAME] and Diego both maintain their own live workspaces (each with their
own merchant data) and both publish improvements through apex.

**Why**: Improvements happen in real workspaces, where the agent is being
exercised against real merchant problems. Without a sync layer, those
improvements stay siloed. Apex is the shared substrate; live workspaces
contribute up to it.

**Hard rule — never ship merchant data**: The sync script
[`scripts/sync-template.py`](scripts/sync-template.py) enforces a leak scan
that fails the sync if any merchant slug, identity token, or 13+ digit Asana
GID appears in template content. Bypass is not supported.

### Conversational mappings

| User says | Action |
|---|---|
| "sync template" / "push to apex" / "publish improvements" | Run `python3 scripts/sync-template.py --push --message "<one-line summary>"`. Always require an explicit message. |
| "what changed since last sync" / "template drift" | Run `python3 scripts/sync-template.py --check`. List the drifted paths. |
| "preview template sync" / "dry run" | Run `python3 scripts/sync-template.py --dry-run`. Show the proposed diff + genericization. |
| "weekly sync report" | Run `python3 scripts/sync-template.py --report`. Surface recent apex commits + staleness. |
| "pull from apex" / "pull updates" / "apply updates" / "update from apex" | Run `python3 scripts/update-from-apex.py --check`. If updates available, run `--diff`. Present each file's diff conversationally with accept/reject per file. On accept: run `--apply-file <path>`. After all files reviewed: run `--finalize`. |
| "check for updates" / "any updates from apex?" | Run `python3 scripts/update-from-apex.py --check`. Surface commit list + file count if available. |
| "update status" | Run `python3 scripts/update-from-apex.py --status`. Show last check time + pending count. |

### When to suggest a sync proactively

After **any** of the following lands in the live workspace, surface a
one-line *"Template-relevant change detected — sync to apex when done?"* note
at the next conversation turn:

- New or edited file under `.cursor/agents/`, `.cursor/skills/`, `.cursor/rules/`, `.cursor/hooks/`
- New or edited `scripts/*.py` that isn't under `__pycache__/`
- New or edited `data/runbooks/*.md`
- New or edited `templates/emails/*`
- Edits to `CLAUDE.md`, `.cursor/hooks.json`, `.cursor/settings.json`
- New `data/lessons-learned/pattern-*.md` (cross-cutting patterns ship; merchant-specific lessons stay local)

**Do not** sync after every single edit — wait for a natural break (end of a
focused work session, before "wrap up", or when the user says "we're done").
Batching reduces noise in apex history.

### Conflict handling

The script auto-rebases against `origin/main` before pushing, so peer
commits from Diego land cleanly. If the rebase fails (true conflict on a
shared file), the script aborts with the conflicted paths printed — resolve
manually in `~/Documents/accelerate-apex-template/` and re-run.

Once both authors are committing >1×/week, switch from direct-to-main
pushes to feature-branch + PR review. The runbook covers the migration.

### Full reference

See [`data/runbooks/template-sync.md`](data/runbooks/template-sync.md) for:

- Architecture diagram
- Roles + responsibilities
- Inclusion / exclusion list (what's template-relevant)
- Genericization rules (live → template substitutions)
- Leak-scan denylist
- Onboarding Diego's workspace
- Failure modes + audit trail (`data/runbooks/template-sync-log.md`)

### Migration Execution Protocol

When `python3 scripts/apply-migration.py --check` reports pending migrations:

1. **List** each migration ID + description to the user.
2. **Ask**: "Apply N migration(s)? (yes / no / show details)"
3. On yes: run `python3 scripts/apply-migration.py --apply-all`
4. **Report** the script's JSON output verbatim — do NOT paraphrase error details.
5. If any step failed: read the error, suggest the specific fix, wait for user.
6. **NEVER** manually edit files that a migration targets — always re-run the script after fixing the blocker.

You are the narrator and invoker. The script is the executor. Do not bypass it.

---

## Agent Delegation (Cursor)

Cursor subagents (`.cursor/agents/*`) handle context-heavy work in isolated windows. Skills (`.cursor/skills/*`) orchestrate sequential workflows. See **Subagent Inventory** above for the full list.

| Scenario | Approach |
|---|---|
| Quick lookup | Direct tool call, no subagent |
| Stripe technical question | `/stripe-jarvis` (always — never answer in main thread) |
| Email/Slack scan (one merchant or all) | `scan-review` skill → fans out `/merchant-scanner` (fetch relay) → `python3 scripts/ingest-comms.py` |
| Review phase after scan | `scan-review` skill → fans out `/comms-analyst` per merchant → `python3 scripts/apply-proposals.py --resume` |
| Hubble snapshot refresh + diff | `/hubble-analyst` (Auto-Startup Agent E or on demand) |
| Auto-Startup | Parallel: A (Asana reconcile), B (silence scan), C (calendar), D (session), E (Hubble), F (dual-write check). Then sequential: G (scan-review — full Gmail/Slack pipeline) |
| Meeting Prep | `meeting-prep` skill (parallel reads of PROJECT.md + Asana + comms + fresh Gmail/Slack) |
| Email drafting/sending | `email-agent` skill (sequential, returns to main thread for approval) |

Guidelines:

- Parallel when independent, sequential when dependent — for fan-outs (scanner, analyst), issue all calls in a single message.
- Subagents return small structured summaries; main thread executes any writes that require Asana/local dual-write.
- Cap concurrent fan-outs at ~10 foreground (blocking) subagents per message. For background (non-blocking) fan-outs like merchant-scanner or comms-analyst, cap at ~15 per message since the parent isn't waiting.

---

## File Templates & Action Item Format

Stable reference moved out of CLAUDE.md to keep the conversational layer tight. Consult on demand:

- [`data/runbooks/file-templates.md`](data/runbooks/file-templates.md) — PROJECT.md template, all secondary file formats (timeline, action-items, issues, drafts, raw/comms, commitments, specialist-runs, asana.json, scan-state.json, hubble.json), Related Projects optional section.
- [`data/runbooks/action-items-format.md`](data/runbooks/action-items-format.md) — line format, full tag vocabulary, complexity scoring, section structure inside `action-items.md`.
- [`data/runbooks/asana-api.md`](data/runbooks/asana-api.md) — Asana endpoint reference, subtask naming convention, JSON parsing gotcha, per-project asana.json mapping.
- [`data/runbooks/merge-slugs.md`](data/runbooks/merge-slugs.md) — runbook for merging duplicate slugs and the Related Projects cross-reference pattern for non-duplicates.

Quick refs that stay inline because they're load-bearing for every conversation:

- **Action item tags (1-3 per item)**: `#email`, `#reply`, `#research`, `#prep`, `#schedule`, `#track`, `#log`, `#waiting` (modifier — never alone).
- **Complexity defaults**: L = `#log/#track/#schedule/#waiting`, M = `#email/#reply/#prep`, H = `#research`. Override based on context.

---

## Investigation & Research

Stripe technical questions are owned by `/stripe-jarvis` (`.cursor/agents/stripe-jarvis.md`). Jarvis runs the 3-gate framework, the internal skeptic pass, and the mandatory Sources section in its own context window. Always delegate — never answer Stripe questions in the main thread.

---

## Project Health & Engagement

### Engagement Model
The primary health signal is **merchant engagement** (are they communicating?), not due date. Engagements run ~12 weeks; going past due is normal. Possible outcomes: merchant engages and activates, merchant never responds (complete without engagement), or project is terminated.

**North star**: Product activation — did the product(s) in the deal get activated?

### Silent Merchant Protocol
- **7-13 days silent**: Flag in startup. Suggest: scan email/Slack, review last timeline entry.
- **14+ days silent**: Flag as "CRITICAL — Silent". Suggest: ping merchant, check with SFDC Opportunity Owner, consider escalation.
- Escalate to: SFDC Opportunity Owner first → #accelerate-team Slack → direct manager.

### On Hold
Update PROJECT.md Status to "On Hold", add timeline entry with reason. Stays in `active/`, excluded from silent calculations.

### Priority Rebalancing
Suggest changes when:
- AONR > $100K and project silent 7+ days → suggest **High**
- Multiple open action items + silent → suggest upgrading
- AONR $0/TBD and on track → can stay **Low**
- Due date overdue alone does NOT trigger priority upgrade

---

## Project Lifecycle

1. **Create**: New merchant detected via one of three sources:
   - **Slack handover scan** (preferred — fully automated): scan-review Phase 0 invokes `/handover-scanner` which **reads** `#ven-ext-stripe-accelerate-amer` (and the legacy `#accelerate-qualification`) by channel ID, parses new threads via `scripts/handover-parse.py`, and classifies them against the roster via `scripts/handover-match.py`. Roster-matched threads become `proposals` handed to the `handover-bootstrap` skill; handover-shaped threads that match no roster row are surfaced as `triage` (never auto-bootstrapped). The skill runs `scripts/handover-create.py` which creates the folder + PROJECT.md (HO+MAN+SFDC+contact+AE pre-filled), Asana task, Hubble backfill, and appends to `data/handover-state.json`. No manual steps. (Retrieval is by channel ID, never by channel name — searching by name previously zeroed retrieval.)
   - **Manual paste** (same pipeline, paste mode): user pastes a Slack permalink or thread text → `handover-bootstrap` skill triggers the same `handover-create.py` flow.
   - **Hubble snapshot NEW rows or manual creation**: path for projects without a Slack handover. Sequence is order-sensitive:
     1. Create `projects/active/<slug>/` with template files (or run `python3 scripts/scaffold-from-hubble.py --apply`).
     2. Run `python3 scripts/hubble-reconcile.py --backfill --slug <slug>` — populates External Links, AONR, dates, Email search, and Key Contacts from Hubble contact data.
     3. **Search for handover thread**: read the handover channel by ID (`read_slack_channel_history` on `HANDOVER_CHANNEL_ID`) and filter in code by merchant name and/or AE name — never `search_slack_messages`/`in:<name>`. If found, run `scripts/handover-parse.py` on it to extract contacts and populate `## Key Contacts`, `Handover:` link, and `raw/comms.md`. If not found, add timeline entry: "Handover: not found — manual lookup needed."
     4. Run `python3 scripts/sync-to-asana.py --slug <slug>` — creates the Asana task with the now-populated PROJECT.md (contacts, links, Email search all filled). **Always run AFTER backfill + handover search** so the Asana description is complete.
2. **Track**: Log meetings, emails, Slack, decisions, action items (dual-write: Asana + local)
3. **Investigate**: Use Stripe tools when issues arise, log findings
4. **Archive**: When merchant goes live or project ends → verify all Asana subtasks completed, complete the Asana task, set "Active on Accelerate?" to NO (`PUT /tasks/{gid}` with `custom_fields: {ASANA_FIELD_ACTIVE: ASANA_FIELD_ACTIVE_NO}`), add final timeline entry, **invoke `/lessons-extract <slug>` to capture institutional knowledge into `data/lessons-learned/<slug>.md`** (and any `pattern-*.md` if cross-cutting patterns emerged), move to `archive/`, regenerate INDEX.md (`python3 scripts/regenerate-index.py`).

---

## Session Logging

Write at end-of-session ("Wrap up") to `sessions/YYYY-MM-DD.md`. Multiple sessions same day separated by `---`. Cap pending at top 10 if >15. The YYYY-MM-DD in session filenames and the `# Session — YYYY-MM-DD` header uses the consultant's local timezone (from the system `date` command), not UTC. This prevents evening sessions that cross midnight UTC from being misdated.

Format:
```
# Session — YYYY-MM-DD
## Summary — [2-3 sentences]
## Stats — Projects created/updated, emails scanned, raw saved, items created/completed (local + Asana subtasks), Asana comments added, issues opened/resolved
## Work Done — [bullets grouped by project]
## Pending / Next Session — [checkbox items]
## Workflow Changes — [CLAUDE.md or config changes]
```

Rules: session log written at wrap-up only, update `sessions/INDEX.md`, on startup read most recent session for continuity. During wrap-up, review if new MCP tools were used — add to `.claude/settings.local.json` permissions (never auto-approve external write ops).

---

## General Rules

- Dates: `YYYY-MM-DD`. Folders: `kebab-case`. Files: clean, scannable, bullets not paragraphs.
- Calendar events: `[Type] — [Merchant Name]`. Write ops require human confirmation.
- Always use `google_calendar_date_time_to_unix_timestamp` — never compute timestamps manually.
- Raw comms: always append full content to `raw/comms.md` + timeline summary.
- Weekend: skip Gmail/Slack/Calendar fetches. Override with explicit requests.
- Merchant name matching: fuzzy, case-insensitive, partial. Support account ID lookup.
- **New contact discovery**: When an email or Slack message involves a new address/handle not in PROJECT.md, immediately add it to Key Contacts + update the Email search query in Communication. This prevents future scan misses.
- **Email Query Format**: Build queries with three layers, in this priority order:
  1. **Domain search** — `from:company.com OR to:company.com` for each company domain. This catches all employees, including new contacts. Skip generic domains (gmail.com, icloud.com, hotmail.com, outlook.com, yahoo.com).
  2. **Name search** — `from:"First Last"` for contacts who use personal/generic email providers. Catches them even if they email from a different personal address.
  3. **Specific address** — `from:personal@gmail.com OR to:personal@gmail.com` for personal email addresses (as fallback alongside the name search).
  - Example (company-only): `from:example.com OR to:example.com`
  - Example (mixed): `from:example.com OR to:example.com OR from:"Jane Doe" OR from:jane.personal@example.com OR to:jane.personal@example.com`
  - When creating a new project, always set the Email search query using this format. Never leave as TBD if contact info is available.
- Never delete project data — archive instead.
- Error handling: timeout → retry once, 403 → note + suggest OAuth check, rate limit → wait 30s, during scans → log error per project and continue.
- **Product brand disambiguation (mandatory)**: When the user's request mentions a product brand or sub-product name (e.g. `BetaProduct`, `GammaSuite`, `DeltaFlow`), the brand is a hint, NOT a slug. Before answering, you MUST:
  1. Resolve which `projects/active/<slug>/` the brand lives under by grepping `Products:` and the H1 title across all PROJECT.md files (and the H1 of `issues/*.md` if needed).
  2. If exactly one match → proceed with that slug.
  3. If multiple matches OR the brand maps to a sub-product within a parent merchant (e.g. `BetaProduct` is one of several products under `parent-merchant-slug`) → ask the user to confirm which slug + which product surface they mean before answering. Do not assume the most-recently-discussed product applies.
  4. If zero matches → say so explicitly and ask for the slug.
  Why this rule exists: a real-world hallucination occurred when a question about one sub-product was answered with a different sub-product of the same parent merchant. Owning the hallucination after the fact is not enough — disambiguate before answering.
