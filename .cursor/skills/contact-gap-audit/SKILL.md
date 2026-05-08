---
name: contact-gap-audit
description: >-
  For each merchant, scans raw/comms.md for any From/To address NOT covered by
  the project's PROJECT.md `Email search` query. Surfaces historical contacts
  that the merchant-scanner's inline contact-discovery rule (added later) never
  had a chance to backfill. Read-only — surfaces gaps for human triage. Use when
  the user says "audit contacts", "find missing contacts", "check email queries",
  or after a slug merge.
---

# Contact Gap Audit

The `/merchant-scanner` adds new contacts to PROJECT.md inline as it logs new comms.
But comms accumulated *before* that rule shipped (or in projects whose `Email search`
field is still TBD) are unaudited. This skill backfills the historical sweep.

## Workflow

```
python3 scripts/contact-gap-audit.py
```

Optional flags:
- `--slug <slug>` — restrict to one merchant
- `--json` — machine-readable
- `--min-count N` — only show gaps with at least N occurrences (filter out one-off ccs)

## Coverage rule (matches CLAUDE.md Email Query Format)

A contact is "covered" if any of:
1. **Domain match**: address is `*@<domain>` and `from:<domain>` is in the query (skip
   generic providers — `gmail.com`, `icloud.com`, `hotmail.com`, `outlook.com`,
   `yahoo.com`, etc.)
2. **Specific-address match**: full address appears as `from:<addr>` or `to:<addr>`
3. **Display-name match**: `from:"<name>"` covers any address sent from that display name

Stripe-internal addresses (`*@stripe.com`, `*@professionalservices.stripe.com`,
`*@*.stripe.com`) and `MY_OUTBOUND_ADDRESSES` are auto-skipped.

## Output structure (per gap)

```
- <email> "<display name>" × <count> (<reason>)
    first seen: <comms.md ## section> (line N)
```

Reasons:
- `domain not in query` — add `from:<domain> OR to:<domain>` to the query
- `generic provider — needs name+address line` — add `from:"<Display Name>" OR from:<addr> OR to:<addr>`

## When to invoke

- After a slug merge (the merged project's query may not cover both source domains)
- When a `/merchant-scanner` run reports "0 new emails" but the user remembers seeing recent
  comms
- Quarterly hygiene
- When the user says "audit contacts", "find missing contacts", "check email queries"

## After running

For each gap, propose the exact `Email search` query update and present for confirmation:

```
proposed for <slug>:
  from:example.com OR to:example.com OR from:"Jane Doe" OR from:jane.personal@example.com OR to:jane.personal@example.com
```

Apply with a `StrReplace` on `projects/active/<slug>/PROJECT.md` only after explicit user
confirmation. Each PROJECT.md update should also add the missing contact(s) to the
Key Contacts section per the workspace rule.

## Hard rules

- **Read-only by default.** The script never edits PROJECT.md. Edits are user-confirmed
  via the parent agent.
- **Never propose adding a generic provider domain** (`gmail.com`, etc.) to the query —
  always propose name + specific-address coverage instead.
- **Skip internal Stripe addresses.** They're not merchant contacts; `is_covered()`
  hard-codes the skip.
