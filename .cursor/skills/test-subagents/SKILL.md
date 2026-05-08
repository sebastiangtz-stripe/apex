---
name: test-subagents
description: >-
  Static contract validation for every Cursor agent, skill, and rule —
  front-matter, required sections, JSON example sanity. Catches Jarvis-style
  "summary-only" regressions at the contract level. Use weekly, before any
  agent/skill edit, or when the user says "test subagents", "validate skills",
  "smoke tests".
---

# Subagent + Skill Smoke Tests

The May 7 Jarvis "summary-only on two consecutive turns" failure was a contract
regression — a missing/weak Output section. This skill catches that class of
failure before it lands by statically validating every agent / skill / rule file.

## Workflow

```
python3 scripts/test-subagents.py
```

Optional flags:
- `--json` — machine-readable
- `--section agents,skills,rules` — subset

The script exits `0` when all pass, `1` on any failure.

## What gets validated

Per file:
- Front-matter parses
- Required keys present (`name`, `description` for agents/skills; `description` only for `.mdc` rules; agents also need `model`, `readonly`)
- `description` >=20 chars and contains a "use when" / "use proactively" / "when the user" phrase (agents/skills only — rules are always-on)
- Body has a Workflow / Process / Phase / Operating / Gate section
- Body has a Hard rules / GUIDELINES / RESTRAINTS section
- For agents documenting a `## Return value` JSON schema: the JSON example parses (after stripping JS-style comments, replacing placeholders, and tolerating common illustrative URL/`...` markers)

## When to invoke

- **Weekly** as part of a Sunday hygiene pass
- **Before any edit** to a file in `.cursor/agents/`, `.cursor/skills/`, or `.cursor/rules/`
- **In CI** (run the script in a pre-commit hook or GitHub Action against the workspace)
- When the user says "test subagents", "validate skills", "smoke tests", "agent contracts ok?"

## After running

For each `[FAIL]`:
- **Front-matter issue**: open the file and fix the front-matter directly. These are quick.
- **Missing section**: the file's contract is incomplete. Add the section with even a one-liner if the agent/skill is truly minimal — empty contract sections are still better than no section at all (the validator checks structure, not content).
- **JSON example issue**: usually means the example has unintentional syntax (e.g. mid-string angle-bracket placeholder, trailing comma, JS-style comment). Fix the example to be parseable.

## Hard rules

- **Never disable a check to make it pass.** The validator surfaces real fragility. If a check is wrong, fix the regex; don't just delete the failure.
- **Failures are the signal, not the noise.** A clean run is the goal because that's when contract regressions become visible.
- **Run before committing.** Easier to fix in the diff than after merging.
