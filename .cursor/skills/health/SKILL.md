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

Render per `response-style.mdc`: a status line using the fixed symbol set,
humanized dates/money (`$120K`, `3d ago`, `Mon 2pm` — not raw ISO/integers),
and **only sections that have content** (a merchant with no overdue items, no
broken commitments, and no drafts should not show those empty headers). One
screen. Lead symbol by state: ✅ on track · ⚠ silent 7–13d / drift · 🔴 silent
14+d / broken commitment · ⏸ on hold · ⏳ waiting on merchant.

```
## Health — <Merchant> (<slug>)

<symbol> <status> · <priority> · AONR <$120K> · due <Mon> · AE <name>
<one-line headline>

### Engagement
<symbol> <N>d silent — last activity <relative date> (<type>)

### Action items (<N> open)
- 🔴 Overdue <N> — top: <description> (due <relative>)
- ⏳ Due this week <N>
- By tag: <only tags with count >0 — e.g. #reply 3 · #research 1>

### Needs attention          ← omit this whole block if nothing below applies
- 🔴 Broken commitment: "<promise>" — overdue <N>d
- ⏳ Stale draft: <name> — <N>d unsent
- ⚠ Drift: <specific>
- ⚠ Hubble status: <X>       (only if not "In Progress")
- ⚠ Email query: <N> contacts uncovered

### Recent activity
- <relative date> — <type> — <one-line summary>   (last 3)

### Specialist history       ← only if prior runs exist
<N> runs · most recent: <topic> (<relative date>)

### Next step
<one concrete action — highest-priority signal: broken commitment > overdue #reply > silent 14+d>
```

Drop the raw scan timestamps and inline file paths from the default view —
they're mechanics, available on "show details".

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
