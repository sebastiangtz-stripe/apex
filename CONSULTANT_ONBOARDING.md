# Apex Consultant Onboarding

Use this as the one-stop checklist for getting a new consultant ready to use
Apex. It covers the access you need, the Asana board copies, Toolshed
authorization, the Asana Personal Access Token, and the first smoke test.

For deeper technical setup or troubleshooting, use [`SETUP.md`](SETUP.md).

## 10-Minute Map

```text
Access ready
    |
    v
Copy 2 Asana boards
    |
    v
Create Asana PAT
    |
    v
Authorize Toolshed bundles
    |
    v
Run /setup in Cursor
    |
    v
Say "what's on the board?"
```

## Before You Start

Have these ready before opening Cursor:

| Item | Why it matters | Where to get it |
|---|---|---|
| Apex repo access | Lets you clone the workspace template | Repo owner |
| Asana access | Needed for merchant tasks and action items | Asana workspace admin or manager |
| Two Asana boards (pre-created) | One board tracks merchants; one board tracks action items | Workspace owner creates these for you |
| Toolshed auth | Lets Apex read Gmail, Slack, Hubble, Calendar, Asana, and internal docs | `go/toolshed-auth` |
| Hubble access | Pulls roster, AONR, due dates, AE, SFDC, and Kantata links | Stripe access request |
| Your intake snippet | Pre-filled by the workspace owner with your handle + board URLs | Setup guide / workspace owner |

## Step 1: Copy The Asana Boards

Apex expects two Asana projects:

```text
Main Merchant Board
  one task per merchant

Action Items Board
  one subtask per follow-up, multi-homed from merchant tasks
```

Ask the workspace owner for the two template board links, then duplicate each
project in Asana.

### Main Merchant Board

Keep these sections exactly:

| Section | Meaning |
|---|---|
| `Received` | New handovers or projects not yet triaged |
| `[GREEN]` | Healthy active projects |
| `[YELLOW]` | Projects needing attention |
| `Completed` | Finished projects before archive cleanup |
| `Terminated` | Projects that ended early |

Required custom field:

| Field | Type | Options |
|---|---|---|
| `Active on Accelerate?` | Single-select | `YES`, `NO` |

### Action Items Board

Keep these sections exactly:

| Section | Meaning |
|---|---|
| `Today` | Needs action today |
| `This Week` | Needs action this week |
| `Later` | Tracked, but not urgent |
| `Waiting` | Blocked on merchant or internal owner |

Required custom fields:

| Field | Type | Options |
|---|---|---|
| `Merchant` | Single-select | Leave empty; Apex adds merchants |
| `Tag` | Single-select | `email`, `reply`, `research`, `prep`, `schedule`, `track`, `log`, `waiting` |
| `Complexity` | Single-select | `LOW`, `MEDIUM`, `HIGH` |

Copy the URLs for both boards. `/setup` will ask for them later.

## Step 2: Create Your Asana PAT

1. Open `https://app.asana.com/0/my-apps`.
2. Click **Create new token**.
3. Name it `Apex local workspace`.
4. Copy the token once.
5. Do not paste it into Slack, docs, Asana comments, session logs, or commits.

Visual check:

```text
Asana My apps
  -> Personal access tokens
      -> Create new token
          -> Copy token
              -> Paste only into /setup
```

Apex stores the token in `.env`, which is gitignored. If a PAT is rejected,
generate a fresh one and rerun `/setup`.

## Step 3: Authorize Toolshed

Open `go/toolshed-auth` and authorize the tool bundles Apex uses.

| Tool | Enables |
|---|---|
| Asana | Read and write merchant tasks, subtasks, comments, custom fields |
| Gmail | Scan merchant email threads and deduplicate logged comms |
| Slack | Read merchant channels and handover threads |
| Slack public | Search public internal channels when needed |
| Hubble | Refresh roster and project metadata |
| Calendar | Prepare for meetings and scheduling workflows |
| Zoom | Meeting-context workflows where available |
| Sourcegraph admin | Technical research through `stripe-jarvis` |
| Starter pack | Shared baseline internal search tools |

The local MCP config starts these bundles for Cursor:

```text
asana.gmail.hubble.slack.slack_public.starter_pack
zoom
sourcegraph.admin
```

If a scan says a tool is unauthorized, return to `go/toolshed-auth`, authorize
the missing bundle, then restart Cursor.

## Step 4: Run Apex Setup

From the repo root:

```bash
cp .env.example .env
cursor .
```

In Cursor, paste `/setup` followed by your intake snippet in a single
message:

```text
/setup
Slack: yourhandle
Asana PAT: <paste your token from Step 2>
Main board: https://app.asana.com/1/.../project/.../list/...
Action Items board: https://app.asana.com/1/.../project/.../list/...
```

Your workspace owner will provide the snippet pre-filled with your Slack
handle and both board URLs. You only need to add your Asana PAT.

The agent automatically resolves your full name (via Home), email, timezone
(from your machine), and handover channel — no additional questions.

Expected success message:

```text
workspace configured
test-subagents OK
asana-reconcile --dry-run OK
```

## Step 5: Verify The Workspace

Say this in Cursor:

```text
what's on the board?
```

You should see a short startup summary with Asana sync status, active project
counts, Hubble status, and any overdue or upcoming work.

Then try one of these:

| Command | What it tests |
|---|---|
| `catch me up` | Asana reconcile, Hubble refresh, Slack/Gmail scan |
| `scan for new handovers` | Slack handover channel access |
| `prep my next meeting` | Calendar plus project context |
| `show my action items` | Local action item rollup |

## What Good Looks Like

```text
Asana
  Main board has one task per merchant
  Action Items board has subtasks grouped by urgency

Local workspace
  projects/active/<merchant>/PROJECT.md stores context
  action-items.md mirrors Asana subtasks
  raw/comms.md stores logged email and Slack history

Cursor
  /setup works once
  "what's on the board?" works every day
  "catch me up" scans and proposes updates
```

## Common Problems

| Symptom | Fix |
|---|---|
| `/setup` says the PAT was rejected | Generate a new PAT at `app.asana.com/0/my-apps` |
| `/setup` says a section or field is missing | Compare your copied boards with the tables above, fix the name, rerun setup |
| Gmail or Slack scans fail auth | Re-authorize the bundle at `go/toolshed-auth`, then restart Cursor |
| Hubble returns no projects | Confirm `HUBBLE_LEAD_FILTER` matches your name in Hubble |
| `what's on the board?` says workspace not configured | `.env` still has `REPLACE` values; rerun `/setup` |
| New action item appears only locally or only in Asana | Run `python3 scripts/asana-reconcile.py` |

## Security Rules

- Never commit `.env`.
- Never paste an Asana PAT into a doc, Slack message, Asana task, or session log.
- Never add real merchant data to this template repo.
- Before publishing template improvements, run the template sync flow in
  [`data/runbooks/template-sync.md`](data/runbooks/template-sync.md).

## Owner Handoff Checklist

Use this list when onboarding another consultant:

- [ ] Confirm they can access the Apex repo.
- [ ] Create their two Asana boards (Main + Action Items) with canonical structure.
- [ ] Prepare their intake snippet (pre-fill their Slack handle + both board URLs).
- [ ] Share the intake snippet with them.
- [ ] Confirm they created an Asana PAT.
- [ ] Confirm they authorized Toolshed at `go/toolshed-auth`.
- [ ] Watch them run `/setup` with the snippet pasted.
- [ ] Watch them run `what's on the board?`.
- [ ] Confirm no secrets or merchant data were committed.
