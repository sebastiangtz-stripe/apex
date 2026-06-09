#!/usr/bin/env python3
"""
Pull updates from the apex template into a live workspace.

Non-interactive script designed for agent consumption — all output is JSON
on stdout. The calling agent (Cursor/Claude Code) handles user interaction
(diffs, accept/reject) conversationally.

Usage:
  python3 scripts/update-from-apex.py --check
  python3 scripts/update-from-apex.py --diff
  python3 scripts/update-from-apex.py --diff-file .cursor/agents/comms-analyst.md
  python3 scripts/update-from-apex.py --apply-file .cursor/agents/comms-analyst.md
  python3 scripts/update-from-apex.py --finalize
  python3 scripts/update-from-apex.py --status
"""

import argparse
import difflib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

LIVE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = Path.home() / "Documents" / "accelerate-apex-template"
APEX_REMOTE = "git@github.com:sebastiangtz-stripe/apex.git"

UPDATE_CONFIG = LIVE_ROOT / "data" / "update-config.json"
STATE_FILE = LIVE_ROOT / "data" / "update-check-state.json"
APPLIED_LOG = LIVE_ROOT / "data" / "update-applied-log.json"

# ── Template-relevant paths (must match sync-template.py SYNC_PATHS) ─────────

SYNC_PATHS = [
    ".cursor/agents/",
    ".cursor/skills/",
    ".cursor/rules/",
    ".cursor/hooks/",
    ".cursor/hooks.json",
    ".cursor/mcp.json",
    ".cursor/settings.json",
    "scripts/",
    "data/runbooks/",
    "data/lessons-learned/README.md",
    "templates/emails/",
    "CLAUDE.md",
    "CLAUDE.md.pre-trim-notes.md",
    ".env.example",
]

NEVER_OVERWRITE = {
    ".env",
    ".env.example",
    "data/update-config.json",
    "data/update-check-state.json",
    "data/update-applied-log.json",
    # sync-template.py contains GENERICIZATION_RULES with real values on the
    # left side in live workspaces. De-genericization would replace BOTH sides
    # with real values, breaking the push direction. Must be updated manually.
    "scripts/sync-template.py",
}

SKIP_DIRS = {".git", "__pycache__"}

# ── Helpers ──────────────────────────────────────────────────────────────────


def run(cmd, cwd=None, check=True, capture=True):
    if isinstance(cmd, str):
        cmd = cmd.split()
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=capture, text=True)


def output_json(obj):
    print(json.dumps(obj, indent=2))


def fail(msg):
    output_json({"error": msg})
    sys.exit(1)


# ── De-genericization ────────────────────────────────────────────────────────


def load_degeneric_config():
    """Load placeholder→real_value mapping from data/update-config.json."""
    if not UPDATE_CONFIG.exists():
        fail("data/update-config.json not found. Run /setup or create manually "
             "from data/update-config.json.example")
    config = json.loads(UPDATE_CONFIG.read_text())
    rules = []
    for placeholder, real_value in config.get("substitutions", {}).items():
        if real_value and real_value != placeholder:
            rules.append((placeholder, real_value))
    rules.sort(key=lambda r: -len(r[0]))
    return rules


def degenericize(text, rules):
    """Replace template placeholders with real values (inverse of genericization)."""
    for placeholder, real_value in rules:
        text = text.replace(placeholder, real_value)
    return text


