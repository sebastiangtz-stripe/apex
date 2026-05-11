---
name: setup
description: >-
  Guided first-time workspace setup. Walks a teammate from a fresh clone to a
  working `.env` in one conversation: collects identity + Asana PAT + board
  URLs, auto-discovers all Asana GIDs via the REST API, defaults the shared
  Hubble query, and runs a smoke test. Use when the user says "/setup", "set
  up", "set me up", "onboarding", "onboard me", "first time", "initialize",
  "I'm new here", or when the auto-startup detects an unconfigured `.env`.
---

# Setup

Onboarding lives in one place. Read [`SETUP.md`](SETUP.md) for the manual
fallback — this skill is the agent-led path that does most of the work for
the user.

The skill collects identity + Asana credentials, calls Asana's REST API to
auto-discover the ~14 section + custom-field + option GIDs, writes `.env`
atomically, and runs a smoke test. The user only types a name, a token, and
two URLs.

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

### Phase 0 — Detect state

Read `.env`. Decide:

- File missing → fresh install, run all phases.
- File exists, contains any value equal to `REPLACE` or starting with
  `REPLACE_WITH` → resume setup at the first unconfigured key.
- File exists, all values populated → use `AskQuestion` to confirm
  "your `.env` looks already configured — re-run setup anyway?" with options
  `Yes, re-run everything` / `No, exit` / `Rotate one specific key`.

Tell the user what you found in one sentence before moving on.

### Phase 1 — Identity

Use `AskQuestion` to collect:

1. **Timezone** — single-select from these common Stripe TZs:
   `America/Los_Angeles`, `America/New_York`, `[YOUR_TIMEZONE]`,
   `Europe/Dublin`, `Europe/London`, `Asia/Singapore`, `Australia/Sydney`,
   `Other (I'll type it)`. If `Other`, ask a follow-up text question for the
   IANA TZ name.
2. **Full name** — free text via a normal chat-turn question
   ("What's your full name as it appears in Hubble / Stripe directory?"). This
   becomes `HUBBLE_LEAD_FILTER`.
