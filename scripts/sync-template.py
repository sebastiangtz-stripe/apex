#!/usr/bin/env python3
"""
Sync this live workspace's template-relevant files to the local template
mirror at ~/Documents/SGG-Assistant-Template/, optionally committing and
pushing to GitHub apex (sebastiangtz-stripe/apex).

This script is the only sanctioned way to publish improvements from a live
workspace (with merchant data) to the shared template (no merchant data).
See data/runbooks/template-sync.md for the full protocol, roles, and
conflict resolution rules.

Usage:
  python3 scripts/sync-template.py                       # local sync only, no push
  python3 scripts/sync-template.py --dry-run             # show what would change
  python3 scripts/sync-template.py --push --message "X"  # sync + commit + push
  python3 scripts/sync-template.py --check               # report drift only, exit code 0/1
  python3 scripts/sync-template.py --report              # weekly commit summary

Failure modes (script aborts BEFORE any push):
  - Leak scan finds a merchant slug or identity token in template content
  - test-subagents.py reports any failed contract
  - Template directory has uncommitted local changes that aren't from this script
  - Push is non-fast-forward and rebase against origin/main fails
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

LIVE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = Path.home() / "Documents" / "SGG-Assistant-Template"
SYNC_LOG = LIVE_ROOT / "data" / "runbooks" / "template-sync-log.md"

# ── What's template-relevant ─────────────────────────────────────────────────

# Paths to copy from live → template. Directories are recursive.
SYNC_PATHS = [
    ".cursor/agents/",
    ".cursor/skills/",
    ".cursor/rules/",
    ".cursor/hooks/",
    ".cursor/hooks.json",
    ".cursor/settings.json",
    "scripts/",
    "data/runbooks/",
    "data/lessons-learned/README.md",
    "templates/emails/",
    "CLAUDE.md",
    "CLAUDE.md.pre-trim-notes.md",
]

# Rsync excludes applied within SYNC_PATHS
RSYNC_EXCLUDES = [
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "drift-audit-last-run.txt",
]

# Files NEVER touched by sync (template owns these locally — never overwritten
# by a sync from live).
TEMPLATE_OWNED = {
    ".env.example",
    ".gitignore",
    "README.md",
    "SETUP.md",
    "projects/INDEX.md",
    "sessions/INDEX.md",
    "ideas/INDEX.md",
    "_inbox/.gitkeep",
    "data/lessons-learned/README.md",  # also in SYNC_PATHS — TEMPLATE_OWNED takes precedence
    "data/runbooks/template-sync-log.md",  # log file, append-only on template side
}

# Always preserve the example-merchant scaffold
TEMPLATE_PROTECTED_DIRS = {
    "projects/active/example-merchant",
}

# ── Genericization rules ─────────────────────────────────────────────────────

# (pattern, replacement) — applied to every synced file's content.
# Order matters: longer/more-specific patterns first.
GENERICIZATION_RULES = [
    # Identity — name (multi-form before single-form to avoid double-replacement)
    ("[YOUR_NAME]", "[YOUR_NAME]"),
    ("[YOUR_NAME]", "[YOUR_NAME]"),
    ("[YOUR_NAME]'s", "[YOUR_NAME]'s"),
    ("[YOUR_NAME]", "[YOUR_NAME]"),
    # Identity — email + initials
    ("your.name@stripe.com", "your.name@stripe.com"),
    # Identity — name in lowercased regex / SQL-like contexts
    ("LIKE '%<your_first_name_lowercased>%'", "LIKE '%<your_first_name_lowercased>%'"),
    ("'%<your_first_name_lowercased>%'", "'%<your_first_name_lowercased>%'"),
    # Board name
    ('"[YOUR_BOARD_NAME]"', '"[YOUR_BOARD_NAME]"'),
    ('"[YOUR_BOARD_NAME]"', '"[YOUR_BOARD_NAME]"'),
    # Timezone
    ('TZ="[YOUR_TIMEZONE]"', 'TZ="[YOUR_TIMEZONE]"'),
    ("[YOUR_TIMEZONE]", "[YOUR_TIMEZONE]"),
    ("**Always use your local timezone.**", "**Always use your local timezone.**"),
    ("user's local TZ", "user's local TZ"),
    ("your local timezone", "your local timezone"),
    # Merchant domains in examples → RFC 2606 reserved
    ("example.com", "example.com"),
    ("example.com", "example.com"),
    ("example.com", "example.com"),
    ("example.com", "example.com"),
    ("example.com", "example.com"),
    # Real merchant slugs in script docstrings / examples
    ("--slug example-merchant", "--slug example-merchant"),
    ("--slug example-merchant", "--slug example-merchant"),
    ("--slug example-merchant", "--slug example-merchant"),
    # Owner field with personal initials
    ("Owner: usually `[YOUR_NAME]`", "Owner: usually `[YOUR_INITIALS]`"),
    ("Owner: [YOUR_INITIALS]", "Owner: [YOUR_INITIALS]"),
]

# Asana GIDs in sync-to-asana.py — replace any quoted 13+ digit number with "REPLACE"
GID_PATTERN = re.compile(r'"(\d{13,})"')
GID_FILE_ALLOWLIST = {"scripts/sync-to-asana.py"}

# Product-brand abstractions (live workspace mentions real product brands;
# template uses Greek-letter placeholders to keep the PBD rule meaningful
# without naming real merchants).
PRODUCT_BRAND_RULES = [
    # Product Brand Disambiguation rule — abstract the whole example sentence at once
    # so we don't have to chase individual brand tokens scattered across narrative.
    (
        "Product brand disambiguation (mandatory)**: When the user's request mentions a product brand or sub-product name (e.g. `BetaProduct`, `GammaSuite`, `DeltaFlow`), the brand is a hint, NOT a slug.",
        "Product brand disambiguation (mandatory)**: When the user's request mentions a product brand or sub-product name (e.g. `BetaProduct`, `GammaSuite`, `DeltaFlow`), the brand is a hint, NOT a slug.",
    ),
    (
        "If multiple matches OR the brand maps to a sub-product within a parent merchant (e.g. `BetaProduct` is one of several products under `parent-merchant-slug`) → ask the user to confirm which slug + which product surface they mean before answering. Do not assume the most-recently-discussed product applies.",
        "If multiple matches OR the brand maps to a sub-product within a parent merchant (e.g. `BetaProduct` is one of several products under `parent-merchant-slug`) → ask the user to confirm which slug + which product surface they mean before answering. Do not assume the most-recently-discussed product applies.",
    ),
    (
        'Why this rule exists: a real-world hallucination occurred when a question about one sub-product was answered with a different sub-product of the same parent merchant. Owning the hallucination after the fact is not enough — disambiguate before answering.',
        "Why this rule exists: a real-world hallucination occurred when a question about one sub-product was answered with a different sub-product of the same parent merchant. Owning the hallucination after the fact is not enough — disambiguate before answering.",
    ),
    # Email-query example with real personal contact name → generic
    ('from:"Jane Doe"', 'from:"Jane Doe"'),
    ("OR from:jane.personal@example.com OR to:jane.personal@example.com", "OR from:jane.personal@example.com OR to:jane.personal@example.com"),

    # ── Real merchant references in script docstrings / skill examples ──
    # Each entry abstracts one specific narrative passage. Add new entries
    # when a new merchant story appears in template-relevant content.

    # scripts/weekly-metrics.py + .cursor/skills/weekly-metrics/SKILL.md
    ('Tolerant parser: many Stats lines use prose ("1 created (`<slug>`), ~10 updated").',
     'Tolerant parser: many Stats lines use prose ("1 created (`<slug>`), ~10 updated").'),
    ('extracts the *first integer* in `<text>`. Many lines have prose ("1 created (`<slug>`),',
     'extracts the *first integer* in `<text>`. Many lines have prose ("1 created (`<slug>`),'),
    # scripts/regenerate-index.py
    ("the historical archived-but-listed-as-active class of leaks).",
     "the historical archived-but-listed-as-active class of leaks)."),
    # scripts/drift-audit.py
    ('# Catches e.g. "acmeglass" ⊂ "acmeautoglass" via shared "acmeglass"',
     '# Catches e.g. "acmeglass" ⊂ "acmeautoglass" via shared "acmeglass"'),

    # .cursor/agents/comms-analyst.md — Yandy example
    ('"description": "Answer Yandy\'s question about subscription proration on plan change"',
     '"description": "Answer Jane\'s question about subscription proration on plan change"'),
    ('"notes": "Jane asked how proration works when a customer upgrades mid-cycle. Reference the Billing docs and confirm with the in-flight integration approach."',
     '"notes": "Jane asked how proration works when a customer upgrades mid-cycle. Reference the Billing docs and confirm with the in-flight integration approach."'),
    ('"label": "#proj-example-merchant thread 2026-04-23"',
     '"label": "#proj-example-merchant thread 2026-04-23"'),

    # .cursor/agents/stripe-jarvis.md
    ("On 2026-05-07 a Tier 3 currency-change response was returned summary-only on two consecutive turns even after an explicit \"paste the full body\" follow-up. The user had to draft directly.",
     "Real-world failure: a Tier 3 response was once returned summary-only on two consecutive turns even after an explicit \"paste the full body\" follow-up. The user had to draft directly."),
    ("matches the de facto pattern of `projects/active/<slug>/issues/<topic>-<date>.md`",
     "matches the de facto pattern of `projects/active/<slug>/issues/<topic>-<date>.md`"),
    ("Accelerate Jarvis is currently in active development by the Stripe Accelerate engineering team.",
     "Accelerate Jarvis is currently in active development by the Stripe Accelerate engineering team."),

    # .cursor/skills/specialist-prompt/SKILL.md
    ("canonical template proven across multiple complex investigations",
     "canonical template proven across multiple complex investigations"),
    ("(e.g. `<merchant-slug>/drafts/specialist-investigation-prompt.md`,",
     "(e.g. `<merchant-slug>/drafts/specialist-investigation-prompt.md`,"),
    ("`<merchant-slug>/drafts/specialist-architecture-prompt.md`).",
     "`<merchant-slug>/drafts/specialist-architecture-prompt.md`)."),
    ("`topic` substring to avoid duplicate work. Complex projects can accumulate 5+ specialist passes; a register",
     "`topic` substring to avoid duplicate work. Complex projects can accumulate 5+ specialist passes; a register"),
    ('(e.g. "Mike Linden\'s price-correction script hit `customer.currency` lock").',
     '(e.g. "<merchant contact>\'s price-correction script hit `customer.currency` lock").'),
    ('**Reference Phase N specialists by name** when prior reports exist (e.g. "<specialist name> validated in',
     '**Reference Phase N specialists by name** when prior reports exist (e.g. "<specialist name> validated in'),
    ("- **[YOUR_NAME]** — Stripe Accelerate IC", "- **[YOUR_NAME]** — Stripe Accelerate IC"),

    # .cursor/skills/lessons-extract/SKILL.md
    ("e.g. after", "e.g. after"),
    ('(e.g. "Skift\'s CSV had grandfathered amounts").',
     '(e.g. "<Merchant>\'s CSV had grandfathered amounts").'),
    ('(e.g. "<Source>→Stripe Billing migration always needs default-PM backfill',
     '(e.g. "<Source>→Stripe Billing migration always needs default-PM backfill'),
    ("- `pattern-billing-migration.md` (e.g. 2+ merchants confirmed)",
     "- `pattern-billing-migration.md` (e.g. 2+ merchants confirmed)"),
    ("- `pattern-default-pm-import-gap.md`", "- `pattern-default-pm-import-gap.md`"),
    ("- `pattern-customer-currency-lock.md`", "- `pattern-customer-currency-lock.md`"),
    ("- `pattern-cnp-tipping-overcapture.md`", "- `pattern-cnp-tipping-overcapture.md`"),
    ("- `pattern-paid-out-of-band-migration.md`", "- `pattern-paid-out-of-band-migration.md`"),

    # .cursor/skills/recall/SKILL.md
    ('("3 candidates: [<slug-a>], [<slug-b>], [pattern-some-topic]. Which?").',
     '("3 candidates: [<slug-a>], [<slug-b>], [pattern-some-topic]. Which?").'),

    # .cursor/skills/index-reconciler/SKILL.md
    ("listing archived merchants in active sections,",
     "listing archived merchants in active sections,"),

    # .cursor/skills/scan-review/SKILL.md
    ("- Slack — #proj-example-merchant thread 2026-04-23 — https://stripe.slack.com/archives/C0XXXX/p1714000000000000",
     "- Slack — #proj-example-merchant thread 2026-04-23 — https://stripe.slack.com/archives/C0XXXX/p1714000000000000"),
    ("forgotten drafts don't accumulate.",
     "forgotten drafts don't accumulate."),

    # .cursor/skills/drift-audit/SKILL.md
    ("INDEX freshness, slug collisions (e.g. `acmeglass` vs `acme-auto-glass`),",
     "INDEX freshness, slug collisions (e.g. `acmeglass` vs `acme-auto-glass`),"),
    ("**C. Slug collisions** | Same merchant under multiple slugs via (a) shared normalized tokens or (b) shared compressed substring (catches e.g. `acmeglass` ↔ `acme-auto-glass`).",
     "**C. Slug collisions** | Same merchant under multiple slugs via (a) shared normalized tokens or (b) shared compressed substring (catches e.g. `acmeglass` ↔ `acme-auto-glass`)."),

    # .cursor/skills/contact-gap-audit/SKILL.md
    ('from:example.com OR to:example.com OR from:"Jane Doe" OR from:jane.personal@example.com OR to:jane.personal@example.com',
     'from:example.com OR to:example.com OR from:"Jane Doe" OR from:jane.personal@example.com OR to:jane.personal@example.com'),

    # .cursor/skills/email-agent/SKILL.md
    ('Show draft to the user. Ask: "Ready to send, or changes needed?"',
     'Show draft to the user. Ask: "Ready to send, or changes needed?"'),

    # .cursor/skills/meeting-prep/SKILL.md
    ("**Never auto-send the prep doc**. It's a draft for the user's eyes only.",
     "**Never auto-send the prep doc**. It's a draft for the user's eyes only."),

    # .cursor/skills/action-items/SKILL.md
    ('| "Just one merchant" | `--slug example-merchant` |',
     '| "Just one merchant" | `--slug example-merchant` |'),

    # data/runbooks/asana-api.md
    ("This is a known parsing pitfall — be defensive when reading Asana JSON in scripts.",
     "This is a known parsing pitfall — be defensive when reading Asana JSON in scripts."),
]

# ── Leak scan denylist ───────────────────────────────────────────────────────

IDENTITY_DENYLIST = [
    "sebastian", "sebastián", "sebastiangtz", "sgg",
    "america/mexico", "mexico_city", "mexico city",
    "stripe accelerate — sgg", "agentic test sgg",
]

# Real merchant slugs that must never appear in template content.
# Add to this list whenever a new merchant joins the live workspace.
MERCHANT_DENYLIST = [
    "acolide", "aimpoint", "caris-life", "caris life", "chabad", "dedalus",
    "earlenterprise", "foto-master", "foto master", "gala-glp", "gala glp",
    "hypetix", "infotech", "la-nova", "la nova", "mdhub", "missing-link",
    "missing link", "nljc", "nielsen-moller", "nielsen & moller",
    "nmautoglass", "novomedici", "orbxi", "painting-co", "painting co",
    "parler", "peoria", "plannery", "pledgepro", "prri", "rula", "shopmy",
    "skift", "the-bros", "the bros", "touchpix", "ultimate-diamond",
    "ultimate diamond", "valon", "virid", "weinfuse",
    "ez-gift", "haptickk", "odyssey",
    "ecellar", "etip", "mealz", "bidx", "cotillion", "sitecare",
    "recurly",  # Stripe competitor name — abstracted in lessons examples
    # Real human names that have appeared in comms
    "mike linden", "charles winters", "yandy", "alexx", "jbedi",
    "javid calcatti", "kai", "javi",
]

# Acceptable substrings (won't trigger leak even if they contain a denylist token).
# These are intentional identifiers in the template (the apex URL, the
# template directory path) that both authors need to know.
LEAK_SCAN_ALLOWLIST = {
    "your.name@stripe.com",
    "accelerate@stripe.com",
    "*@stripe.com",
    "*@professionalservices.stripe.com",
    "<your_first_name_lowercased>",
    # The apex repo URL itself contains "sebastiangtz" — that's the GitHub user
    # both authors push to; not a leak.
    "sebastiangtz-stripe/apex",
    "github.com/sebastiangtz-stripe",
    # Local template directory path — both authors use the same path per the
    # runbook. The "SGG" substring inside the directory name is intentional
    # (the directory predates the apex repo).
    "SGG-Assistant-Template",
}

# Files where the leak scanner's own denylist tokens may appear by design.
# These files are excluded from the leak scan entirely.
LEAK_SCAN_FILE_EXCLUDES = {
    "scripts/sync-template.py",   # contains the denylist constants by definition
    "data/runbooks/template-sync.md",  # documents the protocol, references author names
    "CLAUDE.md.pre-trim-notes.md",  # design history, references real merchants in past tense
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def run(cmd, cwd=None, check=True, capture=False):
    """Run a shell command, return CompletedProcess."""
    if isinstance(cmd, str):
        cmd_list = cmd.split()
    else:
        cmd_list = cmd
    return subprocess.run(
        cmd_list,
        cwd=cwd,
        check=check,
        capture_output=capture,
        text=True,
    )

def fail(msg, code=1):
    print(f"\n✗ {msg}", file=sys.stderr)
    sys.exit(code)

def info(msg):
    print(f"  {msg}")

def step(msg):
    print(f"\n→ {msg}")

# ── Pre-flight ───────────────────────────────────────────────────────────────

def preflight_template(args):
    """Confirm template directory exists and is in a clean state for syncing."""
    if not TEMPLATE_ROOT.exists():
        fail(f"Template directory not found: {TEMPLATE_ROOT}\n  "
             f"This script is for ongoing sync — initial setup is documented in "
             f"data/runbooks/template-sync.md.")
    if not (TEMPLATE_ROOT / ".git").exists():
        fail(f"{TEMPLATE_ROOT} is not a git repo. Run `git init` and add the apex remote.")

    # Branch check
    branch = run(["git", "-C", str(TEMPLATE_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                 capture=True).stdout.strip()
    if branch != "main":
        fail(f"Template is on branch '{branch}', expected 'main'. Switch and retry.")

    # Working tree must be clean (any prior uncommitted state would be clobbered)
    status = run(["git", "-C", str(TEMPLATE_ROOT), "status", "--porcelain"],
                 capture=True).stdout.strip()
    if status and not args.dry_run:
        fail(f"Template working tree has uncommitted changes:\n{status}\n  "
             f"Resolve manually before re-running.")

    # If --push, ensure we have a remote
    if args.push:
        remotes = run(["git", "-C", str(TEMPLATE_ROOT), "remote"], capture=True).stdout.strip()
        if "origin" not in remotes:
            fail("--push requested but template has no 'origin' remote configured.")

# ── Sync ─────────────────────────────────────────────────────────────────────

def rsync_paths(dry_run=False):
    """Rsync each SYNC_PATHS entry from live → template."""
    step(f"Rsync {len(SYNC_PATHS)} paths from live → template")
    for sp in SYNC_PATHS:
        src = LIVE_ROOT / sp
        if not src.exists():
            info(f"skip  {sp}  (not present in live workspace)")
            continue
        # Don't overwrite template-owned files even if they're under a synced path
        if sp in TEMPLATE_OWNED:
            info(f"skip  {sp}  (TEMPLATE_OWNED)")
            continue
        dest = TEMPLATE_ROOT / sp
        if src.is_dir():
            cmd = ["rsync", "-a", "--delete"]
            for ex in RSYNC_EXCLUDES:
                cmd.extend(["--exclude", ex])
            if dry_run:
                cmd.append("--dry-run")
                cmd.append("-i")
            cmd.extend([f"{src}/", f"{dest}/"])
            dest.mkdir(parents=True, exist_ok=True)
        else:
            cmd = ["rsync", "-a"]
            if dry_run:
                cmd.append("--dry-run")
                cmd.append("-i")
            cmd.extend([str(src), str(dest)])
            dest.parent.mkdir(parents=True, exist_ok=True)
        result = run(cmd, capture=True, check=False)
        if result.returncode != 0:
            fail(f"rsync failed for {sp}: {result.stderr}")
        info(f"sync  {sp}")

# ── Genericization ───────────────────────────────────────────────────────────

def apply_genericization(dry_run=False):
    """Apply all GENERICIZATION_RULES to every file under TEMPLATE_ROOT."""
    step("Apply genericization rules")
    files_changed = 0
    total_subs = 0
    for path in TEMPLATE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        rel = path.relative_to(TEMPLATE_ROOT).as_posix()
        if rel in TEMPLATE_OWNED:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        original = text
        local_subs = 0
        # Product-brand abstractions first (long, specific paragraph patterns
        # before any single-word substitution that might break them).
        for old, new in PRODUCT_BRAND_RULES:
            n = text.count(old)
            if n:
                text = text.replace(old, new)
                local_subs += n
        for old, new in GENERICIZATION_RULES:
            n = text.count(old)
            if n:
                text = text.replace(old, new)
                local_subs += n
        # Asana GID sanitization
        if rel in GID_FILE_ALLOWLIST:
            new_text = GID_PATTERN.sub('"REPLACE"', text)
            gid_subs = len(GID_PATTERN.findall(text))
            if gid_subs:
                text = new_text
                local_subs += gid_subs
        if text != original:
            files_changed += 1
            total_subs += local_subs
            if not dry_run:
                path.write_text(text)
            info(f"{'(dry) ' if dry_run else ''}genericized {rel} ({local_subs} subs)")
    print(f"  → {total_subs} substitutions across {files_changed} files")

# ── Leak scan ────────────────────────────────────────────────────────────────

def leak_scan():
    """Fail if any denylist token appears anywhere in TEMPLATE_ROOT (excluding .git)."""
    step("Leak scan")
    all_tokens = IDENTITY_DENYLIST + MERCHANT_DENYLIST
    leaks = []
    for path in TEMPLATE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        rel = path.relative_to(TEMPLATE_ROOT).as_posix()
        if rel in LEAK_SCAN_FILE_EXCLUDES:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        lower = text.lower()
        for token in all_tokens:
            if token.lower() in lower:
                # Find line context
                for lineno, line in enumerate(text.splitlines(), 1):
                    if token.lower() in line.lower():
                        # Check allowlist
                        if any(allowed in line for allowed in LEAK_SCAN_ALLOWLIST):
                            continue
                        leaks.append((str(path.relative_to(TEMPLATE_ROOT)), lineno, token, line.strip()))
        # Also catch 13+ digit numeric strings in any file
        for m in re.finditer(r"\b\d{13,}\b", text):
            gid = m.group(0)
            lineno = text[:m.start()].count("\n") + 1
            line = text.splitlines()[lineno-1] if lineno-1 < len(text.splitlines()) else ""
            leaks.append((str(path.relative_to(TEMPLATE_ROOT)), lineno, f"asana-gid:{gid}", line.strip()))

    if leaks:
        for fp, lineno, token, line in leaks[:30]:
            print(f"  LEAK {fp}:L{lineno} [{token}]: {line[:140]}", file=sys.stderr)
        if len(leaks) > 30:
            print(f"  ... and {len(leaks)-30} more leaks", file=sys.stderr)
        fail(f"LEAK SCAN FAILED — {len(leaks)} hits across {len({fp for fp,_,_,_ in leaks})} files. "
             f"Add a GENERICIZATION_RULE or MERCHANT_DENYLIST entry, or remove the offending content.")
    print("  → CLEAN, no leaks")

# ── Validation ───────────────────────────────────────────────────────────────

def run_contract_validator():
    """Run scripts/test-subagents.py inside the template."""
    step("Run contract validator (scripts/test-subagents.py)")
    result = run(["python3", "scripts/test-subagents.py"],
                 cwd=str(TEMPLATE_ROOT), capture=True, check=False)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        fail("Contract validator failed inside template — see output above.")
    # Report summary line
    last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    info(last_line)

# ── Git operations ───────────────────────────────────────────────────────────

def has_changes_to_commit():
    status = run(["git", "-C", str(TEMPLATE_ROOT), "status", "--porcelain"],
                 capture=True).stdout.strip()
    return bool(status)

def stage_and_commit(message):
    step(f"Commit: {message!r}")
    run(["git", "-C", str(TEMPLATE_ROOT), "add", "-A"])
    if not has_changes_to_commit():
        info("no changes to commit; skipping")
        return None
    # Use the user's git config; let the commit hook (Stripe PII scanner) run normally
    run(["git", "-C", str(TEMPLATE_ROOT), "commit", "-m", message])
    sha = run(["git", "-C", str(TEMPLATE_ROOT), "rev-parse", "HEAD"],
              capture=True).stdout.strip()
    info(f"committed {sha[:7]}")
    return sha

def pull_rebase():
    step("Pull --rebase from origin/main (to incorporate peer pushes)")
    result = run(["git", "-C", str(TEMPLATE_ROOT), "pull", "--rebase", "origin", "main"],
                 capture=True, check=False)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        fail("git pull --rebase failed — resolve manually in template directory.")
    info(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "(no upstream changes)")

def push_to_origin():
    step("Push to origin/main")
    result = run(["git", "-C", str(TEMPLATE_ROOT), "push", "origin", "main"],
                 capture=True, check=False)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        fail("git push failed. If this is an auth issue, see SETUP.md and "
             "ensure the apex remote is configured with a working credential.")
    info("pushed")

def append_sync_log(sha, message, files_changed_count):
    """Append a one-line entry to data/runbooks/template-sync-log.md."""
    if sha is None:
        return
    SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not SYNC_LOG.exists():
        SYNC_LOG.write_text("# Template Sync Log\n\n"
                            "Append-only history of every sync from a live workspace to apex.\n\n"
                            "| Timestamp | Author | Commit | Files | Message |\n"
                            "|---|---|---|---|---|\n")
    author = run(["git", "-C", str(LIVE_ROOT), "config", "user.name"],
                 capture=True, check=False).stdout.strip() or "(unknown)"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"| {ts} | {author} | `{sha[:7]}` | {files_changed_count} | {message} |\n"
    with SYNC_LOG.open("a") as f:
        f.write(line)
    info(f"logged to {SYNC_LOG.relative_to(LIVE_ROOT)}")

# ── Modes ────────────────────────────────────────────────────────────────────

def mode_check():
    """Report drift only. Exit 1 if template would change."""
    if not TEMPLATE_ROOT.exists():
        print("DRIFT: template directory missing")
        sys.exit(1)
    drift_paths = []
    for sp in SYNC_PATHS:
        src = LIVE_ROOT / sp
        dest = TEMPLATE_ROOT / sp
        if not src.exists():
            continue
        if sp in TEMPLATE_OWNED:
            continue
        # Use rsync --dry-run -i to detect changes
        if src.is_dir():
            cmd = ["rsync", "-an", "-i"]
            for ex in RSYNC_EXCLUDES:
                cmd.extend(["--exclude", ex])
            cmd.extend([f"{src}/", f"{dest}/"])
        else:
            cmd = ["rsync", "-an", "-i", str(src), str(dest)]
        result = run(cmd, capture=True, check=False)
        if result.stdout.strip():
            drift_paths.append(sp)
    if drift_paths:
        print(f"DRIFT: {len(drift_paths)} template-relevant paths differ:")
        for p in drift_paths:
            print(f"  {p}")
        sys.exit(1)
    print("CLEAN: live workspace matches template")
    sys.exit(0)

def mode_report():
    """Show commits to apex since 1 week ago."""
    if not TEMPLATE_ROOT.exists():
        fail("template directory missing")
    print("# Apex commits in last 7 days\n")
    result = run(["git", "-C", str(TEMPLATE_ROOT), "log",
                  "--since=7.days", "--pretty=format:%h %ai %an: %s"],
                 capture=True, check=False)
    print(result.stdout or "(no commits in last 7 days)")
    print()
    print("# Files unchanged in last 30 days (potential staleness)")
    print("(run `git -C ~/Documents/SGG-Assistant-Template/ log --diff-filter=AM --name-only --since=30.days | sort -u` for inverse)")

def mode_pull_review():
    """Pull apex + show diff vs live workspace, file-by-file."""
    print("--pull --review not yet implemented. Workflow:\n"
          "  1. git -C ~/Documents/SGG-Assistant-Template/ pull --rebase\n"
          "  2. Manually diff against your live workspace and copy desired changes.\n"
          "Reverse-sync from template → live is intentionally manual to avoid clobbering merchant data.")
    sys.exit(0)

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true",
                        help="commit and push to origin/main (apex)")
    parser.add_argument("--message", "-m", type=str, default=None,
                        help="commit message (required with --push)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would change, don't write")
    parser.add_argument("--check", action="store_true",
                        help="report drift only; exit 1 if template would change")
    parser.add_argument("--report", action="store_true",
                        help="show recent apex commits + staleness report")
    parser.add_argument("--pull", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--review", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.check:
        mode_check()
    if args.report:
        mode_report()
    if args.pull or args.review:
        mode_pull_review()

    if args.push and not args.message:
        fail("--push requires --message \"<one-line summary>\"")

    print(f"Live workspace: {LIVE_ROOT}")
    print(f"Template:       {TEMPLATE_ROOT}")

    preflight_template(args)
    rsync_paths(dry_run=args.dry_run)
    apply_genericization(dry_run=args.dry_run)

    if args.dry_run:
        step("DRY RUN — not running leak scan / validator / commit / push")
        return

    leak_scan()
    run_contract_validator()

    if args.push:
        # Pull first to incorporate peer changes
        pull_rebase()
        sha = stage_and_commit(args.message)
        if sha:
            push_to_origin()
            # Count files in the commit
            count = int(run(["git", "-C", str(TEMPLATE_ROOT), "show", "--stat",
                             "--format=", sha],
                            capture=True).stdout.strip().splitlines()[-1].split()[0])
            append_sync_log(sha, args.message, count)
    else:
        if has_changes_to_commit():
            print(f"\n→ Local template has uncommitted changes. To publish:")
            print(f"  python3 scripts/sync-template.py --push --message \"<msg>\"")
        else:
            print("\n→ Template already matches live workspace. Nothing to do.")

if __name__ == "__main__":
    main()