# ── State persistence ────────────────────────────────────────────────────────


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def log_applied(rel_path, method):
    log = []
    if APPLIED_LOG.exists():
        log = json.loads(APPLIED_LOG.read_text())
    log.append({
        "path": rel_path,
        "method": method,
        "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    APPLIED_LOG.parent.mkdir(parents=True, exist_ok=True)
    APPLIED_LOG.write_text(json.dumps(log, indent=2) + "\n")


# ── Env migration check ──────────────────────────────────────────────────────


def _parse_env_keys(path):
    """Extract variable names from a .env file (ignoring comments and blanks)."""
    keys = []
    if not path.exists():
        return keys
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            keys.append(line.split("=", 1)[0])
    return keys


def check_env_migrations():
    """Compare template .env.example against live .env. Return missing keys."""
    template_example = TEMPLATE_ROOT / ".env.example"
    live_env = LIVE_ROOT / ".env"

    if not template_example.exists() or not live_env.exists():
        return []

    template_keys = set(_parse_env_keys(template_example))
    live_keys = set(_parse_env_keys(live_env))
    missing = sorted(template_keys - live_keys)
    return missing


def _check_migrations():
    """Check for pending structural migrations. Returns list of summaries."""
    migrations_dir = LIVE_ROOT / "data" / "migrations"
    state_file = migrations_dir / "state.json"

    if not migrations_dir.exists():
        return []

    applied_ids = set()
    if state_file.exists():
        state = json.loads(state_file.read_text())
        applied_ids = {e["migration_id"] for e in state.get("applied", [])}

    pending = []
    for f in sorted(migrations_dir.glob("*.json")):
        if f.name in ("schema.json", "state.json"):
            continue
        try:
            data = json.loads(f.read_text())
            mid = data.get("migration_id", "")
            if mid and mid not in applied_ids:
                pending.append({
                    "migration_id": mid,
                    "description": data.get("description", ""),
                    "steps": len(data.get("steps", [])),
                })
        except (json.JSONDecodeError, KeyError):
            continue
    return pending


# ── File comparison ──────────────────────────────────────────────────────────


def _iter_template_files():
    """Yield all template-relevant file paths (as posix relative strings)."""
    for sp in SYNC_PATHS:
        template_path = TEMPLATE_ROOT / sp
        if not template_path.exists():
            continue
        if template_path.is_dir():
            for fp in sorted(template_path.rglob("*")):
                if not fp.is_file():
                    continue
                if any(part in SKIP_DIRS for part in fp.parts):
                    continue
                rel = fp.relative_to(TEMPLATE_ROOT).as_posix()
                if rel not in NEVER_OVERWRITE:
                    yield rel
        else:
            if sp not in NEVER_OVERWRITE:
                yield sp


def _file_differs(rel_path, rules):
    """True if de-genericized template content differs from live workspace."""
    template_file = TEMPLATE_ROOT / rel_path
    live_file = LIVE_ROOT / rel_path

    if not template_file.exists():
        return False

    try:
        template_text = template_file.read_text()
    except UnicodeDecodeError:
        return False

    degenericized = degenericize(template_text, rules)

    if not live_file.exists():
        return True

    try:
        live_text = live_file.read_text()
    except UnicodeDecodeError:
        return False

    return degenericized != live_text


def get_pending_files(rules):
    """Return sorted list of relative paths where template differs from live."""
    pending = []
    for rel in _iter_template_files():
        if _file_differs(rel, rules):
            pending.append(rel)
    return pending


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_check():
    """Fetch origin, compare, write state, output JSON summary."""
    # Ensure template directory exists
    if not TEMPLATE_ROOT.exists():
        result = run(["git", "clone", APEX_REMOTE, str(TEMPLATE_ROOT)], check=False)
        if result.returncode != 0:
            fail(f"Failed to clone apex: {result.stderr.strip()}")

    # Fetch latest
    result = run(["git", "-C", str(TEMPLATE_ROOT), "fetch", "origin"], check=False)
    if result.returncode != 0:
        fail(f"git fetch failed: {result.stderr.strip()}")

    # Compare heads
    local_head = run(["git", "-C", str(TEMPLATE_ROOT), "rev-parse", "HEAD"]).stdout.strip()
    remote_head = run(["git", "-C", str(TEMPLATE_ROOT), "rev-parse", "origin/main"]).stdout.strip()

    commits = []
    if local_head != remote_head:
        # Get commit log before pulling
        log_result = run(["git", "-C", str(TEMPLATE_ROOT), "log",
                          f"{local_head}..{remote_head}",
                          "--pretty=format:%H|%ai|%an|%s"])
        for line in log_result.stdout.strip().splitlines():
            if "|" in line:
                sha, date_str, author, msg = line.split("|", 3)
                commits.append({"sha": sha[:7], "date": date_str,
                                "author": author, "message": msg})

        # Pull to update local mirror
        pull_result = run(["git", "-C", str(TEMPLATE_ROOT), "pull", "--rebase",
                           "origin", "main"], check=False)
        if pull_result.returncode != 0:
            fail(f"git pull --rebase failed: {pull_result.stderr.strip()}")

    # Load config and compute pending files
    rules = load_degeneric_config()
    pending = get_pending_files(rules)

    # Write state
    now = datetime.now(timezone.utc)
    state = {
        "last_check": now.isoformat(timespec="seconds"),
        "last_check_date": now.strftime("%Y-%m-%d"),
        "local_head": local_head,
        "remote_head": remote_head,
        "new_commits": len(commits),
        "pending_files": len(pending),
        "pending_file_list": pending,
    }
    save_state(state)

    # Check for new env variables needed
    env_migrations = check_env_migrations()

    # Check for pending structural migrations
    pending_migrations = _check_migrations()

    has_updates = pending or env_migrations or pending_migrations
    if not has_updates:
        output_json({"status": "up_to_date", "commits": commits,
                     "env_migrations": [], "pending_migrations": 0, "state": state})
    else:
        output_json({"status": "updates_available", "commits": commits,
                     "pending_files": len(pending), "file_list": pending,
                     "env_migrations": env_migrations,
                     "pending_migrations": len(pending_migrations),
                     "migration_summaries": pending_migrations,
                     "state": state})


def cmd_diff(specific_file=None):
    """Generate unified diffs for pending files (or one specific file)."""
    rules = load_degeneric_config()

    if specific_file:
        files = [specific_file]
    else:
        files = get_pending_files(rules)

    diffs = []
    for rel_path in files:
        template_file = TEMPLATE_ROOT / rel_path
        live_file = LIVE_ROOT / rel_path

        if not template_file.exists():
            continue

        try:
            template_text = degenericize(template_file.read_text(), rules)
        except UnicodeDecodeError:
            continue

        if live_file.exists():
            try:
                live_text = live_file.read_text()
            except UnicodeDecodeError:
                continue
            status = "modified"
        else:
            live_text = ""
            status = "new"

        diff_lines = list(difflib.unified_diff(
            live_text.splitlines(keepends=True),
            template_text.splitlines(keepends=True),
            fromfile=f"live/{rel_path}",
            tofile=f"apex/{rel_path}",
        ))

        diffs.append({
            "path": rel_path,
            "status": status,
            "diff": "".join(diff_lines),
            "additions": sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++")),
            "deletions": sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---")),
        })

    output_json({"files": diffs, "total": len(diffs)})


def cmd_apply_file(rel_path):
    """Apply a single template file to the live workspace (de-genericized)."""
    if rel_path in NEVER_OVERWRITE:
        fail(f"Protected file: {rel_path} is in the NEVER_OVERWRITE list")

    template_file = TEMPLATE_ROOT / rel_path
    live_file = LIVE_ROOT / rel_path

    if not template_file.exists():
        fail(f"File not found in template: {rel_path}")

    rules = load_degeneric_config()

    try:
        template_text = degenericize(template_file.read_text(), rules)
        live_file.parent.mkdir(parents=True, exist_ok=True)
        live_file.write_text(template_text)
        method = "degenericized"
    except UnicodeDecodeError:
        live_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(template_file), str(live_file))
        method = "binary_copy"

    log_applied(rel_path, method)
    output_json({"applied": rel_path, "method": method})


def cmd_finalize():
    """Mark update cycle complete."""
    state = load_state()
    state["last_applied"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["status"] = "finalized"
    state["pending_files"] = 0
    state["pending_file_list"] = []
    save_state(state)
    output_json({"finalized": True, "state": state})


def cmd_status():
    """Return current state."""
    state = load_state()
    if not state:
        output_json({"status": "never_checked", "last_check": None, "pending_files": 0})
        return
    output_json(state)


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--check", action="store_true",
                        help="Fetch origin, compare, write check timestamp")
    parser.add_argument("--diff", action="store_true",
                        help="Show per-file diffs (de-genericized template vs live)")
    parser.add_argument("--diff-file", type=str, default=None,
                        help="Show diff for a single file")
    parser.add_argument("--apply-file", type=str, default=None,
                        help="Apply one file (de-genericize + write to live workspace)")
    parser.add_argument("--finalize", action="store_true",
                        help="Mark update cycle complete")
    parser.add_argument("--status", action="store_true",
                        help="Show last check time, pending count")
    args = parser.parse_args()

    if args.check:
        cmd_check()
    elif args.diff or args.diff_file:
        cmd_diff(specific_file=args.diff_file)
    elif args.apply_file:
        cmd_apply_file(args.apply_file)
    elif args.finalize:
        cmd_finalize()
    elif args.status:
        cmd_status()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
