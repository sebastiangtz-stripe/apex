---
name: drift-audit
description: >-
  Read-only audit of workspace state inconsistencies — INDEX.md vs filesystem,
  INDEX freshness, slug collisions (e.g. `acmeglass` vs `acme-auto-glass`),
  PROJECT.md hygiene (TBD email queries with Key Contacts present, missing
  Status/Priority/Account ID), and scan-state.json sanity (future timestamps,
  duplicates, stale scans). Use when the user says "audit drift", "what's broken",
  "workspace health", or as part of weekly hygiene. Surfaces things to fix —
  never fixes them.
---

# Drift Audit

Read-only structural audit. Catches the failure modes that crept in over April-May 2026:
INDEX.md rotting, archived projects still listed, slug duplicates accumulating, scan-state
drift, PROJECT.md fields silently TBD even after first comm.

## Workflow

```
python3 scripts/drift-audit.py
```

Optional flags:
- `--json` — machine-readable output (for scripting / hooks)
- `--section A,C,E` — run a subset (A=index, B=freshness, C=slug, D=project.md, E=scan-state)

The script exits `0` on a clean run, `1` when any drift is found.

## What the audit covers

| Section | Checks |
|---|---|
| **A. INDEX vs filesystem** | Archived slugs still listed (the Apr 11 leak pattern), listed slugs missing on disk, active slugs missing from INDEX.md |
| **B. INDEX freshness** | Days since `Last reconciliation` header. Stale = >3 days. |
| **C. Slug collisions** | Same merchant under multiple slugs via (a) shared normalized tokens or (b) shared compressed substring (catches e.g. `acmeglass` ↔ `acme-auto-glass`). Also same `hubble.json` `project_id` under multiple slugs. |
| **D. PROJECT.md hygiene** | TBD email queries despite populated Key Contacts; missing Status/Priority; no Account ID and no Account Manifest URL. |
| **E. scan-state.json sanity** | Missing scan-state for projects with comms; future timestamps; duplicate IDs in `logged_email_ids`/`logged_slack_thread_ids`; last scan >14 days old. |

## When to invoke

- Weekly (or as part of `/catchup`)
- After a slug rename or merge to confirm the merge was clean
- When a hallucination or context mistake suggests data drift may be a contributor
- When the user says "audit drift", "what's broken in the workspace", "workspace health"

## After running

Surface the findings in conversation, **grouped by severity**:

1. **CRITICAL** (Section A archived-but-listed, Section C `hubble_pid_collisions`, Section E
   future timestamps): these cause active hallucinations or bad routing. Suggest immediate fix.
2. **High** (Section B stale >7d, Section C `name_collisions` with strong evidence, Section
   D `tbd_email_query_with_contacts`): these silently degrade scan accuracy. Suggest fix
   this week.
3. **Medium / informational** (Section E stale_scan, Section D missing fields without
   downstream impact): note for later.

## Hard rules

- **Read-only.** This skill never edits files. The follow-up fixes are user-confirmed and
  done via the relevant other skill (e.g. `/index-reconciler`, `merge-slugs.md` runbook,
  PROJECT.md template fill-ins).
- **Don't auto-fix.** Each finding gets surfaced for human triage.
- **Output is suppress-able.** Section subsetting (`--section`) lets the user skip noisy
  sections during fast triage.
