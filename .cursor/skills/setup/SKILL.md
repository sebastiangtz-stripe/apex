---
name: setup
description: >-
  Guided first-time workspace setup. Accepts an intake snippet (Slack handle,
  Asana PAT, two board URLs) pasted alongside /setup, auto-resolves identity
  via Home lookup + machine timezone, auto-discovers all Asana GIDs, and runs
  a smoke test. Use when the user says "/setup", "set up", "set me up",
  "onboarding", "onboard me", "first time", "initialize", "I'm new here", or
  when the auto-startup detects an unconfigured `.env`.
---

# Setup

Onboarding lives in one place. Read [`SETUP.md`](SETUP.md) for the manual
fallback — this skill is the agent-led path that does most of the work for
the user.

The skill parses an intake snippet, resolves identity from the Slack handle
via Home, auto-detects timezone, calls Asana's REST API to discover all
section + custom-field + option GIDs, writes `.env` atomically, and runs a
smoke test. The user only provides 4 values in one paste.

## Intake Snippet Format

```
Slack: <handle>
Asana PAT: <token>
Main board: <url>
Action Items board: <url>
```

The workspace owner pre-fills the handle and board URLs; the user only
generates and pastes their Asana PAT.

## When to invoke

- User says `/setup`, "set me up", "onboard me", "I'm new here", "first time
  setup", "initialize the workspace".
- Auto-startup detects a missing `.env`, or any `REPLACE` token inside the
  existing `.env` — see the fresh-workspace branch in
  [`CLAUDE.md`](CLAUDE.md).
- User explicitly asks to re-run setup or rotate credentials.

## Workflow

Run the phases in order. Phases 0–5 must all succeed before announcing the
workspace ready. Phase 6 is optional.

### Phase 0 — Detect state + parse snippet

Read `.env`. Decide:

- File missing → fresh install, run all phases.
- File exists, contains any value equal to `REPLACE` or starting with
  `REPLACE_WITH` → resume setup at the first unconfigured key.
- File exists, all values populated → use `AskQuestion` to confirm
  "your `.env` looks already configured — re-run setup anyway?" with options
  `Yes, re-run everything` / `No, exit` / `Rotate one specific key`.

**Parse the user's message for the intake snippet.** Look for lines matching:
- `Slack:` — Slack handle (no leading `@`)
- `Asana PAT:` — the token value
- `Main board:` — full Asana URL
- `Action Items board:` — full Asana URL

Matching is case-insensitive on the label, and tolerant of extra whitespace.

**If all 4 fields are present** → proceed directly to Phase 1 with no
questions.

**If some fields are present but others missing** → accept what's there,
tell the user which are missing, and ask them to provide only the missing
values.

**If no snippet detected** → surface this prompt and wait:

> Fill in this snippet and paste it back (your setup guide has the details):
>
> ```
> Slack: 
> Asana PAT: 
> Main board: 
> Action Items board: 
> ```

Tell the user one sentence about what you found in `.env` before moving on.

### Phase 1 — Auto-resolve identity (no user interaction)

From the parsed snippet, resolve everything automatically:

1. **Timezone** — auto-detect from the machine:
   ```
   readlink /etc/localtime | sed 's|.*/zoneinfo/||'
   ```
   Do NOT ask the user — always read from the system.

2. **Full name** — look up the Slack handle via Home internal search
   (`execute_internal_search` with `filter_types: ["person"]`). Extract the
   `title` field from the result.
   - **ASCII-normalize before writing**: Home returns display names with
     diacritics (e.g. "Sebastián Gutiérrez") but Hubble stores names in ASCII
     (e.g. "Sebastian Gutierrez"). Strip accents via
     `from scripts.lib import ascii_normalize` (or inline:
     `unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode()`)
     before writing to `HUBBLE_LEAD_FILTER`.
   - This becomes `HUBBLE_LEAD_FILTER`.
   - If the lookup fails (no result, MCP error), fall back to asking: "What's
     your full name as it appears in Hubble / the Stripe directory?"

3. **Email** — derive as `<handle>@stripe.com, accelerate@stripe.com`. Write
   to `MY_OUTBOUND_ADDRESSES`.

4. **Slack handle** — from snippet. Write to `SLACK_HANDLE`.

