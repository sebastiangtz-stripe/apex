---
name: email-agent
description: >-
  Drafts, reviews, sends, and logs merchant emails with full context gathering.
  Use when the user asks to email a merchant, draft a reply, follow up, send an
  intro email, or when an action item has #email or #reply tag.
---

# Email Agent

End-to-end email workflow: context -> draft -> review -> send -> log -> follow-up.

## Workflow

### Step 1: Gather Context

Read in parallel:
1. `projects/active/<slug>/PROJECT.md` — products, status, contacts
2. `projects/active/<slug>/raw/comms.md` — last 3-5 entries for tone
3. `projects/active/<slug>/asana.json` — task GID for later
4. Asana subtasks for the triggering action item

### Step 2: Select Template

Choose from `templates/emails/`: intro, follow-up, check-in, technical-answer, escalation.

### Step 3: Draft

Save to `projects/active/<slug>/drafts/<topic>.md` with Context, Research, Sources, Draft, Sent sections.

### Step 4: Present for Review

Show draft to the user. Ask: "Ready to send, or changes needed?"

### Step 5: Send

After approval, use Gmail MCP to send. If unavailable, instruct manual send.

### Step 6: Log

1. Update draft's `## Sent` section
2. Append full email to `raw/comms.md`
3. Add timeline entry to `timeline.md`
4. Complete the Asana subtask that triggered this email (read `asana.json` for GID)
5. Mark local `action-items.md` item as `[x]`

### Step 7: Follow-up

If response expected, create new Asana subtask:
- Name: `#waiting #email — [Merchant]: awaiting reply to [topic]`
- Due: +5 business days
Also write to local `action-items.md`.

## Hard rules

- **Never send without explicit approval.** Always present the draft (Step 4) and wait for confirmation. No auto-send even if the action item explicitly says "send".
- **Always dual-write the close.** Step 6 must complete BOTH local `action-items.md` `[x]` and Asana subtask `completed: true`. Skipping either side breaks the dual-write protocol.
- **Always populate `## Sent`** in the draft file with date + recipients + alias used. The stale-draft sweeper depends on this section being present to know the draft is closed.
- **Apply Product Brand Disambiguation** (CLAUDE.md General Rules) when the slug is ambiguous before reading anything else — wrong-merchant drafts are worse than late drafts.
