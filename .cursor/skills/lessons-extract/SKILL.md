---
name: lessons-extract
description: >-
  At project archive time, extract durable lessons (what worked, what didn't,
  patterns to reuse, patterns to avoid) into data/lessons-learned/<slug>.md
  with structured front-matter (tags, products, related_lessons,
  related_specialist_runs). Use when the user says "archive <merchant>",
  "extract lessons from <merchant>", or after a project is moved to
  projects/archive/.
---

# Lessons Extract

Captures the institutional knowledge from a closing project so future engagements can
recall it via `/recall`. Should run as part of every archival flow.

## When to invoke

- User says "archive <merchant>", "<merchant> is live", "extract lessons from <merchant>"
- After a project folder has just been moved to `projects/archive/<slug>/`
- Optionally on demand mid-project for projects with hard-won learnings (e.g. after
  Phase 2B clears, even before archive)

## Workflow

### Step 1 — Read project context

Read in parallel from the archived (or active) project:
- `PROJECT.md` — Products, Status, AONR, Key Contacts (for tag inference)
- `timeline.md` (full)
- `action-items.md` Completed section (the hard-won wins)
- All `issues/*.md` (the deepest learnings live here)
- `specialist-runs.json` (every specialist pass with outcome)
- All `drafts/*.md` with `## Sent` populated (the comms patterns that worked)
- The 3 most recent `sessions/*.md` files containing this slug (mentions in Stats / Work
  Done)

### Step 2 — Synthesize the structured lesson

Build `data/lessons-learned/<slug>.md` per the format documented in
`data/lessons-learned/README.md`. Critical fields:

- **`tags`**: pull from the README's tag vocabulary. Always tag at least:
  - 1-3 product tags (`billing`, `connect`, etc.)
  - 1-3 pattern tags (`migration`, `multi-currency`, `tipping`, etc.)
  - 1-2 process tags (`escalation`, `customer-foundation`, etc.)
- **`related_specialist_runs`**: paths to every issue file referenced by `specialist-runs.json`
  (these are the citation-backed sources future `/recall` invocations will pull from)

### Step 3 — Detect cross-cutting patterns worth extracting separately

Some lessons are merchant-specific (e.g. "<Merchant>'s CSV had grandfathered amounts"). Others
are pattern-level (e.g. "<Source>→Stripe Billing migration always needs default-PM backfill
because PAN export doesn't carry it").

When a pattern recurs across 2+ merchants OR is general enough to apply to any future
merchant, ALSO write `data/lessons-learned/pattern-<topic-kebab>.md` with the same format.
Reference back to the per-merchant lessons in `related_lessons`.

Existing patterns that should land as `pattern-*.md` files based on workspace history:
- `pattern-billing-migration.md` (e.g. 2+ merchants confirmed)
- `pattern-default-pm-import-gap.md`
- `pattern-customer-currency-lock.md`
- `pattern-cnp-tipping-overcapture.md`
- `pattern-paid-out-of-band-migration.md`

### Step 4 — Confirm before writing

Show the user a short preview (TL;DR + tag list + 1-line per "What worked" / "What didn't")
and ask: "Land this lesson + N pattern files? Or edit anything first?"

After confirmation, write the files.

### Step 5 — Update the lessons index

Append to `data/lessons-learned/INDEX.md` (create if missing) one line per lesson written:

```
- [<slug>](./` + slug + `.md) — <date> — products: [...], tags: [...] — <one-line TL;DR>
```

For pattern files, prefix `[pattern]`.

## Hard rules

- **Confirmation before write.** Lessons are durable institutional knowledge — no
  silent writes.
- **Always tag from the vocabulary.** Free-form tags fragment retrieval. Extend the
  vocabulary in the README first if a needed tag is missing.
- **Cite specialist runs.** Future `/recall` consumers need a path to drill from "1-line
  summary" to "the 12-step playbook the specialist actually wrote".
- **Cross-cutting patterns get their own file.** Don't bury a reusable pattern inside one
  merchant's lesson — extract it.
