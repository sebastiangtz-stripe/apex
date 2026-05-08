---
name: health
description: >-
  Generated-on-read per-merchant health snapshot — silence, open items by tag,
  AONR, drift vs Hubble, broken commitments, pending drafts, recent activity.
  Use when the user says "health for <merchant>", "/health <slug>", "is
  <merchant> on track", or before any merchant-facing decision.
---

# Health

Composes a one-screen health view per merchant from existing primitives. No new files —
everything is read fresh on each invocation.

## Inputs

- **`slug`** (required): kebab-case merchant slug. Apply Product Brand Disambiguation
  (CLAUDE.md General Rules) if the user gave a brand name instead.

## Workflow

Compose by delegating to existing skills + scripts in parallel where possible:

### Step 1 — Run these in parallel

1. **`/quick-context <slug>`** — returns the JSON envelope with status, products, AONR,
   priority, engagement (days_silent), action_items (open_total, overdue, by_tag),
   recent_activity (last 3 timeline entries), and the headline. This is the load-bearing
   call.

2. **`python3 scripts/list-actions.py --slug <slug> --json`** — flat list of every open
   action item with full metadata (already in quick-context, but JSON form makes filtering
   easy if you want to call out specific items).

3. **`python3 scripts/stale-drafts.py --slug <slug> --threshold-days 7 --json`** —
   surfaces forgotten drafts.

4. **`python3 scripts/contact-gap-audit.py --slug <slug> --json`** — checks Email search
   query coverage.

5. **`python3 scripts/drift-audit.py --section A,C,D,E --json`** then filter to lines
   matching the slug — surfaces local drift specific to this merchant.

6. **Read** `projects/active/<slug>/commitments.md` (if it exists) and grep for `Status: overdue`.

7. **Read** `projects/active/<slug>/specialist-runs.json` (if it exists) for the count of
   prior specialist passes and the most recent topic.

8. **Read** `projects/active/<slug>/hubble.json` (if it exists) for the Hubble
   project_status — flag if it's not `In Progress` (potential archive signal).

### Step 2 — Synthesize the snapshot

Render in this format (fits one screen):

```
## Health — <Merchant Display Name> (<slug>)

**Status**: <status> | **Priority**: <priority> | **Due**: <due> | **AONR**: <aonr> | **AE**: <ae>

**Headline**: <quick-context.headline>

### Engagement
- Days silent: <N>d (last activity: <date>, type: <email|slack|...>)
- Last email scan: <ts>  | Last slack scan: <ts>

### Action items (<open_total> open)
- Overdue: <N> — top: [<tag>] <description> — Due <date>
- Due this week: <N>
- By tag: #email N, #reply N, #waiting N, #research N, ...

### Broken commitments
<list overdue lines from commitments.md, OR "none" OR "(commitments.md not yet adopted for this slug)">

### Pending drafts (>7d unsent)
<list, OR "none">

### Drift signals
- Local drift: <list any DRIFT lines from drift-audit matching this slug, OR "none">
- Hubble status: <In Progress | Archived | Discovery | ...>  <flag if !=In Progress>
- Email query coverage: <N gaps from contact-gap-audit>

### Specialist history
- <N> prior runs. Most recent: <topic> (<date>) — <outcome>
  (full register: projects/active/<slug>/specialist-runs.json)

### Recent activity (last 3 timeline entries)
- <date> — <type> — <one-line summary>
- <date> — <type> — <one-line summary>
- <date> — <type> — <one-line summary>

### Suggested next 1-3 actions
1. <concrete action grounded in the highest-priority signal — broken commitment > overdue with merchant ask > silent>
2. <...>
3. <...>
```

### Step 3 — Suggest one follow-up

Based on the highest-priority signal, propose ONE concrete next step:
- If broken commitment → "Want me to draft a reply to <contact> addressing the <promise>?"
- Else if overdue with #reply tag → "Want me to draft the reply to <merchant ask>?"
- Else if days_silent >= 14 → "Want me to draft a check-in to <primary contact>?"
- Else if pending drafts → "Want me to review the oldest stale draft (`<draft>`)?"
- Else if drift signals → "Want me to fix the drift (`<specific>`)?"

## Hard rules

- **Compose, don't reimplement.** Always delegate to existing skills/scripts. If you find
  yourself duplicating logic from `quick-context` or `list-actions`, refactor those —
  don't fork them here.
- **One screen.** If a section has >5 items, summarize and link out. The whole point is
  one-screen triage.
- **Disambiguate first.** If `<slug>` is ambiguous, apply Product Brand Disambiguation
  before running anything else. Otherwise you'll waste 5 parallel calls on the wrong slug.
- **Read-only.** Never modify files. The follow-up suggestion is a question, not an
  action.