5. **Consultant username** — write `CONSULTANT_USERNAME=<handle>` (same value
   as SLACK_HANDLE). The CS query template (`templates/cs-incremental.sql`)
   appends `@stripe.com` itself and uses exact-match on `assignee_email`.
   Note: If the user's Slack handle differs from their Stripe LDAP username
   (rare — mainly contractor accounts), they should override
   CONSULTANT_USERNAME with the LDAP handle instead.

6. **Handover channel** — hardcoded for AMER. Write
   `HANDOVER_CHANNEL_ID=C02HZETBG75` automatically.

6. **Workspace GID** — if the board URL uses the new format
   (`/1/<workspace>/project/<project>/list/...`), extract from the URL. If
   old format, let the discovery script resolve it from the PAT.

Write all values to `.env` immediately using the atomic merge logic.

After writing, surface a one-line confirmation:
*"Identity resolved: <Name>, <timezone>, <handle>@stripe.com"*

### Phase 2 — Asana credentials (from snippet, no questions)

- Write `ASANA_PAT` from the snippet to `.env` immediately.
- Parse both board URLs to extract project GIDs.
  - New format: `https://app.asana.com/1/<workspace>/project/<project_gid>/list/...`
  - Old format: `https://app.asana.com/0/<project_gid>/list`
- If a URL fails to parse, ask the user to re-paste just that URL (don't
  reject everything else).

**Don't echo the PAT back to chat.** After writing, confirm with the literal
string `PAT stored` — no fingerprint, no first/last characters, no length.

### Phase 3 — Auto-discover

Run:

```
python3 scripts/setup-discover-asana.py \
  --pat-from-env \
  --main <MAIN_GID> \
  --ai   <AI_GID>   \
  --write .env
```

Handle the exit codes:

- `0` → discovery clean. Tell the user how many keys were written (one line).
- `1` → auth failure or malformed URL. Surface stderr verbatim, ask the user
  to either regenerate the PAT or re-paste the board URL, then re-run.
- `2` → one or more sections / fields / enum options are missing in Asana.
  Surface the full stderr block verbatim — it lists exactly what's missing
  and what was found. Ask the user to fix the board in Asana, then re-run
  Phase 3 by saying "discover again" (or `/setup` from Phase 2).

Do **not** retry automatically. Discovery failures almost always mean the
Asana board doesn't match the canonical structure documented in `SETUP.md`,
and a silent retry just hides the problem.

### Phase 4 — Hubble

- Confirm `HUBBLE_LEAD_FILTER` was set in Phase 1 — if blank, ask for full
  name again.
- Assume Hubble access is available. Do NOT ask the user whether they have
  access. Just confirm: *"Hubble pre-configured with local query template
  (`templates/hubble-query.sql`), filtered by your name."*
- If Hubble fails at runtime (not during setup), the `/hubble-analyst`
  subagent surfaces the error — no preemptive questions needed here.

### Phase 5 — Smoke test

Run two checks back-to-back:

1. `python3 scripts/test-subagents.py` — must exit 0. If it fails, surface
   the failing files; this is a workspace problem, not a user problem.
2. `python3 scripts/asana-reconcile.py --dry-run` — confirms the discovered
   GIDs resolve against live Asana. No writes. If it errors with 401, the
   PAT is wrong and we restart Phase 2. If it errors with 403/404, one of
   the board GIDs is wrong and we restart Phase 2.

On both green, present a single-line summary plus the counts (skills
validated, Asana objects resolved, Hubble status).

### Phase 5.5 — Write update-config.json

Generate the de-genericization config from values already resolved in Phases 1–2.
This enables `scripts/update-from-apex.py` to de-genericize template content when
pulling updates from apex.

Write to `data/update-config.json`:

```json
{
  "substitutions": {
    "[YOUR_NAME]": "<full_name from Home lookup>",
    "[YOUR_INITIALS]": "<initials derived from name>",
    "[YOUR_TIMEZONE]": "<timezone from system date>",
    "[YOUR_BOARD_NAME]": "<board name from Asana project>",
    "your.name@stripe.com": "<SLACK_HANDLE>@stripe.com",
    "<your_first_name_lowercased>": "<first_name.lower()>"
  }
}
```

If the file already exists, overwrite it (setup re-run refreshes all values).

### Phase 6 — Scaffold projects

**First, remove the bundled examples (silent).** Once the smoke test has passed,
run `python3 scripts/remove-example-projects.py`. The template ships two demo
merchants (`example-merchant`, `acme-corp`) so a fresh clone has something to
look at; `example-merchant` is also the Phase-5 smoke-test fixture, so this MUST
run *after* the smoke test, never before. The script deletes only those two
folders and regenerates INDEX.md — no confirmation, no output beyond the normal
success message below.

