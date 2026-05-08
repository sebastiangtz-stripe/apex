---
name: action-items
description: >-
  Global rollup of action items across all active merchants — filter by tag,
  due window, overdue status, age, complexity, and group by merchant / tag /
  due date. Use when the user says "show me all #waiting", "what's due this
  week", "what's #research", "what's overdue", "what's untouched 5+ days", or
  any global question about open action items that would otherwise require
  reading 30+ files.
---

# Action Items Rollup

Reads every `projects/active/<slug>/action-items.md`, parses each `- [ ]` /  `- [x]` line into a structured record (tags, due, complexity, source date, owner), and lets you filter + group across the whole portfolio without 30 file reads.

## Workflow

```
python3 scripts/list-actions.py [flags]
```

### Common queries

| User asks | Command |
|---|---|
| "Show me everything `#waiting`" | `--tag waiting --group-by merchant` |
| "What's due this week" | `--due-window 7 --group-by due` |
| "What's overdue" | `--overdue --group-by due` |
| "What `#research` is untouched for 5+ days" | `--tag research --untouched-days 5` |
| "All `#email` items, by merchant" | `--tag email --group-by merchant` |
| "All H-complexity items" | `--complexity H --group-by merchant` |
| "Just one merchant" | `--slug example-merchant` |
| "Include closed items" | `--include-closed` (use sparingly — large volume) |

Pipe with `--json` for machine-readable output if you need to compose with other skills.

### Group-by modes

- `merchant` (default): one section per slug, items sorted by due date ascending.
- `tag`: one section per `#tag`, items sorted by due → slug.
- `due`: bucketed `Overdue / Today / This Week / This Month / Later / No Due`.

## What gets parsed from each line

Per CLAUDE.md format `- [ ] #tag — Description — Complexity: X — Owner: who — Due: date — Source: ref`:

- All `#tag` occurrences → `tags`
- `Due: <YYYY-MM-DD>` or `Due: TBD/ASAP` → `due_date`
- `Complexity: <H|M|L>` → `complexity`
- `Source: <YYYY-MM-DD>/...` → `source_date` (used for `--untouched-days`)
- `Owner: <name>` → `owner`

Lines that don't match `^- \[[xX ]\]` are ignored. The Open / Completed section split is honored unless `--include-closed` is set.

## When to invoke

Trigger on any of: "what's due", "what's overdue", "show me all <tag>", "weekly review", "what should I work on this morning", "#waiting list", "what's untouched", "what's #research that's been sitting".

## After running

For lists >20 items, present:
1. The total count
2. Top 5 most urgent (by Due date), with `[slug]` prefix
3. Then offer: "Want the full list, or filtered further (by tag / merchant / complexity)?"

For lists <=20, show in full.

If the user is preparing for a focus block (e.g. "prep me for tomorrow"), suggest combining with `/index-reconciler` (for current OVERDUE flags) and the most-recent session log.

## Hard rules

- **Read-only.** Never modifies action-items.md or Asana. The dual-write rule still applies for new items / completions — those go through normal flows.
- **Asana is the authority for completion state.** This script reads local files only; if local is stale, run `python3 scripts/asana-reconcile.py` first to sync.
- **Don't dump everything by default.** When the result is huge (>30 items) and the user hasn't filtered, ask which filter they want.
