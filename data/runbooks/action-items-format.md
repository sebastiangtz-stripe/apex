# Runbook — Action Item Format, Tags, and Complexity

Stable reference for action items. CLAUDE.md points here.

## Line format

```
- [ ] #tag1 #tag2 — Description — Complexity: L/M/H — Owner: who — Due: YYYY-MM-DD — Source: <ref>
```

- `[ ]` open, `[x]` completed
- 1-3 tags (always at least one action tag)
- Description: action-verb phrase
- Complexity: `L` / `M` / `H`
- Owner: usually `[YOUR_NAME]` (or specific Stripe internal)
- Due: `YYYY-MM-DD`, `TBD`, or `ASAP`
- Source: `<YYYY-MM-DD>/<type>:<ref>` — type is `email|slack|meeting|jira|hubble|health-status`

## Tag vocabulary (1-3 per item)

| Tag | Use when |
|---|---|
| `#email` | Send/reply outbound message |
| `#reply` | Answer a specific question |
| `#research` | Investigate before acting |
| `#prep` | Prepare for upcoming meeting |
| `#schedule` | Create/move/confirm calendar event |
| `#track` | Check status (no message needed) |
| `#log` | Document something administrative |
| `#waiting` | Modifier — blocked on external party (pair with action tag) |

Rules:
- `#reply` for specific questions; `#email` for generic follow-ups
- `#research` + `#reply` when a question needs investigation
- `#waiting` is never alone — pair with `#track`, `#email`, `#reply`, etc.

## Complexity scoring

Auto-assign when creating. Set on the Asana subtask via `custom_fields: { ASANA_AI_FIELD_COMPLEXITY: <GID> }`.

| Score | Default tags | Description |
|---|---|---|
| **Low** | `#log`, `#track`, `#schedule`, `#waiting` | Quick task, single step, no research needed |
| **Medium** | `#email`, `#reply`, `#prep` | Some context or research, short investigation, multi-step reply |
| **High** | `#research` | Deep investigation, multi-product, internal search or parallel research |

Override based on context: a `#reply` to a simple confirmation is Low; a `#prep` for a complex multi-product call is High.

## Section structure inside action-items.md

```markdown
# Action Items — <Merchant>

## Open

- [ ] #email — ...
- [ ] #research — ...

## Completed

- [x] #log — ... — Completed: 2026-05-01
```

The `validate-action-items.sh` hook checks every saved file for missing tags, missing due dates on open items, and reminds about Asana dual-write when completions are present.
