# CLAUDE.md Trim Notes

## Round 2 — 2026-04-23 (Subagent rollout cleanup)

Triggered by introduction of `.cursor/agents/` subagents (`merchant-scanner`, `comms-analyst`, `hubble-analyst`, `stripe-jarvis`). All workflow detail that now lives inside subagent prompts was removed from `CLAUDE.md`, `.cursor/rules/`, and `.cursor/skills/` to avoid duplication.

### Removed from CLAUDE.md

- **Incremental Query Protocol** (~7 lines): full TTL/anchor/dedup/state-update spec. Now inside `.cursor/agents/merchant-scanner.md`.
- **Email Scan / Slack Scan** workflow (~7 lines): batch sizing, mention search, log-only rule, post-scan review trigger. Now in `.cursor/skills/scan-review/SKILL.md` + `merchant-scanner.md`.
- **Review** workflow (~25 lines): outbound identities, auto-close criteria (Step 1), new-item creation (Step 2), triage summary structure (Step 3). Now in `.cursor/agents/comms-analyst.md` + `scan-review/SKILL.md`.
- **Scan & Review Summary Format** code block (~15 lines): now in `scan-review/SKILL.md`.
- **Hubble Ingest / Reconciliation** detail (~40 lines): two-step flow, matching logic, command list, full column mapping table. Now inside `.cursor/agents/hubble-analyst.md` + `scripts/hubble-reconcile.py`. CLAUDE.md keeps a 4-line pointer.
- **Investigation & Research** Tier 1/2/3 descriptions (~6 lines): replaced with single-line pointer to `/stripe-jarvis`.
- **Agent Delegation** table reworked: removed generic "single agent" patterns, added concrete subagent → scenario mappings.

### Removed from .cursor/rules/

- **research-protocol.mdc**: collapsed Gates 1/2/3 + skeptic protocol references to a single delegate-to-Jarvis line. Kept Dual-Write Protocol and orchestrator-layer Scan Protocol rules (still enforced in main thread).
- **scans.mdc**: deleted full Incremental Query Protocol, outbound detection, Step 1/2/3 Review detail. Replaced with thin pointer to subagents + skill + research-protocol.

### Removed from .cursor/skills/

- **stripe-research/SKILL.md** (~150 lines, entire skill): deleted. Jarvis owns the methodology now and is auto-discovered by description.
- **scan-review/SKILL.md**: rewritten from 74 lines of inline scan/review logic to ~50 lines of fan-out orchestration.

### Updated .cursor/hooks.json

- The `preToolUse` Stripe research check was reworded: instead of pointing at the deleted `stripe-research` skill, it now reminds the main thread to delegate Stripe questions to `/stripe-jarvis`.

### Why

Cursor subagents have isolated context windows. Duplicating workflow detail in CLAUDE.md (which loads on every conversation) was wasting tokens that we now don't need to spend — the subagent prompts only load when the subagent is invoked. Goal: shrink the always-loaded surface and let isolated contexts hold the heavy detail.

---

## Round 1 — 2026-04-13

Original: 687 lines → Trimmed: 290 lines (58% reduction)

## What was removed (verbose padding, not functionality)

### Removed sections entirely:
- **Communication & Scheduling Tools Usage** (lines 590-610) — Gmail/Calendar/Slack tool documentation that Claude already knows. Replaced with concise mappings in Conversational Mappings tables.
- **Stripe Internal Tools Quick Reference** (lines 580-588) — tool name → description mapping. Redundant with Research Tool Reference table.

### Condensed significantly:
- **Conversational Mappings**: Removed "User says (examples)" column header verbosity. Kept all mappings but trimmed descriptions.
- **Email Scan + Slack Scan**: Were ~25 lines each with repeated Incremental Query Protocol references. Consolidated into single 6-line section referencing the protocol once.
- **Meeting Prep**: Was 12 lines of numbered steps. Condensed to 2 lines listing the parallel agent strategy + output fields.
- **Incremental Query Protocol**: Was 12 lines. Condensed to 4 bullets (same logic).
- **File Templates**: Issue, Draft, Raw Comms, Idea formats were full multi-line code blocks. Replaced with 1-line format summaries. PROJECT.md template kept in full.
- **Investigation & Research**: Tier descriptions were 3 verbose paragraphs. Condensed to 3 bullet points. Research Rules section removed (redundant with tier descriptions). Verification Pass condensed from 5 numbered items to 2 sentences.
- **Session Logging**: Was 15 lines + full code block template. Condensed to format summary + key rules.
- **Error Handling**: Was 5 bullet paragraphs. Condensed to single line with → separators.
- **Agent Delegation**: Was table + 5 guideline bullets. Condensed to table + 1-line guideline summary.

### Changed (not just trimmed):
- **Milestones → Product Activation**: Template section replaced. All 29 PROJECT.md files updated.
- **Priority model**: Due-date-centric → engagement-centric. "Overdue projects" de-emphasized, "Silent merchants" added as primary signal.
- **Escalation Protocol**: Reframed from "overdue action items" to "silent merchants".
- **Priority Rebalancing**: Removed "due date passed → upgrade to High" rule. Added "due date overdue alone does NOT trigger priority upgrade".
- **PROJECT.md template**: Removed Environment, Actual Go-Live fields (unused). Removed Jira from External Links (rarely populated).

## How to restore
If the trimmed version is missing something, check this file for what was removed. The original content was read in full during the 2026-04-13 (early AM) session — the session log at sessions/2026-04-12.md documents the exact state.
