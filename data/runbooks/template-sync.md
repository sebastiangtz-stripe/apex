# Runbook — Template Sync Protocol (apex)

**Status**: load-bearing. This workspace is the upstream of a peer-shared agent
template. Improvements made here are propagated to the GitHub repo
[`sebastiangtz-stripe/apex`](https://github.com/sebastiangtz-stripe/apex), from
which collaborators (currently [YOUR_NAME] + Diego) pull updates into their own
live workspaces.

## Architecture

```
┌──────────────────────────────────┐         ┌──────────────────────────────────┐
│  [YOUR_NAME]'s live workspace    │         │  Diego's live workspace          │
│  (this repo, with merchant data) │         │  (his repo, with merchant data)  │
└─────────────┬────────────────────┘         └────────────────┬─────────────────┘
              │ scripts/sync-template.py --push                │ scripts/sync-template.py --push
              │ (sanitize + push)                              │ (sanitize + push)
              ▼                                                 ▼
       ┌─────────────────────────────────────────────────────────────┐
       │  ~/Documents/accelerate-apex-template/                         │
       │  (no merchant data, fully sanitized)                         │
       │                                                              │
       │  origin = github.com/sebastiangtz-stripe/apex (private)      │
       └─────────────────────────────────────────────────────────────┘
                                    ▲
                                    │ git pull (manual, into local template clone,
                                    │  then re-apply changes in own live workspace)
```

## Roles

| Role | Responsibility |
|---|---|
| **[YOUR_NAME]'s live workspace** (this) | Primary author of agent / skill / rule / script changes. Runs `scripts/sync-template.py --push` after substantive improvements. |
| **Diego's live workspace** | Second author. Same sync responsibility on his side. Diego's workspace lives on his machine — never on this filesystem. |
| **`~/Documents/accelerate-apex-template/`** | Local sanitized mirror, single source of truth for what goes to GitHub. Always behind the latest live changes until a sync runs. |
| **`apex` (GitHub)** | Canonical shared template. Both authors pull here, both push here. PR review encouraged once both authors are active. |

## What is template-relevant (gets synced)

| Path | Synced? | Notes |
|---|---|---|
| `.cursor/agents/` | YES | All subagents |
| `.cursor/skills/` | YES | All skills |
| `.cursor/rules/` | YES | All `.mdc` rules |
| `.cursor/hooks/`, `.cursor/hooks.json`, `.cursor/settings.json` | YES | All validators + config |
| `scripts/*.py`, `scripts/*.sh` | YES | All automation, no `__pycache__` |
| `data/runbooks/` | YES | All runbooks (including this one) |
| `data/lessons-learned/README.md` | YES | README only — actual lessons stay local |
| `templates/emails/` | YES | Email templates |
| `CLAUDE.md` | YES | Top-level conversational context |
| `.env.example`, `.gitignore`, `README.md`, `SETUP.md` | YES | Top-level scaffolding |
| `projects/active/example-merchant/` | YES | Worked example only |
| `_inbox/.gitkeep`, `ideas/INDEX.md`, `sessions/INDEX.md` | YES | Empty scaffolds |
| **— never synced —** | | |
| `.env` | NO | Real secrets |
| `projects/active/<other slugs>/` | NO | Real merchant data |
| `projects/archive/` | NO | Archived merchant data |
| `sessions/[date].md` | NO | Daily logs may name merchants |
| `data/hubble-snapshot.json` | NO | SFDC data |
| `data/lessons-learned/*.md` (except README) | NO | Real lessons may name merchants |
| `.git/` | NO | Live workspace git history |

## Genericization rules (applied automatically by sync script)

These string substitutions run on every synced file. The list lives in
[`scripts/sync-template.py`](../../scripts/sync-template.py)
(`GENERICIZATION_RULES`). Add a new rule whenever a new identifying token
appears in template-relevant content.

| Source (live workspace) | Replacement (template) |
|---|---|
| `[YOUR_NAME]` / `[YOUR_NAME]` / `[YOUR_NAME]` (narrative) | `[YOUR_NAME]` |
| `sebastian` (lowercased, in regex examples) | `<your_first_name_lowercased>` |
| `your.name@stripe.com` | `your.name@stripe.com` |
| `Stripe Accelerate — SGG` / `Agentic test SGG` | `[YOUR_BOARD_NAME]` |
| `[YOUR_TIMEZONE]` | `[YOUR_TIMEZONE]` |
| `your local timezone` / `user's local TZ` | `your local timezone` / `user's local TZ` |
| Real merchant slugs (`rula`, `skift`, `infotech`, all 35) | `example-merchant` or abstracted (`<slug-a>`, `<merchant-slug>`) |
| Real merchant domains (`example.com`, etc.) | `example.com` (RFC 2606) / `example.org` |
| Real Asana GIDs (16-digit strings in `sync-to-asana.py`) | `"REPLACE"` placeholder |
| Real session-date references (`2026-04-30 sessions`) | abstracted (`a known parsing pitfall`, etc.) |

## Trigger criteria — when to sync

The sync should run when **any** of the following is true:

1. **A `.cursor/agents/`, `.cursor/skills/`, `.cursor/rules/`, or `.cursor/hooks/` file changed** since the last sync. These are the highest-leverage shared assets.
2. **A `scripts/*.py` script changed.** Even small fixes (argparse, error messages) help the peer.
3. **A `data/runbooks/*.md` runbook changed.**
4. **`CLAUDE.md`, `templates/emails/*`, or top-level scaffolding changed** in a way the peer should adopt.
5. **A new lesson is captured at `data/lessons-learned/pattern-*.md`** (cross-cutting patterns ship; merchant-specific lessons stay local).

The auto-startup summary (CLAUDE.md §Auto-Startup) surfaces a `Template drift`
indicator if `scripts/sync-template.py --check` reports template-relevant
changes since the last commit on the template branch.

## Procedure (prescriptive)

### Routine sync (most common)

```bash
# From this live workspace root
python3 scripts/sync-template.py --push --message "<one-line summary of changes>"
```

The script does, in order:

1. Verifies `~/Documents/accelerate-apex-template/` exists and is on `main` with no uncommitted changes.
2. Rsyncs template-relevant paths from the live workspace to the template directory (with the exclusions above).
3. Applies all `GENERICIZATION_RULES` to every changed file.
4. Runs the leak scan. **Fails hard** if any token from the deny list is found.
5. Runs `python3 scripts/test-subagents.py` inside the template. **Fails hard** if any contract is broken.
6. Stages and commits with the supplied `--message`.
7. Pushes to `origin/main` (apex).

If any step fails, the script aborts before any push. The local template
working tree may have partial changes — `git -C ~/Documents/accelerate-apex-template/ reset --hard origin/main` to clean up.

### Dry run (review before pushing)

```bash
python3 scripts/sync-template.py --dry-run
```

Shows the rsync diff, the genericization replacements that would apply, and
the would-be commit summary. Makes no changes.

### Local sync only (no push)

```bash
python3 scripts/sync-template.py
```

Sanitizes and commits to the local template repo without pushing. Useful when
you want to review the diff in the template directory before publishing. Push
later with `git -C ~/Documents/accelerate-apex-template/ push`.

### When the script can't be used

For one-off content that the script's rules don't cover (e.g. a brand-new
file pattern you haven't taught the script about yet), do the manual flow:

1. Add the file to the live workspace.
2. Add a generalization rule to `scripts/sync-template.py` (`GENERICIZATION_RULES`).
3. Add the file's path to the inclusion list (`SYNC_PATHS`) if it's not under an existing tracked directory.
4. Run the script.

This forces every sync path through the same lint/leak gates.

## Conflict resolution

Two-author repos drift quickly. Rules:

1. **Pull before sync.** Always `git -C ~/Documents/accelerate-apex-template/ pull --rebase` before running the sync. The script does this automatically when invoked with `--push`.
2. **Single source of truth per file.** A given skill / agent / runbook is owned by whoever last touched it; the other author should branch off `main` and open a PR rather than pushing directly.
3. **Open a PR for non-trivial changes.** Once both authors are actively pushing (>1 commit per author per week), switch from direct-to-main pushes to feature branches + PRs. Use `gh pr create` from the template directory.
4. **Resolve in the template, not in the live workspace.** Rebase / merge happen in `~/Documents/accelerate-apex-template/`. Once `main` is clean, both authors pull into their own live workspaces manually (each author decides what to incorporate where; live workspaces never auto-update from `main`).
5. **Lessons stay local.** Even when a `pattern-*.md` lesson is shared, only the abstracted pattern ships — never the merchant-specific lesson file.

## Pulling from apex into a live workspace

Both authors keep a clone of `apex` at `~/Documents/accelerate-apex-template/`. To
pick up the peer's improvements:

```bash
git -C ~/Documents/accelerate-apex-template/ pull --rebase
```

Then, in the live workspace, copy the changed template-relevant files in
manually. There's deliberately no automated reverse sync — live workspaces have
merchant data, and an automated apex → live merge is the easy way to clobber it.

A semi-automated helper is available:

```bash
python3 scripts/sync-template.py --pull --review
```

This shows a unified diff between `~/Documents/accelerate-apex-template/`
(after pulling) and the live workspace, file by file, and lets the user
accept / skip each change. It never modifies merchant data paths.

## Onboarding Diego

When Diego sets up his own live workspace from `apex`:

1. Clone `apex` → his own `~/Documents/accelerate-apex-template/` (his canonical mirror).
2. Copy that into a new live workspace at his preferred path (e.g. `~/Documents/Diego-Accelerate/`).
3. Replace `[YOUR_NAME]` / `[YOUR_INITIALS]` / `[YOUR_TIMEZONE]` / `[YOUR_BOARD_NAME]` placeholders with his own values in `CLAUDE.md` and templates.
4. Set up `.env` per `SETUP.md`.
5. When he authors improvements in his live workspace, he runs the same
   `scripts/sync-template.py --push` from his side. The genericization rules
   are author-agnostic — Diego's name will be sanitized to `[YOUR_NAME]` the
   same way [YOUR_NAME]'s is.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `LEAK SCAN FAILED` with a merchant slug | A new merchant or alias appeared that's not in the deny list | Add the slug to `MERCHANT_DENYLIST` in `scripts/sync-template.py` and re-run |
| `LEAK SCAN FAILED` with `[YOUR_NAME]` | A new file path or context introduced an un-genericized identity reference | Add a substitution rule to `GENERICIZATION_RULES` |
| `test-subagents.py` fails after sync | Genericization broke a required marker (e.g. `## Hard rules`) in a skill | Inspect the diff in `~/Documents/accelerate-apex-template/`, revise the rule to be more specific |
| Push rejected (non-fast-forward) | Diego pushed since you last pulled | The script auto-rebases on `--push`. If running manually, `git pull --rebase` first |
| 403 on push | PAT expired or scope reduced | Regenerate at github.com/settings/personal-access-tokens with `Contents: Read and write` on `apex` |

## Audit trail

- All commits live in `apex` history.
- The sync script writes a one-line entry to `data/runbooks/template-sync-log.md` on every successful push (date, author, commit SHA, files changed).
- Weekly, run `python3 scripts/sync-template.py --report` to summarize commits since last week and identify untouched assets.
