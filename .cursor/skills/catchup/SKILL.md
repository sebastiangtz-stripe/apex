---
name: catchup
description: >-
  Closes the "reconcile gap" by running asana-reconcile, /hubble-analyst,
  scan-review across all active merchants, and /index-reconciler in one command.
  Returns a single consolidated triage summary instead of piecemeal updates. Use
  when the user says "catch me up", "catchup", "catch up", "reconcile", "I've
  been away", "what did I miss", or after any gap of 3+ days between sessions.
---

# Catchup

The "Reconcile gap since Apr 30" item appeared as Pending in three consecutive sessions
(2026-04-30, 2026-05-05, 2026-05-07) — every session promised to close it, none did. The
reason was operational, not motivational: there was no single command for it. This skill
is that command.

## When to invoke

- User says "catch me up", "catchup", "I've been away", "what did I miss", "let's reconcile".
- More than 3 days have passed since the most recent `sessions/*.md` file.
- After a long PTO / weekend gap.

## Workflow

Run these four phases. Each phase blocks the next — the order is load-bearing because
later phases consume earlier phases' outputs.

### Phase 1 — Asana ↔ local reconcile (deterministic)

```
python3 scripts/asana-reconcile.py
```

This syncs both directions: Asana completions → local `[x]`, local new items → Asana
subtasks, local completions → Asana completions. Capture stdout — count of changes per
direction goes in the final summary.

### Phase 2 — Hubble snapshot + diff (subagent)

Invoke `/hubble-analyst` with no arguments. It refreshes the snapshot if older than the
TTL, runs `scripts/hubble-reconcile.py`, and returns structured `new_projects`,
`archive_candidates`, and `drift` arrays. Hold these for the final summary — surface only
non-empty ones.

### Phase 3 — scan-review across all merchants (skill)

Invoke the `scan-review` skill. It now has three phases of its own: Phase 0 sweeps the
Slack handover channel via `/handover-scanner` and bootstraps any new merchants found
(folder + Asana + Hubble backfill); Phase 1 fans out `/merchant-scanner` per active
merchant in parallel; Phase 2 fans out `/comms-analyst` per merchant with new content;
Phase 3 does the dual-write to Asana + local for auto-closures and new action items.
The output is the standard `## Scan & Review Summary`, now including a `New Handovers`
section at the top when Phase 0 finds anything.

This is the heaviest phase — expect 30-90s for a full fan-out across ~35 merchants.

### Phase 4 — Index regeneration (script)

Run the `/index-reconciler` skill (or directly `python3 scripts/regenerate-index.py`).
Picks up any moves to `archive/`, new projects created in Phase 3, and recomputes OVERDUE
flags against today.

## Output: single consolidated catchup summary

Present **one** summary block, never piecemeal. Format:

```
## Catchup Summary — YYYY-MM-DD
_(Gap closed: last session YYYY-MM-DD → today, N days)_

### Phase 1: Asana reconcile
- Asana → Local: N completions synced
- Local → Asana: N new subtasks, N completions synced

### Phase 2: Hubble
- N new projects (list slug suggestions)
- N archive candidates (list slugs)
- N drift items (one-line each)

### Phase 3: Scan & Review
- N merchants scanned, N had new activity
- N auto-closed action items
- N new action items created
- N waiting on merchant (>3 days silent)
- N new contacts discovered

### Phase 4: Index regenerated
- N active, N archived
- Top OVERDUE: <merchant> (<Nd>), <merchant> (<Nd>), ...
- Hubble cross-check delta: N archive candidates, N new rows

### Recommended next actions (top 3)
1. <merchant>: <action> (driver: P0 / OVERDUE / commitment broken / etc.)
2. <merchant>: <action>
3. <merchant>: <action>
```

## Hard rules

- **Phases run sequentially**, not in parallel — Phase 4 needs Phase 3's project moves to
  be on disk, Phase 3 needs Phase 2's new project folders to exist (if any were created
  via human confirmation), etc.
- **Never skip Phase 4** — the whole point is to leave the workspace clean. INDEX.md being
  stale is exactly what triggered the need for this skill.
- **Surface, don't auto-act** on Phase 2 archive candidates and new projects. Those need
  human confirmation per CLAUDE.md.
- **One summary, not four.** Don't dump intermediate output into the conversation.
