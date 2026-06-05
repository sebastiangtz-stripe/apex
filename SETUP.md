# Setup

If you are onboarding a consultant, start with
[`CONSULTANT_ONBOARDING.md`](CONSULTANT_ONBOARDING.md). This file is the
technical setup reference and manual fallback.

The fastest path from a fresh clone to a working assistant is the agent-led
`/setup` skill. Manual instructions are kept below as a fallback in case the
skill breaks, you prefer to do it by hand, or you're configuring something
the skill doesn't yet cover.

## Quick start (agent-led, ~3 min)

```bash
git clone <your-fork-url> accelerate-pm-assistant
cd accelerate-pm-assistant
cp .env.example .env
cursor .
```

In the Cursor chat, paste `/setup` with your intake snippet in one message:

```
/setup
Slack: yourhandle
Asana PAT: <your token>
Main board: <url from workspace owner>
Action Items board: <url from workspace owner>
```

The assistant will:

1. Parse the snippet and resolve your full name from Home (via Slack handle).
2. Auto-detect your timezone from the machine.
3. Derive your email (`<handle>@stripe.com, accelerate@stripe.com`).
4. Write the PAT and extract project GIDs from the board URLs.
5. Call the Asana REST API to auto-discover all section + custom-field +
   enum-option GIDs, and write them to `.env` for you.
6. Confirm the Hubble saved query (`stripe/c5619e62`) is pre-configured.
7. Run a smoke test (`test-subagents.py` + `asana-reconcile.py --dry-run`).
8. Optionally help you create your first merchant project.

No interactive questions unless something fails. When the skill says
*"workspace configured"* you're ready to say *"what's on the board?"* and
exercise the full auto-startup.

## Prerequisites

- **Cursor** installed (the assistant runs as a Cursor agent — `.cursor/`
  contains all the agents/skills/rules/hooks).
- **Python 3.9+** (scripts use stdlib only — no pip deps).
- **Git**.
- An **Asana account** with permission to create a project + custom fields.
- Optional: **Hubble** access for SFDC + Kantata roster sync. The shared
  saved-query UUID is pre-filled in `.env.example`, so you don't need to
  create your own.
- **Toolshed MCP** (pre-configured in `.cursor/mcp.json`): The workspace
  ships with MCP server definitions that auto-start when you open in Cursor.
  Before they work, you must authorize each tool bundle once at
  `go/toolshed-auth` (Gmail, Slack, Calendar, Hubble, Asana). Without
  authorization, the scan-review skill and merchant-scanner subagent will
  not be able to fetch email or Slack — but everything else (Asana sync,
  drift audits, action-items rollup, INDEX regeneration, lessons / recall)
  still works.

## Asana board structure (one-time setup)

`/setup` assumes the two Asana boards already exist with the canonical
section + field names below. Most teammates have these created for them by
the workspace owner. If you're creating them yourself, follow these
specifications exactly — the auto-discovery script matches on these names.

### Main board (one task per merchant)

Sections (in order):

- `Received`
- `[GREEN]`
- `[YELLOW]`
- `Completed`
- `Terminated`

Custom fields **on the project** (not the workspace):

| Field name | Type | Options |
|---|---|---|
| `Active on Accelerate?` | Single-select | `YES`, `NO` |

### Action Items cross-project

Sections (in order):

- `Today`
- `This Week`
- `Later`
- `Waiting`

Custom fields **on this project**:

| Field name | Type | Options |
|---|---|---|
| `Merchant` | Single-select | leave empty — `sync-to-asana.py` adds options as merchants are created |
| `Tag` | Single-select | `email`, `reply`, `research`, `prep`, `schedule`, `track`, `log`, `waiting` |
| `Complexity` | Single-select | `LOW`, `MEDIUM`, `HIGH` |

### Personal Access Token

Generate one at `app.asana.com/0/my-apps`. `/setup` will ask you to paste it.

---

## Manual fallback

If the agent-led path doesn't work for you, you can fill `.env` by hand.

### 1. Clone and configure secrets [3 min]

```bash
git clone <your-fork-url> accelerate-pm-assistant
cd accelerate-pm-assistant
cp .env.example .env
```

Open `.env` and fill `MY_OUTBOUND_ADDRESSES`. The Asana / Hubble values come
from the steps below.

### 2. Set up the Asana board [8 min]

Create the two projects with the structure documented in the *Asana board
structure* section above. Then grab the GIDs:

- Project GID: visible in the URL — `app.asana.com/0/<PROJECT_GID>/list`.
- Section GIDs: `GET https://app.asana.com/api/1.0/projects/<PROJECT_GID>/sections`.
- Custom field GIDs + option GIDs: `GET https://app.asana.com/api/1.0/projects/<PROJECT_GID>/custom_field_settings`.

