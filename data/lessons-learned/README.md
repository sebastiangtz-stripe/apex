# Lessons Learned

Per-merchant + per-pattern knowledge accumulation. Extracted at project archive time
by the `/lessons-extract` skill, retrieved on demand by the `/recall` skill.

## File naming

- Per-merchant lessons: `<slug>.md`
- Cross-cutting patterns: `pattern-<topic-kebab>.md` (e.g. `pattern-billing-migration.md` example)

## File format

Each lesson is a markdown file with structured front-matter:

```markdown
---
slug: <merchant-slug or null>
date_archived: YYYY-MM-DD
tags: [billing, migrations, multi-currency, default-pm]
products: [Payments, Billing, Invoicing]
related_lessons: [pattern-billing-migration.md, <other-merchant-slug>.md]
related_specialist_runs:
  - projects/active/<slug>/issues/specialist-report-phase2-<date>.md
---

# <Merchant> — Lessons Learned

## What worked
- <bullet>
- <bullet>

## What didn't
- <bullet>

## Patterns to reuse
- **<pattern name>**: <2-3 sentences. Reference the canonical exemplar (issue file, code snippet, or prior project).

## Patterns to AVOID
- **<pattern name>**: <what failed and why>

## Key technical references
- <Sourcegraph path / Trailhead URL / Confluence page>
- <Stripe doc URL>

## Next time, do this differently
- <bullet>
```

## Why these exist

Real example: a complex migration pattern recall was once done from memory by [YOUR_NAME]. Pattern-tagged lessons let `/recall billing migration patterns` surface the same recall in 1 query, without depending on the agent's session memory.

## Tags vocabulary (extend freely)

Common tags so `/recall` can do exact-match retrieval:

- Products: `billing`, `connect`, `payments`, `terminal`, `tax`, `radar`, `checkout`, `invoicing`, `metronome`
- Patterns: `migration`, `multi-currency`, `default-pm`, `paid-out-of-band`, `subscription-schedule`, `tipping`, `overcapture`, `customer-currency-lock`, `pm-portability`, `account-currency-lock`, `pause-collection`, `void-invoice`
- Process: `escalation`, `customer-foundation`, `dm-ops`, `migrations-team`, `professional-services`, `specialist-handoff`
- Business: `no-comms-constraint`, `consent-risk`, `grandfathered-pricing`
