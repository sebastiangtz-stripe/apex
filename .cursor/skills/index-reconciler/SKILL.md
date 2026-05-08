---
name: index-reconciler
description: >-
  Regenerates projects/INDEX.md from the filesystem (active + archive folders),
  per-project PROJECT.md fields, and the Hubble snapshot. Computes real OVERDUE
  flags against today's date, never lists archived projects in active sections,
  and surfaces Hubble drift (archive candidates + new rows). Use when the user
  says "regenerate index", "fix the dashboard", "rebuild INDEX", or as part of
  the /catchup skill. Also runs at startup if INDEX.md is older than 3 days.
---

# Index Reconciler

The hand-maintained `projects/INDEX.md` rotted between 2026-04-11 and 2026-05-07: it kept
listing archived merchants in active sections, computed OVERDUE flags
against month-old dates, and required manual maintenance that never happened. This skill
makes the dashboard a derived view.

## Workflow

1. Run the regeneration script:

   ```
   python3 scripts/regenerate-index.py
   ```

2. Read the script's stdout summary (e.g. `Wrote projects/INDEX.md (35 active, 3 archived, 35 rows, 0 parse errors)`).

3. Read `projects/INDEX.md` and surface to the user:
   - Top of any **High Priority** OVERDUE block
   - Anything in the **Hubble Cross-Check** → `Archive candidates` or `New Hubble rows` section (these need human triage)
   - Any `Parse Errors` (PROJECT.md files that couldn't be parsed)

## Flags

- `--dry-run`: print to stdout instead of writing
- `--no-hubble`: skip the Hubble cross-check (use when offline or snapshot is stale)

## What the script does (so you can answer "how does this work?")

- Reads every `projects/active/<slug>/PROJECT.md` and parses the Overview block (Name,
  Products, Status, Priority, Due, AONR).
- Groups by Priority → High / Medium / Low / Unspecified, sorted by Due ascending.
- Computes Flag from Due relative to today's date:
  `OVERDUE (Nd)` / `Due today` / `Due tomorrow` / `Due in Nd` / `On Hold` / `**P0** — <status>`.
- Excludes anything in `projects/archive/` from active sections.
- Cross-checks against `data/hubble-snapshot.json`: matches via per-project `hubble.json`
  `project_id` first (authoritative), falls back to fuzzy name match for projects without
  `hubble.json`. Surfaces `archive_candidates` (active locally, not in Hubble In Progress)
  and `new_projects` (Hubble rows without a local folder).

## Hard rules

- **Never edit `projects/INDEX.md` by hand.** It is regenerated. Edits to PROJECT.md fields
  (Priority, Status, Due, AONR) flow into the next regeneration.
- **Don't auto-archive on Hubble drift.** Archive candidates are surfaced for human confirmation
  only — the user decides whether the local folder should move to `projects/archive/` or
  whether the Hubble row needs a status correction.
- **Run after every `python3 scripts/asana-reconcile.py`** or after any project create/archive,
  so the dashboard never lags reality.