3. **Email aliases** — free text ("What Stripe email aliases do you send
   merchant comms from? Comma-separated."). Becomes `MY_OUTBOUND_ADDRESSES`.

Write all three to `.env` immediately using the atomic merge logic below — do
not batch into a single end-of-phase write.

### Phase 2 — Asana credentials

Use `AskQuestion`:

- *"Have you generated an Asana Personal Access Token at
  app.asana.com/0/my-apps?"* — options `Yes, I have it ready` /
  `Not yet (open link)` / `Skip (I'll set up Asana later)`.

If `Skip`, jump to Phase 4 and leave Asana keys unset. If `Not yet`, surface
the link and wait. If `Yes`, ask the user to paste the PAT in their next
message.

**Persist the PAT.** Write it to `.env` immediately as `ASANA_PAT=<value>`.
This is the one and only time the user should ever have to type it — every
downstream script (`sync-to-asana.py`, `asana-reconcile.py`,
`setup-discover-asana.py --pat-from-env`) reads it from `.env`. macOS
filesystem permissions + `.gitignore` are the security layer; do not invent
a "more secure" alternative (no keychain prompts, no per-session re-entry,
no environment-variable-only mode). The user said "I will set this up once".

**Don't echo it back to chat.** After writing, confirm with the literal
string `PAT stored` — no fingerprint, no first/last characters, no length.
This is purely a chat-display rule so a screenshot of the conversation
doesn't expose the token; it has nothing to do with where the token is
stored.

Then ask, in one chat-turn message, for both board URLs:

> Paste the URL of your **main merchant board** and your **Action Items
> cross-project** (one per line). They look like
> `https://app.asana.com/0/<PROJECT_GID>/list`.

Parse both GIDs from the URLs. If parsing fails, ask the user to confirm or
paste the raw numeric GID.

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
  to either regenerate the PAT or re-paste the board URL, then re-run Phase 2.
- `2` → one or more sections / fields / enum options are missing in Asana.
  Surface the full stderr block verbatim — it lists exactly what's missing
  and what was found. Ask the user to fix the board in Asana, then re-run
  Phase 3 by saying "discover again" (or `/setup` from Phase 2).

Do **not** retry automatically. Discovery failures almost always mean the
Asana board doesn't match the canonical structure documented in `SETUP.md`,
and a silent retry just hides the problem.

### Phase 3.5 — Slack handover channel

Two short `AskQuestion` prompts:

1. *"What's your Slack handle (no leading `@`)?"* — free-text follow-up.
   Write to `.env` as `SLACK_HANDLE=<value>`. Used by `/handover-scanner` to
   detect when a handover thread tags the user.
2. *"What's the channel ID for `#ven-ext-stripe-accelerate-amer` (or your
   region's handover channel)?"* — single-select:
   - `I'll paste it now` → free-text follow-up, write to `.env` as
     `HANDOVER_CHANNEL_ID=<value>`.
   - `Look it up for me` → use the Slack MCP `slack_search` (or equivalent)
     to find the channel by name; surface the resolved ID and ask the user
     to confirm before writing.
   - `Skip for now` → leave as `REPLACE`; the handover scanner will no-op
     gracefully until the user fills it in later.

`HANDOVER_CHANNEL_ID_LEGACY` is already defaulted in `.env.example` and
doesn't need user input.

### Phase 4 — Hubble

The shared saved-query UUID is already defaulted in `.env.example`. This
phase only needs to:

- Confirm `HUBBLE_LEAD_FILTER` was set in Phase 1 — if blank, ask again.
- Tell the user the Hubble query is pre-filled and they don't need to do
  anything else for it.
- Use `AskQuestion` "Do you have Hubble MCP access wired in Cursor?" with
  options `Yes` / `No / not sure` / `Skip Hubble entirely`. If `No`, point
  them at the Toolshed MCP discovery flow. If `Skip`, leave the env as-is
  and tell them the `/hubble-analyst` subagent will no-op gracefully.

Do not attempt a Hubble query yourself — `/hubble-analyst` is the only
place that calls the MCP tool. Confirming `.env` is enough.

### Phase 5 — Smoke test

Run two checks back-to-back:

1. `python3 scripts/test-subagents.py` — must exit 0. If it fails, surface
   the failing files; this is a workspace problem, not a user problem.
2. `python3 scripts/asana-reconcile.py --dry-run` — confirms the discovered
   GIDs resolve against live Asana. No writes. If it errors with 401, the
   PAT is wrong and we restart Phase 2. If it errors with 403/404, one of
   the board GIDs is wrong and we restart Phase 2.

On both green, present a single-line "✓ workspace configured" summary plus
the counts (skills validated, Asana objects resolved, Hubble status).

### Phase 6 — First project (optional)

Use `AskQuestion`: *"Create your first merchant project now?"* with options
`Yes, from Hubble` / `Yes, from the example scaffold` /
`No, I'll do it later`.

- **From Hubble** — invoke `/hubble-analyst`. Surface its `new_projects` list
  and ask which one to scaffold. Hand off to the CLAUDE.md "new project"
  conversational mapping — do not reimplement creation here.
- **From scaffold** — ask for the merchant name and account ID, then follow
  the same CLAUDE.md "new project" flow.
- **Later** — exit cleanly with a one-liner pointing at the relevant
  CLAUDE.md section.

## Output

End of run, post one consolidated summary:

```
## Setup complete — <ISO date>

- Identity: <name>, <tz>, <N email aliases>
- Asana: workspace `<workspace_name>`, <N keys discovered>
- Hubble: pre-filled (lead filter: `<filter>`) | skipped
- Smoke tests: test-subagents OK, asana-reconcile --dry-run OK
- First project: <slug> created | deferred

You're ready. Say "what's on the board?" to exercise the full auto-startup.
```

## Hard rules

- **Always persist the Asana PAT to `.env`.** The user types it once during
  Phase 2 and never again. Every downstream script reads it from `.env` via
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
