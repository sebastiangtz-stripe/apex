# Setup

Walkthrough for getting this template running on a fresh machine. ~15-20 minutes
end-to-end. Estimated time per step in brackets.

## Prerequisites

- **Cursor** installed (the assistant runs as a Cursor agent — `.cursor/`
  contains all the agents/skills/rules/hooks).
- **Python 3.9+** (scripts use stdlib only — no pip deps).
- **Git**.
- An **Asana account** with permission to create a project + custom fields.
- Optional: **Hubble** access for SFDC + Kantata roster sync.
- Optional: **Toolshed MCP** wired into Cursor for Gmail / Slack / Calendar
  access. Without it, the scan-review skill and merchant-scanner subagent will
  not be able to fetch email or Slack — but everything else (Asana sync, drift
  audits, action-items rollup, INDEX regeneration, lessons / recall) still
  works.

## 1. Clone and configure secrets [3 min]

```bash
git clone <your-fork-url> accelerate-pm-assistant
cd accelerate-pm-assistant
cp .env.example .env
```

Open `.env` and fill in `MY_OUTBOUND_ADDRESSES` for now. The Asana / Hubble
values come from steps 2 and 4.

## 2. Set up the Asana board [8 min]

You need **two** Asana projects: a main board (one task per merchant) and an
Action Items cross-project (one subtask per open action, multi-homed from the
main board).

### Main board

Create a new Asana project named anything you like (e.g. `Stripe Accelerate —
<your initials>`). Add these sections in order:

- `Received`
- `[GREEN]`
- `[YELLOW]`
- `Completed`
- `Terminated`

Then add these custom fields **on the project** (not the workspace):

| Field name | Type | Options |
|---|---|---|
| `Active on Accelerate?` | Single-select | `YES`, `NO` |
| Other domain fields you want (Status, AONR, etc.) | per your preference | |

Grab the GIDs:

- Project GID: visible in the URL — `app.asana.com/0/<PROJECT_GID>/list`.
- Section GIDs: `GET https://app.asana.com/api/1.0/projects/<PROJECT_GID>/sections`.
- Custom field GIDs + option GIDs: `GET https://app.asana.com/api/1.0/projects/<PROJECT_GID>/custom_field_settings`.

Paste them into `.env` under `ASANA_PROJECT_GID` and `ASANA_SECTION_*` and
`ASANA_FIELD_ACTIVE*`.

### Action Items cross-project

Create a second Asana project named `Action Items` (or similar). Add sections:

- `Today`
- `This Week`
- `Later`
- `Waiting`

And custom fields **on this project**:

| Field name | Type | Options |
|---|---|---|
| `Merchant` | Single-select | leave empty for now — `sync-to-asana.py` adds options as merchants are created |
| `Tag` | Single-select | `email`, `reply`, `research`, `prep`, `schedule`, `track`, `log`, `waiting` |
| `Complexity` | Single-select | `LOW`, `MEDIUM`, `HIGH` |

Grab the GIDs the same way and fill in the `ASANA_AI_*` keys in `.env`.

### Personal Access Token

Generate at `app.asana.com/0/my-apps`. Paste into `ASANA_PAT`.

## 3. Smoke-test the contract validators [1 min]

```bash
python3 scripts/test-subagents.py
```

Expected: `✓ 25 agents/skills/rules validated, 0 failed`. If anything fails,
the template was edited mid-flight — `git status` should show what's off.

## 4. (Optional) Hubble [4 min]

Hubble is the source of truth for the merchant roster, AONR, account
executive, dates, SFDC + Kantata URLs, segment, and Accelerate type. Without
Hubble, you maintain `PROJECT.md` fields manually and the
`hubble-analyst` subagent skips gracefully.

If you have Hubble access, save a query that returns the columns
`scripts/hubble-reconcile.py` expects (see the script's docstring) and put
the saved-query UUID into `HUBBLE_SAVED_QUERY_ID` and your name into
`HUBBLE_LEAD_FILTER`.

Test:

```bash
python3 scripts/hubble-reconcile.py
```

## 5. Create your first project [3 min]

Two ways:

**A. Manually:** copy the example scaffold:

```bash
cp -R projects/active/example-merchant projects/active/my-first-merchant
# edit projects/active/my-first-merchant/PROJECT.md
python3 scripts/sync-to-asana.py --slug my-first-merchant
```

**B. From a Hubble row** (if step 4 is wired): just say to Cursor
*"new project: <merchant name>, <acct_id>"* and the conversational mappings
in `CLAUDE.md` will create the folder + Asana task.

## 6. Open in Cursor [1 min]

`cursor .` from the repo root. The assistant auto-loads:

- Top-level `CLAUDE.md` (always-applied conversational context).
- `.cursor/rules/*.mdc` (always-applied or glob-scoped rules).
- `.cursor/agents/*.md` (subagents, invoked with `/<name>` or by description).
- `.cursor/skills/*/SKILL.md` (skills, auto-discovered by description).
- `.cursor/hooks.json` (file-shape validators run after edits).

First message to try: **"What's on the board?"** — exercises the auto-startup
protocol, the `index-reconciler` skill, and your `.env` Asana wiring all in
one shot.

## Customizing

- **Conventions live in `.cursor/rules/`** — adjust action-items tag vocabulary,
  email tone, or scan rules without touching agent prompts.
- **Workflows live in `.cursor/skills/`** — each skill is a self-contained
  markdown file with front-matter + instructions. Add your own as new
  recurring patterns emerge.
- **Heavy readers live in `.cursor/agents/`** — add subagents when an
  operation produces a lot of intermediate output (raw email bodies, internal
  search results, large JSON) that you don't want to bloat the main thread.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `test-subagents.py` reports "missing Hard rules" | A skill or agent file was edited without preserving the `## Hard rules` section. Add it back. |
| `sync-to-asana.py` 401s | `ASANA_PAT` expired or empty. Regenerate at `app.asana.com/0/my-apps`. |
| `hubble-reconcile.py` returns no rows | `HUBBLE_LEAD_FILTER` doesn't match `project_lead_user_name` casing. The script lowercases both sides — confirm your name appears in the saved-query result set. |
| INDEX.md says "0 active" but you have folders | `python3 scripts/regenerate-index.py` rebuilds it from the filesystem. |
| Cursor doesn't pick up a new skill | Confirm the skill has `description:` front-matter. Skills are auto-discovered by description text. |

## Updating from upstream

This template will continue to evolve. To pull updates without losing your
local merchant data:

```bash
git fetch upstream
git merge upstream/main
```

Real merchant data lives under `projects/active/` (excluding
`example-merchant/`), `projects/archive/`, `sessions/[date].md`,
`data/lessons-learned/*.md`, and `data/hubble-snapshot.json` — all of which
are in `.gitignore` and never tracked. Conflicts should only ever appear in
template files (CLAUDE.md, skills, agents, scripts).
