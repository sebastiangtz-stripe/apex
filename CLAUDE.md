# Stripe Accelerate — Project Management Workspace

Conversational PM system for [YOUR_NAME], a Stripe Accelerate consultant managing 25-35 merchant projects. [YOUR_NAME] talks naturally, Claude interprets intent and manages files.

**Architecture**: Asana is the project and task management layer (board: "[YOUR_BOARD_NAME]"). Local markdown stores raw communications, timelines, issues, drafts, and session logs. Cursor agents are the primary interface — no custom dashboard.

**Shared with Diego via apex**: This workspace is the upstream of a peer-shared agent template at `~/Documents/SGG-Assistant-Template/`, mirrored to GitHub at [`sebastiangtz-stripe/apex`](https://github.com/sebastiangtz-stripe/apex). [YOUR_NAME] and Diego both run their own live workspaces with their own merchant data, both publish improvements (skills, agents, scripts, runbooks, CLAUDE.md changes) to apex via `python3 scripts/sync-template.py --push`, and both pull updates from apex into their own workspaces. Full protocol + roles + conflict rules in [`data/runbooks/template-sync.md`](data/runbooks/template-sync.md). **Never** push merchant data, sessions, or `.env` to apex — the sync script enforces this with a leak scan that fails hard on any merchant token.

---

## Subagent Inventory

Specialized Cursor subagents live in `.cursor/agents/`. Each has its own context window, so noisy intermediate output (full email bodies, internal search results, Hubble JSON) never bloats the main thread. Invoke with `/name` or by mentioning naturally.

| Subagent | Use for | Model |
|----------|---------|-------|
| `merchant-scanner` | Per-merchant Gmail + Slack scan with dedup. Always fanned out in parallel by the `scan-review` skill. | claude-4.6-sonnet |
| `comms-analyst` | Read-only review of one merchant's full `raw/comms.md` to propose auto-closures + new action items. Main thread executes the dual-write. | claude-4.6-sonnet |
| `hubble-analyst` | Refresh `data/hubble-snapshot.json` if stale, run `scripts/hubble-reconcile.py`, return structured diff. Used by Auto-Startup Agent E. | fast |
| `stripe-jarvis` | Any Stripe technical question (Tier 1/2/3 owned by Jarvis itself). Self-contained: searches internal docs, Trailhead, Sourcegraph, Jira, Slack, public docs. | claude-opus-4-7 (Max Mode) |

**Skills vs. subagents**: Skills (`.cursor/skills/*`) orchestrate sequential PM workflows. Subagents (`.cursor/agents/*`) do heavy reading/research in isolated contexts. Skills delegate to subagents, never the other way around.

---

## Auto-Startup Summary

**Step 0 — Fresh-workspace check (before anything else).** Read `.env`. If the file is missing, OR if any value equals `REPLACE` / starts with `REPLACE_WITH`, OR if `ASANA_PAT` is empty: skip the entire auto-startup below and surface a single line — *"Workspace not configured. Run `/setup` to onboard."* Then stop and wait for the user. This prevents a cascade of agent failures on a fresh clone before the user even types anything. Do NOT run any of the steps below until `.env` is fully populated.

If `.env` looks healthy, run these in **parallel** where possible:

1. Run `TZ="[YOUR_TIMEZONE]" date '+%A %Y-%m-%d %H:%M:%S %Z'` for current date/time (includes day of week). **Always use your local timezone.** Never infer the day of week — read it from the command output.
2. **Agent A** (Asana reconcile): Run `python3 scripts/asana-reconcile.py` to sync any changes made in Asana since last session (items completed on mobile, etc.).
3. **Agent B** (silence scan): Read each project's `timeline.md` for last activity date. Compute days since last activity.
4. **Agent C** (calendar): Fetch today's calendar (skip on weekends — note "Weekend mode").
5. **Agent D** (session): Read most recent `sessions/*.md` for continuity.
6. **Agent E** (Hubble sync): Invoke `/hubble-analyst`. It refreshes the snapshot if stale and returns a structured diff (new projects, archive candidates, drift). The verbose JSON stays inside the subagent.
7. After agents complete, read `action-items.md` files for overdue/upcoming items (3 days), and read `commitments.md` files (where present) for any `Status: overdue` lines. Asana is the authority for open items; local files are the backup.
8. Present concise summary:
 - **Asana sync**: Changes detected by reconcile (if any)
 - **Today's schedule**: Meetings with merchant matches
 - **Silent merchants**: Projects with no activity in 7+ days, sorted by AONR. 7-13d = "Silent", 14+d = "CRITICAL — Silent". Suggest: scan email/Slack, ping contact, check with SFDC Opportunity Owner.
 - **Overdue items**: Action items past due (informational — not top priority)
 - **Broken commitments**: Surface ANY `commitments.md` line with `Status: overdue` from any merchant. These get higher priority than overdue action items because they represent things you explicitly promised the merchant. Show as `[<slug>] promised <date>: "<promise>" (overdue Nd)`.
 - **Due soon**: Items due within 3 days
 - **Tag distribution**: Open items by tag (2+ only)
 - **On Hold**: Paused projects with reason
 - **Priority suggestions**: Per rebalancing rules below
 - **Hubble**: Surface only if `/hubble-analyst` returned non-empty `new_projects`, `archive_candidates`, or material `drift`. Non-blocking otherwise.
 - **Drift audit (weekly)**: If today is Monday OR last `data/runbooks/drift-audit-last-run.txt` mtime >7d, run `python3 scripts/drift-audit.py`. Surface any CRITICAL findings (Section A archived-but-listed, Section C hubble_pid_collisions, Section E future_timestamp). Skip otherwise.
 - **Template drift (apex)**: Run `python3 scripts/sync-template.py --check`. If it reports DRIFT, surface a single-line note: *"Template drift: N template-relevant paths differ from apex. Run `python3 scripts/sync-template.py --push --message <msg>` after wrap-up."* Non-blocking; informational only.
 - **Last session**: Date, 1-sentence summary, pending count
 - **Quick actions**: 1-2 concrete next steps
9. At scale (35+): Cap at top 10 items, summarize rest

---

## Conversational Mappings

Interpret intent, not rigid commands. Key mappings:

### Project Management

| User says | Action |
|---|---|
| "New project: [Merchant], [acct_id]" | Create `projects/active/<merchant-kebab>/` with template files, create Asana task in Integration section, save GID to `asana.json`, update INDEX.md |
| "Here's the transcript from my call with [merchant]" | Save to `raw/comms.md`, summary to `timeline.md`, extract action items as Asana subtasks |
| "What's happening with [merchant]?" | Read PROJECT.md + timeline.md + Asana task (status, subtasks), concise status |
| "What are my priorities?" | Read Asana board for open subtasks + local timelines for silence detection |
| "Show me all #[tag] items" / "Batch my emails" | Read Asana subtasks, filter by tag prefix in name, grouped by parent task |
| "Draft an email for [merchant] about [topic]" | Read context, research if needed, draft to `drafts/<topic>.md` |
| "Archive [merchant]" / "[merchant] is live" | Complete Asana task, move to `archive/`, update INDEX.md |

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
1. Check mtime of `data/hubble-snapshot.json`. If missing or older than `HUBBLE_SNAPSHOT_TTL_HOURS` (default 24h), run the saved query via `run_hubble_query` with the SQL from the saved query plus `AND lower(p.project_lead_user_name) LIKE '%<your_first_name_lowercased>%'` appended. Overwrite the snapshot with `{ fetched_at, lead_filter, saved_query_id, hubble_query_id, row_count, projects }`.
2. Run `python3 scripts/hubble-reconcile.py` to diff. In the startup summary, surface only if non-empty: `NEW PROJECTS` (Hubble rows without a local folder), `ARCHIVE CANDIDATES` (local folders missing from Hubble `In Progress`), and material `DRIFT` (AONR / AE / dates).
3. On explicit user confirmation of drift, run `python3 scripts/hubble-reconcile.py --backfill` to apply PROJECT.md + `hubble.json` updates.
4. Never auto-archive or auto-create projects from a diff; these remain human-confirmed operations.

### Email / Slack Scan + Review

Triggered when the user says "scan email", "scan Slack", "check all projects", "review open items", or "what's new". Owned by the `scan-review` skill (`.cursor/skills/scan-review/SKILL.md`):

- Phase 1 fans out `/merchant-scanner` per active merchant (Incremental Query Protocol, dedup by message_id, log every email/thread to `raw/comms.md` + `timeline.md`, update `scan-state.json`).
- Phase 2 fans out `/comms-analyst` per merchant with new content (read-only proposals: auto-closures + new items + waiting list + commitments).
- Main thread executes the dual-write to Asana + local `action-items.md` and presents the consolidated triage summary.

All implementation detail (TTL, outbound detection, auto-close criteria, complexity scoring, summary format) lives inside the subagent and skill files — not here.

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
**Sections**: Received, [GREEN], [YELLOW], Completed, Terminated
**Tasks**: One per merchant with custom fields.
**Subtasks**: Action items. Name format: plain action-verb description (e.g. `Send revised contract to ABC Co`). Tag is set as the Asana custom field on the subtask, not in the name. Due dates set on subtask.

### Dual-Write Protocol

Every change must update **both** Asana and local files. Asana is the management view; local is the agent context store.

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
`~/Documents/SGG-Assistant-Template/`, mirrored to GitHub at
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
| "pull from apex" | Run `git -C ~/Documents/SGG-Assistant-Template/ pull --rebase`. Then offer to manually walk diffs into the live workspace (no auto-apply — merchant data must be preserved). |

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
manually in `~/Documents/SGG-Assistant-Template/` and re-run.

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

---

## Agent Delegation (Cursor)

Cursor subagents (`.cursor/agents/*`) handle context-heavy work in isolated windows. Skills (`.cursor/skills/*`) orchestrate sequential workflows. See **Subagent Inventory** above for the full list.

| Scenario | Approach |
|---|---|
| Quick lookup | Direct tool call, no subagent |
| Stripe technical question | `/stripe-jarvis` (always — never answer in main thread) |
| Email/Slack scan (one merchant or all) | `scan-review` skill → fans out `/merchant-scanner` per merchant in parallel |
| Review phase after scan | `scan-review` skill → fans out `/comms-analyst` per merchant; main thread does the dual-write |
| Hubble snapshot refresh + diff | `/hubble-analyst` (Auto-Startup Agent E or on demand) |
| Auto-Startup | 5 parallel agents: A (Asana reconcile), B (silence scan), C (calendar), D (session), E (Hubble) |
| Meeting Prep | `meeting-prep` skill (parallel reads of PROJECT.md + Asana + comms + fresh Gmail/Slack) |
| Email drafting/sending | `email-agent` skill (sequential, returns to main thread for approval) |

Guidelines:

- Parallel when independent, sequential when dependent — for fan-outs (scanner, analyst), issue all calls in a single message.
- Subagents return small structured summaries; main thread executes any writes that require Asana/local dual-write.
- Cap concurrent fan-outs at ~10 in any single message.

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

1. **Create**: New merchant (detected via Hubble snapshot NEW rows, Slack handover, or manual) → create `projects/active/<slug>/` with template files (copy Kantata Project ID, Kantata Workspace, Salesforce URL, CSAT from Hubble snapshot; acct_id + contacts still manual), then run `python3 scripts/sync-to-asana.py --slug <slug>` to create Asana task + subtasks, followed by `python3 scripts/hubble-reconcile.py --backfill --slug <slug>` to write `hubble.json`
2. **Track**: Log meetings, emails, Slack, decisions, action items (dual-write: Asana + local)
3. **Investigate**: Use Stripe tools when issues arise, log findings
4. **Archive**: When merchant goes live or project ends → verify all Asana subtasks completed, complete the Asana task, set "Active on Accelerate?" to NO (`PUT /tasks/{gid}` with `custom_fields: {ASANA_FIELD_ACTIVE: ASANA_FIELD_ACTIVE_NO}`), add final timeline entry, **invoke `/lessons-extract <slug>` to capture institutional knowledge into `data/lessons-learned/<slug>.md`** (and any `pattern-*.md` if cross-cutting patterns emerged), move to `archive/`, regenerate INDEX.md (`python3 scripts/regenerate-index.py`).

---

## Session Logging

Write at end-of-session ("Wrap up") to `sessions/YYYY-MM-DD.md`. Multiple sessions same day separated by `---`. Cap pending at top 10 if >15.

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