Paste them into `.env` under `ASANA_PROJECT_GID`, `ASANA_SECTION_*`,
`ASANA_FIELD_ACTIVE*`, and the `ASANA_AI_*` keys for the Action Items
board. Generate a PAT at `app.asana.com/0/my-apps` and paste into
`ASANA_PAT`.

Or — far easier — once you've put the PAT and the two board URLs in
`.env`, run:

```bash
python3 scripts/setup-discover-asana.py \
  --pat-from-env \
  --main https://app.asana.com/0/<MAIN_GID>/list \
  --ai   https://app.asana.com/0/<AI_GID>/list \
  --write .env
```

This is the same script `/setup` calls in Phase 3.

### 3. Smoke-test the contract validators [1 min]

```bash
python3 scripts/test-subagents.py
```

Expected: `25+ agents/skills/rules validated, 0 failed`. If anything fails,
the template was edited mid-flight — `git status` should show what's off.

### 4. (Optional) Hubble [2 min]

The shared Accelerate saved-query UUID is pre-filled in `.env.example`. You
only need to set `HUBBLE_LEAD_FILTER` to your full name as it appears in
`project_lead_user_name`.

Test:

```bash
python3 scripts/hubble-reconcile.py
```

If you don't have Hubble access, leave `HUBBLE_LEAD_FILTER` as the
placeholder; the `hubble-analyst` subagent will skip gracefully.

### 5. Create your first project [3 min]

Two ways:

**A. Manually:** copy the example scaffold:

```bash
cp -R projects/active/example-merchant projects/active/my-first-merchant
# edit projects/active/my-first-merchant/PROJECT.md
python3 scripts/sync-to-asana.py --slug my-first-merchant
```

**B. From a Hubble row** (if step 4 is wired): just say to Cursor
*"new project: <merchant name>, <acct_id>"* and the conversational
mappings in `CLAUDE.md` will create the folder + Asana task.

### 6. Open in Cursor [1 min]

`cursor .` from the repo root. The assistant auto-loads:

- Top-level `CLAUDE.md` (always-applied conversational context).
- `.cursor/rules/*.mdc` (always-applied or glob-scoped rules).
- `.cursor/agents/*.md` (subagents, invoked with `/<name>` or by description).
- `.cursor/skills/*/SKILL.md` (skills, auto-discovered by description).
- `.cursor/hooks.json` (file-shape validators run after edits).

First message to try: **"What's on the board?"** — exercises the
auto-startup protocol, the `index-reconciler` skill, and your `.env` Asana
wiring all in one shot.

## Customizing

- **Conventions live in `.cursor/rules/`** — adjust action-items tag
  vocabulary, email tone, or scan rules without touching agent prompts.
- **Workflows live in `.cursor/skills/`** — each skill is a self-contained
  markdown file with front-matter + instructions. Add your own as new
  recurring patterns emerge.
- **Heavy readers live in `.cursor/agents/`** — add subagents when an
  operation produces a lot of intermediate output (raw email bodies, internal
  search results, large JSON) that you don't want to bloat the main thread.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `/setup` says "missing section X" or "missing custom field Y" | The Asana board doesn't have a section/field with the canonical name. Add it in Asana (see *Asana board structure* above) and re-run `/setup` Phase 3. |
| `/setup` says "PAT rejected (401)" | The Asana PAT is wrong, expired, or empty. Regenerate at `app.asana.com/0/my-apps` and re-run setup. |
| `test-subagents.py` reports "missing Hard rules" | A skill or agent file was edited without preserving the `## Hard rules` section. Add it back. |
| `sync-to-asana.py` 401s | `ASANA_PAT` expired or empty. Regenerate at `app.asana.com/0/my-apps`. |
| `hubble-reconcile.py` returns no rows | `HUBBLE_LEAD_FILTER` doesn't match `project_lead_user_name` casing. The script lowercases both sides — confirm your name appears in the saved-query result set. |
| INDEX.md says "0 active" but you have folders | `python3 scripts/regenerate-index.py` rebuilds it from the filesystem. |
| Cursor doesn't pick up a new skill | Confirm the skill has `description:` front-matter. Skills are auto-discovered by description text. |
| Auto-startup says "Workspace not configured" | Your `.env` has at least one `REPLACE` value left. Run `/setup` to finish, or edit `.env` by hand. |

## Updating from upstream

This template will continue to evolve. To pull updates without losing your
local merchant data:

```bash
git fetch upstream
git merge upstream/main
```

Real merchant data lives under `projects/active/` (excluding
`example-merchant/`), `projects/archive/`, `sessions/[date].md`,
`data/lessons-learned/*.md`, and `data/hubble-snapshot.json` — all of
which are in `.gitignore` and never tracked. Conflicts should only ever
appear in template files (CLAUDE.md, skills, agents, scripts).
