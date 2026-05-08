---
name: specialist-prompt
description: >-
  Generates a self-contained specialist investigation prompt at
  projects/active/<slug>/drafts/specialist-<topic>-YYYY-MM-DD.md using the
  canonical template proven across multiple complex investigations
  architecture. Use when the user says "draft a specialist prompt", "build a
  Tier 3 brief", "I need to escalate to a specialist on [merchant]", or after a
  /stripe-jarvis Tier 3 returns a hypothesis that needs internal-tool validation
  the parent can't do.
---

# Specialist Prompt Generator

The same prompt structure has been hand-rebuilt at least four times
(e.g. `<merchant-slug>/drafts/specialist-investigation-prompt.md`,
`…/specialist-price-investigation-prompt.md`,
`…/specialist-currency-lock-investigation-prompt.md` — 296 lines,
`<merchant-slug>/drafts/specialist-architecture-prompt.md`). Each took
30+ minutes of prose-writing. This skill turns it into a 1-shot generation.

## Inputs

Ask the user (or accept inline) for:

1. **`slug`** — kebab-case merchant slug (must match `projects/active/<slug>/`).
2. **`topic`** — short kebab-case topic for the filename (e.g. `currency-lock-investigation`,
   `tipping-architecture`, `connect-onboarding-rejection`).
3. **`trigger`** — 1-2 sentence description of what just happened that prompted this
   (e.g. "<merchant contact>'s price-correction script hit `customer.currency` lock").
4. **`hypothesis_path`** — optional path to a prior Jarvis or specialist report whose
   claims need validation (e.g. `issues/currency-lock-remediation-2026-05-05.md`).
   If absent, the prompt is exploratory and the Claims section asks the specialist to
   produce the hypothesis.
5. **`claims_to_validate`** — 5-12 specific claims (CL1..CLn). Each needs: `claim` (one
   sentence), `verify_via` (Sourcegraph paths, doc URLs, internal channels), `why_it_matters`
   (what the recommendation hinges on if this is wrong).
6. **`diagnostics`** — 3-6 required diagnostics (D1..Dn) the specialist must run (e.g.
   warehouse queries, audit script population counts, sample API responses).
7. **`primary_deliverable`** — what the specialist must hand back. Default for execution
   work is "step-by-step execution guide". For architecture work it's "validated architecture
   recommendation with surface-by-surface playbook".
8. **`constraints`** — hard constraints (e.g. "no customer comms", "12-day-old P0",
   "specialist needs warehouse ACL", "Toolshed unauthorized this session").

## Workflow

### Step 1 — Read merchant context

Read in parallel from `projects/active/<slug>/`:
- `PROJECT.md` (account ID, products, status, AONR, key contacts, Stripe contacts)
- Most recent 1-3 entries from `timeline.md` (for the trigger framing)
- The `hypothesis_path` file if provided
- Any existing `drafts/specialist-*.md` files for this merchant (precedent + don't duplicate)

### Step 2 — Build the file at the canonical path

Write to `projects/active/<slug>/drafts/specialist-<topic>-YYYY-MM-DD.md`. Use the
template below with all sections filled. Aim for 200-300 lines — the proven length range.

### Step 3 — Append to specialist-runs.json (register)

Append (or create) `projects/active/<slug>/specialist-runs.json`:

```json
{
  "runs": [
    {
      "date": "YYYY-MM-DD",
      "topic": "<topic-kebab>",
      "phase": "<optional, e.g. '2B'>",
      "prompt_path": "drafts/specialist-<topic>-YYYY-MM-DD.md",
      "hypothesis_path": "<issues/...md if any>",
      "claims_count": N,
      "diagnostics_count": N,
      "primary_deliverable": "<execution_guide | architecture_recommendation | validation_only>",
      "trigger": "<one-sentence>",
      "status": "drafted",
      "agent_id": null,
      "output_path": null,
      "outcome": null
    }
  ]
}
```

If the file doesn't exist yet, create with `{"runs": []}` and append. Persist `agent_id`,
`output_path`, and `outcome` later (see "Updating after the specialist returns" below).

### Step 4 — Surface to user for review

Present:
- The file path
- A 5-bullet summary of what the prompt asks for
- The CL count, D count, and Deliverable count
- A single confirmation question: "Ready to drop into the specialist's context window, or
  any framing changes needed first?"

Do NOT auto-invoke the specialist. The user decides whether to drop into `/stripe-jarvis`,
launch a separate Task subagent, or hand to a human SA.

### Updating after the specialist returns

When the user pastes back / forwards the specialist's output (or after `/stripe-jarvis`
writes to `projects/active/<slug>/issues/jarvis-<topic>-YYYY-MM-DD.md`), update the
matching entry in `specialist-runs.json`:

