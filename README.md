# Stripe Accelerate PM Assistant — Agent Template

Cursor-based conversational PM system for a Stripe Accelerate consultant managing
~25-35 merchant projects in parallel. The assistant interprets natural-language
intent, manages a hybrid Asana + local-markdown workspace, fans out specialized
subagents for heavy reading/research, and keeps every action item dual-written
between Asana and local files.

This repo is the **template** — no real merchant data, no secrets. Each user
clones it, fills in `.env`, and starts adding their own projects under
`projects/active/`.

## What's in here

- **`.cursor/agents/`** — 5 subagents with isolated context windows
  - `merchant-scanner` — incremental Gmail + Slack scan per merchant
  - `comms-analyst` — read-only proposal of auto-closures + new action items
  - `hubble-analyst` — SFDC/Kantata snapshot refresh + diff
  - `stripe-jarvis` — Stripe technical research (Tier 1/2/3 framework)
  - `quick-context` — fast headline lookup
- **`.cursor/skills/`** — 15 orchestrated PM workflows
  - Daily ops: `catchup`, `scan-review`, `meeting-prep`, `email-agent`, `log-comms`, `health`, `action-items`
  - Hygiene: `drift-audit`, `contact-gap-audit`, `index-reconciler`, `test-subagents`
  - Knowledge: `lessons-extract`, `recall`, `specialist-prompt`, `weekly-metrics`
- **`.cursor/rules/`** — always-applied conventions (action items format, email drafting, PM workspace, research protocol, scan protocol)
- **`.cursor/hooks/`** — file-shape validators that fire on edit (action-items, timeline, PROJECT.md, scan-state, drafts)
- **`scripts/`** — Python automation: Asana ↔ local reconcile, Hubble ingest, drift audit, contact gap audit, action-items rollup, weekly metrics, INDEX regeneration, contract validation
- **`data/runbooks/`** — stable reference templates (file shapes, action-items format, Asana API, slug-merge runbook)
- **`templates/emails/`** — 5 email templates (intro, follow-up, check-in, escalation, technical-answer)
- **`projects/active/example-merchant/`** — worked example showing canonical project layout
- **`CLAUDE.md`** — top-level conversational mappings + auto-startup protocol

## Setup

See [`SETUP.md`](SETUP.md) for the 15-20 minute walkthrough (clone → `.env` →
Asana board → first scan).

## Architecture in one diagram

```
┌──────────────────────────────────────────────────────────────────┐
│  Cursor (main thread)                                            │
│  ─ reads CLAUDE.md + .cursor/rules/ on every conversation        │
│  ─ delegates heavy work to subagents in isolated context windows │
└─────────┬──────────────────┬─────────────────┬──────────────────┘
          │                  │                 │
          ▼                  ▼                 ▼
   merchant-scanner    comms-analyst     stripe-jarvis
   (Gmail + Slack)    (review proposals) (Tier 1/2/3 research)
          │                  │                 │
          ▼                  ▼                 ▼
   raw/comms.md +    structured JSON     issues/jarvis-*.md
   timeline.md       proposals only      (5-bullet TL;DR back)

  Main thread executes the dual-write:
    Asana subtask ←→ projects/active/<slug>/action-items.md
```

## Design principles

1. **Asana is the management view; local markdown is the agent context store.**
   Every action item is dual-written. Reconciliation runs at startup.
2. **Subagents own heavy reading.** Email bodies, Hubble JSON, internal search
   results never bloat the main context window.
3. **Skills orchestrate; subagents read.** Skills are sequential PM workflows.
   Subagents are isolated readers/researchers.
4. **Engagement is the primary health signal**, not due date. 12-week engagements
   routinely run past due — silence is what matters.
5. **Hubble is source of truth for roster + AONR + dates.** Local PROJECT.md
   only owns merchant-specific manual fields (account ID, contacts, Slack channels).

## Status

Template extracted from a live workspace running ~35 active merchant projects.
All 25 agent/skill/rule contracts pass static validation
(`python3 scripts/test-subagents.py`).

## License

Internal Stripe use. Do not commit real merchant data, OAuth tokens, or PATs to
this repo. The `.gitignore` blocks the obvious paths but it's the contributor's
responsibility to confirm before pushing.
