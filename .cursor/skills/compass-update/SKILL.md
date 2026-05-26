---
name: compass-update
description: >-
  Generate an Apex Project Compass update draft. Use when the user says
  "update compass", "compass update", "biweekly update", or "/compass-update".
  Pulls from apex git log + session logs + adoption memory to produce a
  ready-to-paste update in the established devblog-style format.
---

# Compass Update

Generates a draft update for the Apex Project Compass (home.corp.stripe.com/compass/projects/accelerate-apex). Pulls data from two sources, classifies into sections, and presents for review.

## Inputs

- **Period**: Date range to cover (default: last 14 days). Ask if ambiguous.
- **Focus/theme** (optional): e.g. "collaboration", "onboarding", "resilience". Weights the narrative framing but doesn't exclude other content.

## Data sources

Gather these in parallel:

1. **Apex git log**: `git -C ~/Documents/accelerate-apex-template log --since="<start-date>" --format="%h %an: %s" --all`
2. **Session logs**: Read `sessions/*.md` files whose dates fall within the period. Extract: Summary, Stats, Work Done, Workflow Changes sections.
3. **Adoption list**: Read memory file `project_apex_users.md` for current user count and names.

## Workflow

1. Ask the user for the period (default: last 14 days) and optional theme.
2. Gather data sources in parallel (git log, session logs, adoption list).
3. Classify each accomplishment into the categories below.
4. Draft the update in the output format below.
5. Present the draft for user review before posting.

## Classification

Sort each accomplishment (from commits + sessions) into one of three buckets:

- **New features**: Net-new capabilities that didn't exist before. New scripts, new workflows, new integrations.
- **Improvements**: Enhancements to existing functionality. Dedup fixes, parser robustness, backfill logic, performance.
- **Changes**: Infrastructure, monitoring, guardrails, process changes. Health checks, leak scans, audit tooling.

## Output format

```
**Update — <start> to <end>**

**Adoption: N out of 26 Accelerate consultants now running on apex.** <names onboarded this period>. <who's next>. <growth context sentence>.

---

**New features**

<Prose paragraphs, one per major feature. Hook, what it does, why it matters.>

**Improvements**

- <item>: <explanation>
- ...

**Changes**

- <item>: <explanation>
- ...

**Collaboration**

<One paragraph on contributor activity, commit counts by author, what the multi-author dynamic means strategically.>
```

## Hard rules

- **No em dashes.** Use colons, commas, semicolons, or restructure the sentence.
- **No markdown tables.** Compass doesn't render them.
- **Third person.** Strategic initiative update, not a personal blog.
- **Track people, not project counts.** Adoption = N/26 consultants. Never report merchant counts.
- **Period-scoped.** Only cover work since the last update. This is a log, not a rewrite.
- **Professional but personal tone.** Devblog-style adapted for internal Stripe. Not dramatic, not formulaic, not overly casual.
- **Do NOT auto-post.** Always present the draft for user review before they paste into Compass.
- **Update adoption memory** if new users onboarded since last saved state (edit `project_apex_users.md`).
