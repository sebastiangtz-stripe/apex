# Runbook — Asana API Reference

PAT in `.env`. Endpoint base: `https://app.asana.com/api/1.0`.

## Common operations

| Operation | Method + path | Body |
|---|---|---|
| Create subtask | `POST /tasks/{parent_gid}/subtasks` | `{ name, due_on, notes }` — always include 1-2 sentence `notes` with context |
| Complete task/subtask | `PUT /tasks/{gid}` | `{ completed: true }` |
| Reopen | `PUT /tasks/{gid}` | `{ completed: false }` |
| Update due date | `PUT /tasks/{gid}` | `{ due_on: "YYYY-MM-DD" }` |
| Add comment | `POST /tasks/{gid}/stories` | `{ text }` |
| Move to section | `POST /sections/{section_gid}/addTask` | `{ task: gid }` |
| Multi-home a subtask | `POST /tasks/{subtask_gid}/addProject` | `{ project: AI_PROJECT_GID, section: <urgency_section_gid> }` |
| Set custom field on subtask | `PUT /tasks/{subtask_gid}` | `{ custom_fields: { <field_gid>: <value or option_gid> } }` |
| Set "Active on Accelerate?" to NO (archive) | `PUT /tasks/{gid}` | `{ custom_fields: { ASANA_FIELD_ACTIVE: ASANA_FIELD_ACTIVE_NO } }` |

## Subtask name convention

Plain natural-language action-verb description. NO `#tag` prefix. Tag lives in the
custom field. Examples:

- Good: `Send revised contract to ABC Co`
- Good: `Reply to Mike's currency question`
- Bad: `#email — Send revised contract` (tag belongs in field)

## JSON parsing gotcha

Asana API response bodies sometimes contain raw control characters that break
Python's `json.loads`. Workaround:

```python
import json, re
# Either:
json.loads(body, strict=False)
# Or extract via regex when strict=False still fails:
json.loads(re.sub(r'[\x00-\x1f]+', ' ', body))
```

This is a known parsing pitfall — be defensive when reading Asana JSON in scripts.

## Reconciliation

Run `python3 scripts/asana-reconcile.py` to sync both directions:
- Asana completions (e.g. items completed on mobile) → mark local `[x]`
- Local new items → create Asana subtasks
- Local completions → complete Asana subtasks

Use `--dry-run` to preview. Always run as Phase 1 of `/catchup` after a multi-day gap.

## Per-project mapping

Each project has `projects/active/<slug>/asana.json`:

```json
{
  "task_gid": "123456789",
  "project_gid": "...",
  "section": "...",
  "subtask_gids": { "action-item-key": "987654321" }
}
```

The `subtask_gids` map is the source of truth for completing the right subtask. Keep in
sync via the dual-write protocol — don't let local-only or Asana-only items accumulate.