- `status`: `"completed"` (or `"failed"` if the specialist couldn't produce useful output)
- `agent_id`: subagent / agent invocation ID, when available (e.g. `b26da3bc-35c5-...`)
- `output_path`: relative path to the output file
- `outcome`: 1-line summary (e.g. "8 of 10 claims validated; Fork 3 confirmed; CF engagement open")

Use this register before commissioning a NEW specialist run on the same topic — search by
`topic` substring to avoid duplicate work. Complex projects can accumulate 5+ specialist passes; a register
prevents re-asking already-validated claims.

## Canonical template (use verbatim, fill placeholders)

````markdown
# Specialist Investigation Prompt — <Merchant Display Name> <Topic Title> [(Phase N)]

> Self-contained brief for a Stripe <product-area> specialist agent. <One sentence on why
> this engagement now: prior research caveat, escalation reason, P0 status, etc.> Drop
> into the specialist's context window unmodified.

---

## TL;DR

<2-4 sentences. The literal merchant question (verbatim quote if available), what we
already know from prior work, what we need from this specialist (validate claims +
deliver execution guide / architecture recommendation).>

---

## Account & Project Context

- **Stripe account**: `acct_xxx` (<Merchant>, <country>, <Standard/Express/Direct>, <vertical>)
- **Account Manifest**: <admin URL>
- **Migration / integration ticket**: <Jira ID + URL if applicable>
- **Phase 1/2/2A status (if applicable)**: <one line per prior phase: ROOT CAUSE confirmed by
  <specialist> on <date>, what's gated, what's unblocked>

### Primary contacts
- **<Merchant lead>** — role — email (<what they own in the engagement>)
- **<Merchant tech>** — role — email (<what they own>)
- **<AE>** — Stripe (notified via Slack thread <thread ref>)
- **[YOUR_NAME]** — Stripe Accelerate IC

---

## Trigger for this investigation

<Verbatim quote of the merchant's question / Slack message / error response, with full
context — channel, message ID, request_log_url. Then 1-2 sentences on why this triggers
specialist engagement vs Tier 1/2 self-serve.>

---

## What we already know (don't re-establish)

<Bulleted list of established facts the specialist should NOT re-investigate. This is the
"don't waste tokens" section. Include cites to prior reports.>

- <Fact 1> — confirmed by <prior specialist or Jarvis report> on <date>
- <Fact 2>
- ...

---

## Working hypothesis from prior research (caveat: <if any>)

<If a Jarvis or earlier specialist report exists, include the headline recommendation here
in <table | numbered list> form. Note any caveats (e.g. "internal search was unauthorized",
"based on 6-day-old data").>

Full report: `<hypothesis_path>`.

### <Recommendation summary table OR plan>

| Fork / Surface | Pattern | Recommended path |
|---|---|---|
| 1 | <pattern> | <path> |
| ... | | |

---

## Claims to validate (with concrete sources to check)

For each claim: VALIDATE / REFUTE / REFINE using `pay-server` Sourcegraph (with file:line
citations), Trailhead internal docs, team Slack tribal knowledge, and any precedent.

### CL1 — <One-line claim title>
**Claim**: <Full claim, 1-3 sentences. Specific and testable.>
**Verify via**: <Concrete sources — Sourcegraph paths, doc URLs, internal Slack channels,
team contacts. Be specific about file paths when possible.>
**Why it matters**: <What recommendation hinges on this. What flips if it's wrong.>

### CL2 — <Title>
**Claim**: ...
**Verify via**: ...
**Why it matters**: ...

<repeat for CL3..CLn — usually 5-12 claims total>

---

## Required diagnostics (specialist with warehouse access OR via merchant-side API)

These need <warehouse table ACL / merchant-side API access / specific tool>. <Note any
known access gaps — e.g. "[YOUR_NAME] cannot run Hubble billing queries; merchant-side via
<contact> is acceptable fallback.">

### D1 — <Diagnostic title>
<What to run, what input data is needed, what output buckets / counts to produce, what
spot-checks confirm correctness.>

### D2 — <Title>
<...>

<repeat D3..Dn — usually 3-6 diagnostics total>

---

## Required deliverables

### Deliverable 1 — Validation report
For each of CL1–CLn above: VALIDATED / REFUTED / REFINED, with citations (Sourcegraph
file:line, Trailhead URL, Confluence page, internal Slack thread, team confirmation).

### Deliverable 2 — Confirmed root cause + recovery framing (data-backed)
<What the merchant + DM Ops / Stripe team need to act on. Specifically: confirmed answer
to the merchant's literal question, with confirmed populations from D1.>

### Deliverable 3 — <Primary deliverable: step-by-step execution guide / architecture recommendation> (THE MAIN ASK)

<For execution work, require:>
#### Format requirements
- **Step number, title, owner** ([YOUR_NAME] / Merchant lead / Stripe team / DM Ops)
- **Preconditions**
- **Action** with exact API calls, admin URLs, or Sourcegraph code paths
- **Expected output**
- **Error handling** (retry / rollback / escalate)
- **Edge cases**
- **Rollback / abort criteria**
- **Verification step**

#### Required content
1. <Step 1 — content brief>
2. <Step 2>
...

### Deliverable 4 — Risk register (extending <prior R-numbers>)
Top 5–10 risks specific to this phase, with mitigation. Suggested starting points:
- <Risk 1>
- <Risk 2>
...

### Deliverable 5 — Escalation routing (named)
For each blocker in the execution guide, name the team(s) and channel(s):
- <Surface 1>: <team / channel / on-call rotation>
- <Surface 2>: ...

### Deliverable 6 — Sources list
Sourcegraph links (file:line where relevant), Trailhead URLs, Confluence pages, Jira
tickets, Slack threads consulted.

---

## Constraints / context

- **<Hard constraint 1>**: <description>. <Why it forecloses certain paths.>
- **<Hard constraint 2>**: ...
- **<Speed / urgency context>**: <how old is the issue, AONR stake, escalation level>
- **<Access constraint>**: <what [YOUR_NAME] / current session cannot do>

---

## Reference artifacts

- Project workspace: `projects/active/<slug>/`
- <Prior specialist report 1>: `projects/active/<slug>/issues/<file>.md`
- <Prior specialist report 2>: ...
- <Working hypothesis (this prompt's basis)>: `<hypothesis_path>`
- Comms history: `projects/active/<slug>/raw/comms.md`
- <Today's trigger email/Slack>: <Gmail Message-ID / Slack thread ref / permalink>
- <Coordination thread>: <permalink>
- <Jira ticket>: <URL>

End of brief.
````

## Hard rules

- **Always self-contained.** The specialist's context window starts empty. Every fact the
  specialist needs must be in the prompt — no "see PROJECT.md for details" without including
  the relevant excerpt.
- **Verbatim merchant quote** in the Trigger section. Paraphrasing loses signal.
- **Claims are specific and testable.** "X works correctly" is not a claim. "X validates by
  comparing `customer.currency` against the new Price's `currency` and rejects on mismatch
  with `invalid_request_error` referencing `items[0][price_data]`" is a claim.
- **Each claim has Why It Matters.** The specialist needs to know what flips if the claim
  is wrong, so they can prioritize verification effort.
- **Deliverable 3 is the main ask.** Be explicit: "THE MAIN ASK" callout matters.
- **Reference Phase N specialists by name** when prior reports exist (e.g. "<specialist name> validated in
  Phase 2 claim C5" — don't re-litigate validated facts).
- **Save to drafts/, not issues/.** Issues files are for the specialist's *output*. The
  prompt is a draft asking for that output.
- **Never auto-invoke the specialist.** The user routes (Jarvis Tier 3, Task subagent,
  human SA), not this skill.

## Anti-patterns (don't do these)

- Asking 20+ claims. Specialists drown. 5-12 is the proven sweet spot.
- Treating the trigger as the whole prompt. The trigger is one section.
- Omitting "What we already know" — leads to wasted re-investigation.
- Burying the primary deliverable in a footnote. Lead with it under "THE MAIN ASK".
- Generic "do an analysis". Always force structure: format requirements + required content
  list under Deliverable 3.
