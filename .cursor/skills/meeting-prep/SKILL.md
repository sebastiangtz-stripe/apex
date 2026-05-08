---
name: meeting-prep
description: >-
  Prepares a structured briefing document before merchant meetings. Use when the
  user says "prep me for [merchant]", "get ready for my call", or when a
  calendar event with a merchant is within 2 hours.
---

# Meeting Prep

Generates a pre-meeting briefing from Asana + local files.

## Workflow

### Step 1: Identify the Meeting
Match merchant name to `projects/active/<slug>/`

### Step 2: Gather Context (launch all in parallel)

Launch these as parallel reads/agents — they are fully independent:

| Agent | Source | What to get |
|-------|--------|-------------|
| A (local) | `PROJECT.md` + `timeline.md` + `issues/` | Status, products, AONR, contacts, last 5 timeline entries, open issues |
| B (Asana) | `asana.json` + Asana API | Open subtasks (action items), task status, custom fields |
| C (comms) | `raw/comms.md` | Last 3 communications for conversation context |
| D (fresh) | Gmail + Slack (only if last scan >4hrs) | Recent messages not yet logged |

### Step 3: Build Prep Document

Save to `projects/active/<slug>/drafts/prep-YYYY-MM-DD.md`:

```
# Meeting Prep — [Merchant] — YYYY-MM-DD
## Meeting Info (time, attendees, type)
## Project Status (products, AONR, activation progress)
## Open Action Items (from Asana subtasks)
## Recent Activity (from timeline)
## Key Discussion Points (synthesized)
## Talking Points (concrete questions/updates)
```

### Step 4: Present
30-second verbal summary: status, top 2-3 things to address, risks.

### After the Meeting
1. Save transcript to `raw/comms.md`
2. Extract action items as Asana subtasks + local backup
3. Update Asana task notes if status changed

## Hard rules

- **Always parallelize Steps A–D**. Sequential reads waste minutes when the call is in 5.
- **Apply Product Brand Disambiguation** (CLAUDE.md General Rules) — if the meeting attendees mention a brand instead of the slug, resolve before reading.
- **Skip fresh fetches on weekends or when last scan was <4 hours ago**. The cached state is good enough; the merchant rarely emails during a meeting.
- **Never auto-send the prep doc**. It's a draft for the user's eyes only.