Then present the success message (see Output below), and ask:

*"Want me to scan Hubble for your projects and start scaffolding them?"*

Options: `Yes` / `Not now`

- **Yes** — invoke `/hubble-analyst`. Then scaffold projects in this exact order:
  1. Run `python3 scripts/scaffold-from-hubble.py --apply` to create all folders + PROJECT.md with Email search and Key Contacts pre-filled from Hubble contacts.
  2. Run `python3 scripts/hubble-reconcile.py --backfill` to populate External Links, AONR, dates, and any remaining contact fields.
  3. **Backfill handovers via the `handover-bootstrap` skill (backfill mode) — not the read-only `/handover-scanner` relay.** The skill runs the full deterministic pipeline (wide channel read → `handover-parse.py` → `handover-match.py --coverage` → one batched `python3 scripts/handover-create.py --proposals-stdin --update-existing`), which merges contacts, the Handover permalink + Manifest URL, and sets `scan_source: core` on every matched project. **Step 3 is complete only when `handover-create.py` has actually run** and the coverage matcher has reported its `covered` / `missing` split — do **not** treat a subagent's natural-language summary as completion. Projects in `missing` (no clean signal in the channel) legitimately keep `Handover: TBD`; that is expected, not a failure.
  4. **Verify before syncing:** confirm step 3 wrote the links — `grep -L "Handover: TBD" projects/active/*/PROJECT.md` should now list the `covered` projects, and the remaining `TBD` set should match the coverage report's `missing`. Only proceed once this lines up.
  5. Run `python3 scripts/sync-to-asana.py` to create Asana tasks — **only after** steps 1-4 so descriptions are fully populated. If tasks were already created in an earlier or interrupted pass, re-run with `--resync` — it now updates existing tasks **in place** (patches notes/links/fields) rather than creating duplicates.
  Confirm each batch before proceeding (don't run all steps silently).
- **Not now** — exit cleanly.

## Output

On successful setup, present exactly this:

```
Setup complete. Your workspace is ready.
```

Then immediately ask whether to scan Hubble and scaffold projects (Phase 6).

## Hard rules

- **Always persist the Asana PAT to `.env`.** The user types it once during
  setup and never again. Every downstream script reads it from `.env` via
  `--pat-from-env` or direct env load. Do not invent alternative storage
  (keychain, per-session prompt, env-var-only). macOS filesystem permissions
  + `.gitignore` are the security model.
- **Never echo the PAT back to chat.** After collection, the only
  acknowledgement is the literal string `PAT stored`. No fingerprints, no
  first/last characters, no length, no logging. This is a chat-display rule
  to keep screenshots of the conversation safe — it does not affect storage.
- **Never write secrets anywhere except `.env`**. `.env` is in `.gitignore`
  and the sync-template leak scan would reject it. Do not paste the PAT into
  a comment, a session log, a draft, or a script flag that would land in
  shell history.
- **Atomic `.env` writes only.** Use the merge logic in
  [`scripts/setup-discover-asana.py`](../../../scripts/setup-discover-asana.py)
  (`write_env`) or call the script with `--write .env` — never `cat >` the
  file or use a non-atomic editor. Partial writes during interruption corrupt
  every downstream tool.
- **Fail fast on auth errors.** Any Asana 401 aborts the current phase
  immediately. Do not retry, do not assume a transient. Tell the user the PAT
  was rejected and re-collect.
- **Discovery failures are user-visible.** When
  `setup-discover-asana.py` exits 2, surface its full stderr block verbatim
  — it names the exact missing piece. Do not paraphrase, do not "try
  again", do not silently substitute defaults.
- **Don't proxy Hubble.** The `/hubble-analyst` subagent owns all Hubble MCP
  calls. This skill only writes `.env`; it never invokes `run_hubble_query`.
- **Re-runnable.** Every phase must be safe to invoke twice. The atomic
  `.env` merge replaces keys in place, so re-running setup never duplicates
  lines or loses unrelated values.
- **Never commit `.env`.** If the user asks "should I commit this?", say no
  — `.env` is gitignored on purpose and the sync-template leak scan would
  reject it.
- **Home lookup is best-effort.** If the internal search fails or returns no
  person result for the handle, fall back to asking the user for their full
  name. Do not block the entire setup on a Home MCP failure.
