#!/usr/bin/env python3
"""
Deterministic migration executor for apex template updates.

Reads structured migration manifests from data/migrations/ and applies them
mechanically — no LLM interpretation, no creative execution. The calling agent
invokes this script and reports results; it never edits target files directly.

Usage:
  python3 scripts/apply-migration.py --check
  python3 scripts/apply-migration.py --apply --file data/migrations/2026-06-09-example.json
  python3 scripts/apply-migration.py --apply-all
  python3 scripts/apply-migration.py --dry-run --file data/migrations/2026-06-09-example.json
  python3 scripts/apply-migration.py --validate
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = WORKSPACE_ROOT / "data" / "migrations"
STATE_FILE = MIGRATIONS_DIR / "state.json"
BACKUP_DIR = WORKSPACE_ROOT / "data" / ".migration-backups"

# ── Helpers ──────────────────────────────────────────────────────────────────


def output_json(obj):
    print(json.dumps(obj, indent=2))


def fail(msg):
    output_json({"error": msg})
    sys.exit(1)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"applied": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def get_applied_ids():
    state = load_state()
    return {entry["migration_id"] for entry in state.get("applied", [])}


def get_pending_manifests():
    applied_ids = get_applied_ids()
    pending = []
    if not MIGRATIONS_DIR.exists():
        return pending
    for f in sorted(MIGRATIONS_DIR.glob("*.json")):
        if f.name in ("schema.json", "state.json"):
            continue
        try:
            data = json.loads(f.read_text())
            mid = data.get("migration_id", "")
            if mid and mid not in applied_ids:
                pending.append(f)
        except (json.JSONDecodeError, KeyError):
            continue
    return pending


# ── Condition Evaluation ─────────────────────────────────────────────────────


def _parse_env(path=None):
    env_path = path or (WORKSPACE_ROOT / ".env")
    keys = {}
    if not env_path.exists():
        return keys
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            keys[k] = v
    return keys


def _resolve_json_path(data, key_path):
    """Navigate a dotted key path like 'permissions.allow' into a JSON structure."""
    parts = key_path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def evaluate_condition(cond):
    """Evaluate a single pre/post condition. Returns (passed: bool, detail: str)."""
    ctype = cond["type"]

    if ctype == "file_exists":
        path = WORKSPACE_ROOT / cond["path"]
        return path.exists(), f"file_exists: {cond['path']}"

    elif ctype == "file_absent":
        path = WORKSPACE_ROOT / cond["path"]
        return not path.exists(), f"file_absent: {cond['path']}"

    elif ctype == "env_key_present":
        env = _parse_env()
        keys = cond.get("keys", [cond["key"]] if "key" in cond else [])
        missing = [k for k in keys if k not in env]
        return len(missing) == 0, f"env_key_present: missing={missing}"

    elif ctype == "env_key_absent":
        env = _parse_env()
        key = cond["key"]
        return key not in env, f"env_key_absent: {key}"

    elif ctype == "json_valid":
        path = WORKSPACE_ROOT / cond["path"]
        try:
            json.loads(path.read_text())
            return True, f"json_valid: {cond['path']}"
        except Exception as e:
            return False, f"json_valid FAILED: {cond['path']} — {e}"

    elif ctype == "json_contains":
        path = WORKSPACE_ROOT / cond["path"]
        try:
            data = json.loads(path.read_text())
            resolved = _resolve_json_path(data, cond["key_path"])
            if isinstance(resolved, list):
                found = cond["value"] in resolved
            elif isinstance(resolved, dict):
                found = cond["value"] in resolved
            else:
                found = resolved == cond["value"]
            return found, f"json_contains: {cond['key_path']}={cond['value']}"
        except Exception as e:
            return False, f"json_contains FAILED: {e}"

    elif ctype == "json_key_absent":
        path = WORKSPACE_ROOT / cond["path"]
        try:
            data = json.loads(path.read_text())
            resolved = _resolve_json_path(data, cond["key_path"])
            if isinstance(resolved, list):
                absent = cond["value"] not in resolved
            elif isinstance(resolved, dict):
                absent = cond["value"] not in resolved
            else:
                absent = resolved != cond["value"]
            return absent, f"json_key_absent: {cond['key_path']}!={cond['value']}"
        except Exception:
            return True, f"json_key_absent: file/path not found (counts as absent)"

    elif ctype == "command_succeeds":
        try:
            result = subprocess.run(
                cond["command"], shell=True, capture_output=True, text=True,
                timeout=cond.get("timeout", 30), cwd=str(WORKSPACE_ROOT))
            return result.returncode == 0, f"command_succeeds: rc={result.returncode}"
        except subprocess.TimeoutExpired:
            return False, f"command_succeeds: timeout after {cond.get('timeout', 30)}s"
        except Exception as e:
            return False, f"command_succeeds: {e}"

    elif ctype == "command_output_contains":
        try:
            result = subprocess.run(
                cond["command"], shell=True, capture_output=True, text=True,
                timeout=cond.get("timeout", 30), cwd=str(WORKSPACE_ROOT))
            found = cond["expected"] in result.stdout
            return found, f"command_output_contains: '{cond['expected']}' in stdout"
        except Exception as e:
            return False, f"command_output_contains: {e}"

    return False, f"unknown condition type: {ctype}"


# ── Step Execution ───────────────────────────────────────────────────────────


def execute_step(step, dry_run=False):
    """Execute a single migration step. Returns {status, detail}."""
    stype = step["type"]
    step_id = step["id"]

    try:
        if stype == "env_append":
            return _step_env_append(step, dry_run)
        elif stype == "json_merge":
            return _step_json_merge(step, dry_run)
        elif stype == "run_command":
            return _step_run_command(step, dry_run)
        elif stype == "file_ensure":
            return _step_file_ensure(step, dry_run)
        elif stype == "rename_path":
            return _step_rename(step, dry_run)
        elif stype == "delete_path":
            return _step_delete(step, dry_run)
        else:
            return {"status": "failed", "step_id": step_id,
                    "error": f"Unknown step type: {stype}"}
    except Exception as e:
        return {"status": "failed", "step_id": step_id, "error": str(e)}


def _step_env_append(step, dry_run):
    env_path = WORKSPACE_ROOT / ".env"
    env = _parse_env(env_path)
    lines_to_add = []

    for entry in step["entries"]:
        if entry["key"] not in env:
            comment = f"# {entry['comment']}" if entry.get("comment") else ""
            if comment:
                lines_to_add.append(comment)
            lines_to_add.append(f"{entry['key']}={entry['default']}")

    if not lines_to_add:
        return {"status": "applied", "step_id": step["id"], "detail": "all keys already present"}

    if dry_run:
        return {"status": "dry_run", "step_id": step["id"],
                "would_add": lines_to_add}

    with env_path.open("a") as f:
        f.write("\n" + "\n".join(lines_to_add) + "\n")

    return {"status": "applied", "step_id": step["id"],
            "detail": f"appended {len(step['entries'])} entries"}


def _step_json_merge(step, dry_run):
    target_path = WORKSPACE_ROOT / step["target"]
    if not target_path.exists():
        return {"status": "failed", "step_id": step["id"],
                "error": f"Target file not found: {step['target']}"}

    data = json.loads(target_path.read_text())
    merge_path = step["merge_path"]
    values = step["values"]
    strategy = step["strategy"]

    # Navigate to the merge point
    parts = merge_path.split(".")
    parent = data
    for part in parts[:-1]:
        if part not in parent:
            parent[part] = {}
        parent = parent[part]
    leaf_key = parts[-1]

    if strategy == "array_append_dedup":
        existing = parent.get(leaf_key, [])
        if not isinstance(existing, list):
            return {"status": "failed", "step_id": step["id"],
                    "error": f"Expected array at {merge_path}, got {type(existing).__name__}"}
        added = [v for v in values if v not in existing]
        if not added and not dry_run:
            return {"status": "applied", "step_id": step["id"], "detail": "all values already present"}
        parent[leaf_key] = existing + added

    elif strategy == "object_merge_shallow":
        existing = parent.get(leaf_key, {})
        if not isinstance(existing, dict):
            return {"status": "failed", "step_id": step["id"],
                    "error": f"Expected object at {merge_path}, got {type(existing).__name__}"}
        existing.update(values)
        parent[leaf_key] = existing

    elif strategy == "object_merge_deep":
        existing = parent.get(leaf_key, {})
        if not isinstance(existing, dict):
            return {"status": "failed", "step_id": step["id"],
                    "error": f"Expected object at {merge_path}, got {type(existing).__name__}"}
        parent[leaf_key] = _deep_merge(existing, values)

    else:
        return {"status": "failed", "step_id": step["id"],
                "error": f"Unknown strategy: {strategy}"}

    if dry_run:
        return {"status": "dry_run", "step_id": step["id"],
                "would_write": step["target"]}

    target_path.write_text(json.dumps(data, indent=2) + "\n")
    return {"status": "applied", "step_id": step["id"],
            "detail": f"merged into {step['target']}"}


def _deep_merge(base, override):
    result = deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _step_run_command(step, dry_run):
    if dry_run:
        return {"status": "dry_run", "step_id": step["id"],
                "would_run": step["command"]}

    try:
        result = subprocess.run(
            step["command"], shell=True, capture_output=True, text=True,
            timeout=step.get("timeout", 30), cwd=str(WORKSPACE_ROOT))
    except subprocess.TimeoutExpired:
        return {"status": "failed", "step_id": step["id"],
                "error": f"Command timed out after {step.get('timeout', 30)}s"}

    if result.returncode != 0:
        return {"status": "failed", "step_id": step["id"],
                "error": f"Command failed (rc={result.returncode}): {result.stderr[:500]}"}

    if "expected_stdout" in step and step["expected_stdout"] not in result.stdout:
        return {"status": "failed", "step_id": step["id"],
                "error": f"Expected '{step['expected_stdout']}' in stdout, not found"}

    return {"status": "applied", "step_id": step["id"], "detail": "command succeeded"}


def _step_file_ensure(step, dry_run):
    target = WORKSPACE_ROOT / step["path"]
    if target.exists():
        return {"status": "applied", "step_id": step["id"], "detail": "already exists"}

    if dry_run:
        return {"status": "dry_run", "step_id": step["id"],
                "would_create": step["path"]}

    source = WORKSPACE_ROOT / step.get("source_template", step["path"])
    if source.exists() and source != target:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(target))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")

    return {"status": "applied", "step_id": step["id"], "detail": f"created {step['path']}"}


def _step_rename(step, dry_run):
    src = WORKSPACE_ROOT / step["from"]
    dst = WORKSPACE_ROOT / step["to"]

    if not src.exists():
        if dst.exists():
            return {"status": "applied", "step_id": step["id"], "detail": "already renamed"}
        return {"status": "failed", "step_id": step["id"],
                "error": f"Source not found: {step['from']}"}

    if dry_run:
        return {"status": "dry_run", "step_id": step["id"],
                "would_rename": f"{step['from']} → {step['to']}"}

    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"status": "applied", "step_id": step["id"],
            "detail": f"renamed {step['from']} → {step['to']}"}


def _step_delete(step, dry_run):
    target = WORKSPACE_ROOT / step["path"]
    if not target.exists():
        return {"status": "applied", "step_id": step["id"], "detail": "already absent"}

    if dry_run:
        return {"status": "dry_run", "step_id": step["id"],
                "would_delete": step["path"]}

    if target.is_dir():
        shutil.rmtree(str(target))
    else:
        target.unlink()
    return {"status": "applied", "step_id": step["id"], "detail": f"deleted {step['path']}"}


# ── Backup / Rollback ────────────────────────────────────────────────────────


def create_backups(paths):
    """Backup files before migration. Returns backup manifest."""
    backups = {}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for rel_path in paths:
        src = WORKSPACE_ROOT / rel_path
        if src.exists():
            dst = BACKUP_DIR / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            backups[rel_path] = str(dst)
    return backups


def restore_backups(backups):
    """Restore all backed-up files."""
    for rel_path, backup_path in backups.items():
        dst = WORKSPACE_ROOT / rel_path
        if Path(backup_path).exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, str(dst))
    cleanup_backups()


def cleanup_backups():
    """Remove backup directory."""
    if BACKUP_DIR.exists():
        shutil.rmtree(str(BACKUP_DIR))


# ── Main Migration Logic ─────────────────────────────────────────────────────


def apply_migration(manifest_path, dry_run=False):
    """Execute a single migration manifest. Returns result dict."""
    data = json.loads(manifest_path.read_text())
    mid = data["migration_id"]

    # Already applied?
    if mid in get_applied_ids():
        return {"status": "skipped", "migration_id": mid, "reason": "already applied"}

    # Check pre-conditions
    for cond in data.get("pre_conditions", []):
        passed, detail = evaluate_condition(cond)
        if not passed:
            return {"status": "skipped", "migration_id": mid,
                    "reason": f"pre-condition failed: {detail}"}

    # Create backups
    rollback_paths = data.get("rollback", {}).get("paths", [])
    backups = {} if dry_run else create_backups(rollback_paths)

    # Execute steps
    step_results = []
    for step in data["steps"]:
        result = execute_step(step, dry_run)
        step_results.append(result)

        if result["status"] == "failed":
            if not dry_run:
                restore_backups(backups)
            return {"status": "failed", "migration_id": mid,
                    "failed_step": step["id"], "error": result["error"],
                    "rolled_back": not dry_run, "step_results": step_results}

        # Per-step validation
        if "validate_after" in step and not dry_run:
            passed, detail = evaluate_condition(step["validate_after"])
            if not passed:
                restore_backups(backups)
                return {"status": "failed", "migration_id": mid,
                        "failed_step": step["id"],
                        "error": f"validate_after failed: {detail}",
                        "rolled_back": True, "step_results": step_results}

    # Post-conditions
    if not dry_run:
        post_results = []
        for cond in data.get("post_conditions", []):
            passed, detail = evaluate_condition(cond)
            post_results.append({"condition": cond["type"], "passed": passed, "detail": detail})

        if any(not r["passed"] for r in post_results):
            restore_backups(backups)
            return {"status": "failed", "migration_id": mid,
                    "post_conditions_failed": post_results,
                    "rolled_back": True, "step_results": step_results}

        # Record success
        state = load_state()
        state["applied"].append({
            "migration_id": mid,
            "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "steps": len(data["steps"]),
        })
        save_state(state)
        cleanup_backups()

    return {"status": "applied" if not dry_run else "dry_run",
            "migration_id": mid, "step_results": step_results}


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_check():
    pending = get_pending_manifests()
    summaries = []
    for f in pending:
        data = json.loads(f.read_text())
        summaries.append({
            "migration_id": data["migration_id"],
            "description": data.get("description", ""),
            "steps": len(data.get("steps", [])),
            "file": str(f.relative_to(WORKSPACE_ROOT)),
        })
    output_json({"pending": len(summaries), "migrations": summaries})


def cmd_apply(file_path, dry_run=False):
    path = Path(file_path)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    if not path.exists():
        fail(f"Migration file not found: {file_path}")
    result = apply_migration(path, dry_run=dry_run)
    output_json(result)


def cmd_apply_all(dry_run=False):
    pending = get_pending_manifests()
    if not pending:
        output_json({"status": "nothing_pending", "applied": 0})
        return

    results = []
    for f in pending:
        result = apply_migration(f, dry_run=dry_run)
        results.append(result)
        if result["status"] == "failed":
            break

    applied = sum(1 for r in results if r["status"] == "applied")
    failed = sum(1 for r in results if r["status"] == "failed")
    output_json({"applied": applied, "failed": failed,
                 "total": len(pending), "results": results})


def cmd_validate():
    """Re-run post-conditions for all applied migrations (drift detection)."""
    state = load_state()
    results = []
    for entry in state.get("applied", []):
        mid = entry["migration_id"]
        manifest_files = list(MIGRATIONS_DIR.glob(f"*{mid}*"))
        if not manifest_files:
            results.append({"migration_id": mid, "status": "manifest_missing"})
            continue
        data = json.loads(manifest_files[0].read_text())
        post_results = []
        for cond in data.get("post_conditions", []):
            passed, detail = evaluate_condition(cond)
            post_results.append({"condition": cond["type"], "passed": passed, "detail": detail})
        all_passed = all(r["passed"] for r in post_results)
        results.append({"migration_id": mid, "status": "valid" if all_passed else "drift",
                        "conditions": post_results})

    drifted = sum(1 for r in results if r["status"] == "drift")
    output_json({"total": len(results), "valid": len(results) - drifted,
                 "drifted": drifted, "results": results})


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--check", action="store_true",
                        help="List pending migrations")
    parser.add_argument("--apply", action="store_true",
                        help="Apply a migration (requires --file)")
    parser.add_argument("--apply-all", action="store_true",
                        help="Apply all pending migrations in order")
    parser.add_argument("--file", type=str, default=None,
                        help="Path to migration manifest file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing")
    parser.add_argument("--validate", action="store_true",
                        help="Re-run post-conditions for applied migrations (drift check)")
    args = parser.parse_args()

    if args.check:
        cmd_check()
    elif args.apply or (args.dry_run and args.file):
        if not args.file:
            fail("--apply requires --file <path>")
        cmd_apply(args.file, dry_run=args.dry_run)
    elif args.apply_all:
        cmd_apply_all(dry_run=args.dry_run)
    elif args.validate:
        cmd_validate()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
