---
name: recall
description: >-
  Searches data/lessons-learned/ by topic, tag, product, or merchant for prior
  patterns. Use BEFORE commissioning new research when the question is "have we
  seen this pattern before". Use when the user says "have we seen this before",
  "any prior art on X", "recall billing migration patterns", "/recall <topic>".
---

# Recall

Cheap retrieval against `data/lessons-learned/`. Returns the small set of relevant lessons
+ their full text (small files, ~200 lines each), so the agent can synthesize without
re-doing the original research.

## Inputs

The user's request, parsed into one or more of:
- **`query`** (free text): natural language question (e.g. "billing migration patterns")
- **`tags`**: explicit tags from the vocabulary (e.g. `[migration, multi-currency]`)
- **`product`**: a product area (`billing`, `connect`, etc.)
- **`slug`**: a specific past merchant

## Workflow

### Step 1 — Read the index

```
data/lessons-learned/INDEX.md
```

If missing or empty, return: "No lessons indexed yet. Use `/lessons-extract <slug>` to
build the corpus." Don't fall back to grep — the index is the source of truth.

### Step 2 — Filter

Apply filters in this order:
1. If `slug` given: load only `data/lessons-learned/<slug>.md` (and `pattern-*.md` files
   listed in its `related_lessons`).
2. If `tags` given: filter index lines whose `tags: [...]` intersects with requested tags
   (require ≥1 overlap).
3. If `product` given: filter index lines whose `products: [...]` contains the product.
4. If `query` given (and no other filter narrowed): grep the index TL;DR column AND grep
   the full lesson files for query terms (case-insensitive, stopword-stripped).

If filtering returns >5 candidates, narrow further by asking the user a tight question
("3 candidates: [<slug-a>], [<slug-b>], [pattern-some-topic]. Which?").

### Step 3 — Read the matched lessons

Read the 1-5 selected lesson files. Each is small (<200 lines).

### Step 4 — Synthesize

Return a single-block synthesis:

```
## Recall — <query>

### Most relevant: <lesson title>
<3-5 sentence synthesis pulled from "What worked" + "Patterns to reuse">

**Reuse pattern**: <name> — <2-3 lines of how to apply it now>
**Avoid**: <pattern> — <why>

### Also relevant: <lesson title>
<2-3 sentence synthesis>

### Drill-down sources
- `data/lessons-learned/<file>.md`
- `projects/active/<slug>/issues/<file>.md` (the citation-backed deep dive)
- `projects/active/<slug>/specialist-runs.json` for the full specialist register
```

### Step 5 — Suggest next step

Offer one concrete follow-up:
- If the lesson clearly answers the user's current question: "Want me to apply this pattern
  to <current-merchant>? I can draft the email / specialist prompt / playbook now."
- If the lesson is partial: "This covers part of it. Want me to commission a `/specialist-prompt`
  to fill the gaps with current data?"
- If multiple patterns matched: "Want me to combine these into a single playbook for
  <current-merchant>?"

## Hard rules

- **Always read the index first.** It's the cheapest way to filter.
- **Don't grep `projects/active/*/issues/`** as a fallback. That's specialist-runs.json
  territory — reachable from the lessons via `related_specialist_runs`.
- **Cite paths in every synthesis.** Recall outputs that don't link back to the source
  files are unverifiable.
- **Empty result is a valid result.** "No lessons matched" is more useful than
  hallucinating a pattern.

## Related

- `/lessons-extract` — populates the corpus at archive time (or on demand)
- `/specialist-prompt` — when a `/recall` returns "partial" / "no match", this is the
  next step before doing fresh research
- `/stripe-jarvis` — when even a specialist prompt is overkill (Tier 1/2 questions)
