---
name: weekly-metrics
description: >-
  Aggregates per-session Stats blocks across a date window into a single
  metrics rollup. Use when the user says "how did my week go", "weekly review",
  "weekly metrics", "monthly review", "show me throughput", or any quantitative
  question about session activity over time.
---

# Weekly Metrics

Until now, sessions/*.md `## Stats` blocks have been prose-only — answering "how many
items did I close last week" required reading 5+ files. This skill aggregates them.

## Workflow

```
python3 scripts/weekly-metrics.py [--days N] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--append-jsonl]
```

Defaults to a rolling 7-day window from today. Use `--days 30` for a monthly review.

`--append-jsonl` appends the rollup to `data/weekly-metrics.jsonl` so trend-over-time can
be plotted later (each line is one rollup window).

## What gets parsed

The parser is **tolerant**: each Stats line is `- <Label>: <text>` and the parser
extracts the *first integer* in `<text>`. Many lines have prose ("1 created (`<slug>`),
~10 updated") — the parser takes the first number. This under-counts in those cases but
never hallucinates.

Mapped labels (canonical keys):
- `projects_created`, `projects_updated`, `projects_touched`
- `emails_scanned`, `emails_logged`, `raw_entries`
- `items_created`, `items_completed` (also from combined `created/completed` lines)
- `asana_created`, `asana_completed`, `asana_updated`, `asana_updated_due`,
  `asana_custom_field_updates`, `asana_comments`
- `issues_opened`, `issues_resolved`, `drafts_created`, `drafts_revised`
- `subagent_invocations`, `slack_threads_logged`, `slack_messages_sent`, `slack_dms_sent`
- `script_patches`, `lints_introduced`, `hallucinations_owned`

Unmapped labels are silently skipped — extend `LABEL_MAP` in the script when new labels
appear in session writeups.

## When to invoke

- "Weekly review" / "how did my week go" / "what did I ship this week"
- "Monthly review" / "show me April"
- After 7+ days of no metrics rollup written (Sunday wrap-ups are a natural cadence)
- Whenever the user asks a comparative throughput question ("did I close more this week
  than last week")

## After running

Surface the rollup, then:
1. Highlight any **anomalies vs prior windows** (compare to previous JSONL line if available)
2. Suggest one of:
   - "Want a per-merchant breakdown for this window?"
   - "Want me to roll forward into a session/wrap-up?"
   - "Want to commit this to `data/weekly-metrics.jsonl` for trend-tracking? (`--append-jsonl`)"

## Hard rules

- **Tolerant by design.** Don't fix "missed" numbers by adding new regex — instead, agree
  on a tighter Stats line format with the user and update LABEL_MAP.
- **Never modify session files.** This is a read-only aggregator. Stats stays in prose;
  the rollup is derived.
- **Don't append to JSONL silently.** Only with `--append-jsonl`.
