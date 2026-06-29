#!/usr/bin/env python3
"""Remove the bundled example/demo projects from a live workspace.

The apex template ships two demo merchants under `projects/active/` —
`example-merchant` (also a fixture for `tests/smoke.py`) and `acme-corp` — so a
freshly cloned workspace has something to look at. Once a real consultant
finishes `/setup`, those fakes should be gone.

This deletes ONLY the hardcoded demo slugs below — it takes no slug argument and
can never be pointed at a real project — then regenerates INDEX.md. Idempotent:
re-running after removal is a no-op.

MUST run AFTER the setup smoke test (Phase 5), which uses `example-merchant` as
a fixture. The setup skill invokes it in Phase 6.

Usage:
  python3 scripts/remove-example-projects.py            # remove + regenerate index
  python3 scripts/remove-example-projects.py --dry-run  # show what would be removed
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
ACTIVE = WORKSPACE / "projects" / "active"

# The ONLY folders this script may delete. Bundled template demo data.
EXAMPLE_SLUGS = ["example-merchant", "acme-corp"]


def main():
    ap = argparse.ArgumentParser(description="Remove bundled example projects.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be removed without deleting.")
    args = ap.parse_args()

    present = [s for s in EXAMPLE_SLUGS
               if (ACTIVE / s).exists() and (ACTIVE / s).is_dir()]

    if args.dry_run:
        print(json.dumps({"dry_run": True, "would_remove": present}))
        return

    removed = []
    for slug in present:
        shutil.rmtree(ACTIVE / slug)
        removed.append(slug)

    # Keep INDEX.md honest after any removal.
    if removed:
        idx = WORKSPACE / "scripts" / "regenerate-index.py"
        if idx.exists():
            subprocess.run([sys.executable, str(idx)], cwd=str(WORKSPACE),
                           check=False)

    remaining = [s for s in EXAMPLE_SLUGS if (ACTIVE / s).exists()]
    print(json.dumps({"removed": removed, "remaining_examples": remaining}))


if __name__ == "__main__":
    main()
