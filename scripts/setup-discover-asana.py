#!/usr/bin/env python3
"""
Auto-discover Asana GIDs for the /setup skill.

Given an Asana Personal Access Token and the URLs (or GIDs) of the two
canonical boards — a main merchant board and an Action Items cross-project —
this script calls the Asana REST API to enumerate sections, custom fields,
and enum option GIDs, then prints or writes the .env lines.

Boards must follow the canonical structure documented in SETUP.md:
  Main board sections:        Received, [GREEN], [YELLOW], Completed, Terminated
  Main board fields:          "Active on Accelerate?" (single-select: YES, NO)
                              "ONR" (number)
                              "Products to activate" (multi-select: Payments, Billing,
                                Radar, Terminal, Connect, Invoicing, Tax, Sigma, Identity)
                              "Account Executive" (text)
                              "Activation Quarter" (single-select: Q2, Q3, Q4)
                              "GLD" (date)
                              "Is Platform?" (single-select: Yes, No)
                              "Status (Accelerate)" (multi-select: [GREEN], [YELLOW],
                                [RED], [LIVE], [TERMINATED], [COMPLETED])
                              "Acct_id" (text)
  Action Items sections:      Today, This Week, Later, Waiting
  Action Items fields:        Merchant (text), Action Tag (single-select: email, reply,
                                research, prep, schedule, track, log, waiting),
                              Complexity (single-select: LOW, MEDIUM, HIGH)

Usage:
  # Print .env lines to stdout (default — easy to redirect or inspect)
  python3 scripts/setup-discover-asana.py \\
      --pat <ASANA_PAT> --main <URL_OR_GID> --ai <URL_OR_GID>

  # Atomically update .env in place (preserves unrelated keys)
  python3 scripts/setup-discover-asana.py \\
      --pat-from-env --main <URL_OR_GID> --ai <URL_OR_GID> \\
      --write .env

The --pat-from-env flag reads ASANA_PAT from .env first, then the environment.
Useful when the /setup skill has already written the PAT and you don't want it
echoed on the command line.

Exit codes:
  0  clean
  1  auth failure (401), malformed URL, or other fatal error
  2  missing section / field / option in the live Asana boards (user needs to
     add it before re-running)
"""

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = WORKSPACE_ROOT / ".env"


# ── Canonical name → env-var mappings ─────────────────────────────────────────

MAIN_SECTIONS = {
    "Received":   "ASANA_SECTION_RECEIVED",
    "[GREEN]":    "ASANA_SECTION_GREEN",
    "[YELLOW]":   "ASANA_SECTION_YELLOW",
    "Completed":  "ASANA_SECTION_COMPLETED",
    "Terminated": "ASANA_SECTION_TERMINATED",
}

MAIN_FIELDS = {
    "Active on Accelerate?": {
        "field_env": "ASANA_FIELD_ACTIVE",
        "options": {
            "YES": "ASANA_FIELD_ACTIVE_YES",
            "NO":  "ASANA_FIELD_ACTIVE_NO",
        },
    },
    "ONR": {
        "field_env": "ASANA_FIELD_ONR",
    },
    "Products to activate": {
        "field_env": "ASANA_FIELD_PRODUCTS",
        "options": {
            "Payments":  "ASANA_FIELD_PRODUCT_PAYMENTS",
            "Billing":   "ASANA_FIELD_PRODUCT_BILLING",
            "Radar":     "ASANA_FIELD_PRODUCT_RADAR",
            "Terminal":  "ASANA_FIELD_PRODUCT_TERMINAL",
            "Connect":   "ASANA_FIELD_PRODUCT_CONNECT",
            "Invoicing": "ASANA_FIELD_PRODUCT_INVOICING",
            "Tax":       "ASANA_FIELD_PRODUCT_TAX",
            "Sigma":     "ASANA_FIELD_PRODUCT_SIGMA",
            "Identity":  "ASANA_FIELD_PRODUCT_IDENTITY",
        },
    },
    "Account Executive": {
        "field_env": "ASANA_FIELD_ACCOUNT_EXEC",
    },
    "Activation Quarter": {
        "field_env": "ASANA_FIELD_ACTIVATION_QUARTER",
        "options": {
            "Q2": "ASANA_FIELD_QUARTER_Q2",
            "Q3": "ASANA_FIELD_QUARTER_Q3",
            "Q4": "ASANA_FIELD_QUARTER_Q4",
        },
    },
    "GLD": {
        "field_env": "ASANA_FIELD_GLD",
    },
    "Is Platform?": {
        "field_env": "ASANA_FIELD_IS_PLATFORM",
        "options": {
            "Yes": "ASANA_FIELD_PLATFORM_YES",
            "No":  "ASANA_FIELD_PLATFORM_NO",
        },
    },
    "Status (Accelerate)": {
        "field_env": "ASANA_FIELD_STATUS",
        "options": {
            "[GREEN]":      "ASANA_FIELD_STATUS_GREEN",
            "[YELLOW]":     "ASANA_FIELD_STATUS_YELLOW",
            "[RED]":        "ASANA_FIELD_STATUS_RED",
            "[LIVE]":       "ASANA_FIELD_STATUS_LIVE",
            "[TERMINATED]": "ASANA_FIELD_STATUS_TERMINATED",
            "[COMPLETED]":  "ASANA_FIELD_STATUS_COMPLETED",
        },
    },
    "Acct_id": {
        "field_env": "ASANA_FIELD_ACCT_ID",
    },
}

AI_SECTIONS = {
    "Today":     "ASANA_AI_SECTION_TODAY",
    "This Week": "ASANA_AI_SECTION_THIS_WEEK",
    "Later":     "ASANA_AI_SECTION_LATER",
    "Waiting":   "ASANA_AI_SECTION_WAITING",
}

AI_FIELDS = {
    "Merchant": {"field_env": "ASANA_AI_FIELD_MERCHANT"},
    "Action Tag": {
        "field_env": "ASANA_AI_FIELD_TAG",
        "options": {
            "email":    "ASANA_AI_TAG_EMAIL",
            "reply":    "ASANA_AI_TAG_REPLY",
            "research": "ASANA_AI_TAG_RESEARCH",
            "prep":     "ASANA_AI_TAG_PREP",
            "schedule": "ASANA_AI_TAG_SCHEDULE",
            "track":    "ASANA_AI_TAG_TRACK",
            "log":      "ASANA_AI_TAG_LOG",
            "waiting":  "ASANA_AI_TAG_WAITING",
        },
    },
    "Complexity": {
        "field_env": "ASANA_AI_FIELD_COMPLEXITY",
        "options": {
            "LOW":    "ASANA_AI_COMPLEXITY_LOW",
            "MEDIUM": "ASANA_AI_COMPLEXITY_MEDIUM",
            "HIGH":   "ASANA_AI_COMPLEXITY_HIGH",
        },
    },
}


# ── Env loading ───────────────────────────────────────────────────────────────

def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


# ── Asana API ─────────────────────────────────────────────────────────────────

class AsanaError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def api_get(pat: str, path: str) -> dict:
    req = urllib.request.Request(
        f"https://app.asana.com/api/1.0{path}",
        headers={"Authorization": f"Bearer {pat}"},
        method="GET",
    )
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read()).get("data")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise AsanaError(e.code, f"HTTP {e.code} on GET {path}: {body}")


# ── URL / GID parsing ─────────────────────────────────────────────────────────

GID_RE = re.compile(r"^\d{6,}$")
# New format: /1/<workspace_gid>/project/<project_gid>/list/...
URL_PROJECT_RE = re.compile(r"app\.asana\.com/\d+/\d+/project/(\d{6,})")
# Old format: /0/<project_gid>/list
URL_GID_RE = re.compile(r"app\.asana\.com/\d+/(\d{6,})")
# Workspace GID from new format: /1/<workspace_gid>/project/...
URL_WORKSPACE_RE = re.compile(r"app\.asana\.com/\d+/(\d{6,})/project/")


def parse_project_ref(ref: str) -> str:
    """Accept either a raw project GID or an Asana URL and return the project GID."""
    ref = ref.strip()
    if GID_RE.match(ref):
        return ref
    # Try new format first: /1/<workspace>/project/<project_gid>/...
    m = URL_PROJECT_RE.search(ref)
    if m:
        return m.group(1)
    # Fall back to old format: /0/<project_gid>/list
    m = URL_GID_RE.search(ref)
    if m:
        return m.group(1)
    raise ValueError(
        f"Could not parse Asana project GID from {ref!r}. "
        f"Expected a numeric GID or an URL like "
        f"https://app.asana.com/1/<WORKSPACE>/project/<PROJECT_GID>/list/... or "
        f"https://app.asana.com/0/<PROJECT_GID>/list."
    )


def parse_workspace_from_url(ref: str) -> Optional[str]:
    """Extract workspace GID from a new-format Asana URL. Returns None if not present."""
    m = URL_WORKSPACE_RE.search(ref)
    return m.group(1) if m else None


# ── Name normalization for tolerant matching ──────────────────────────────────

def norm(s: str) -> str:
    """Lowercase, strip punctuation/whitespace — for matching Asana labels."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def find_by_name(items: list, target: str, name_key: str = "name"):
    """Return the first item whose normalized name matches target. None if missing."""
    nt = norm(target)
    for it in items or []:
        if norm(it.get(name_key, "")) == nt:
            return it
    return None


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover_workspace(pat: str) -> tuple[str, list[str]]:
    """Return (workspace_gid, [workspace_names]). Errors if no workspaces."""
    me = api_get(pat, "/users/me")
    workspaces = me.get("workspaces") or []
    if not workspaces:
        raise AsanaError(0, "PAT is valid but has no accessible workspaces.")
    return workspaces[0]["gid"], [w["name"] for w in workspaces]


def discover_project(pat: str, project_gid: str, section_map: dict, field_map: dict,
                     errors: list) -> dict:
    """Return {env_var: gid} for one project. Appends human-readable errors."""
    out = {}

    sections = api_get(pat, f"/projects/{project_gid}/sections") or []
    for label, env_var in section_map.items():
        sec = find_by_name(sections, label)
        if sec is None:
            errors.append(
                f"  - Project {project_gid}: missing section {label!r}. "
                f"Add it in Asana (sections found: "
                f"{[s.get('name') for s in sections] or 'none'})."
            )
            continue
        out[env_var] = sec["gid"]

    cf_settings = api_get(pat, f"/projects/{project_gid}/custom_field_settings") or []
    fields = [cfs.get("custom_field") or {} for cfs in cf_settings]
    for label, spec in field_map.items():
        field = find_by_name(fields, label)
        if field is None:
            errors.append(
                f"  - Project {project_gid}: missing custom field {label!r}. "
                f"Add it on the project (fields found: "
                f"{[f.get('name') for f in fields] or 'none'})."
            )
            continue
        out[spec["field_env"]] = field["gid"]

        opt_spec = spec.get("options") or {}
        if opt_spec:
            enum_opts = field.get("enum_options") or []
            for opt_label, opt_env in opt_spec.items():
                opt = find_by_name(enum_opts, opt_label)
                if opt is None:
                    errors.append(
                        f"  - Project {project_gid}, field {label!r}: missing option "
                        f"{opt_label!r}. Add it in Asana (options found: "
                        f"{[o.get('name') for o in enum_opts] or 'none'})."
                    )
                    continue
                out[opt_env] = opt["gid"]

    return out


# ── .env writing ──────────────────────────────────────────────────────────────

def render_env_lines(values: dict) -> str:
    """Emit values as KEY=VALUE lines in a stable canonical order."""
    canonical_order = [
        # Workspace
        "ASANA_WORKSPACE_GID",
        "ASANA_PROJECT_GID",
        # Main board sections
        "ASANA_SECTION_RECEIVED",
        "ASANA_SECTION_GREEN",
        "ASANA_SECTION_YELLOW",
        "ASANA_SECTION_COMPLETED",
        "ASANA_SECTION_TERMINATED",
        # Main board fields
        "ASANA_FIELD_ACTIVE",
        "ASANA_FIELD_ACTIVE_YES",
        "ASANA_FIELD_ACTIVE_NO",
        "ASANA_FIELD_ONR",
        "ASANA_FIELD_PRODUCTS",
        "ASANA_FIELD_PRODUCT_PAYMENTS",
        "ASANA_FIELD_PRODUCT_BILLING",
        "ASANA_FIELD_PRODUCT_RADAR",
        "ASANA_FIELD_PRODUCT_TERMINAL",
        "ASANA_FIELD_PRODUCT_CONNECT",
        "ASANA_FIELD_PRODUCT_INVOICING",
        "ASANA_FIELD_PRODUCT_TAX",
        "ASANA_FIELD_PRODUCT_SIGMA",
        "ASANA_FIELD_PRODUCT_IDENTITY",
        "ASANA_FIELD_ACCOUNT_EXEC",
        "ASANA_FIELD_ACTIVATION_QUARTER",
        "ASANA_FIELD_QUARTER_Q2",
        "ASANA_FIELD_QUARTER_Q3",
        "ASANA_FIELD_QUARTER_Q4",
        "ASANA_FIELD_GLD",
        "ASANA_FIELD_IS_PLATFORM",
        "ASANA_FIELD_PLATFORM_YES",
        "ASANA_FIELD_PLATFORM_NO",
        "ASANA_FIELD_STATUS",
        "ASANA_FIELD_STATUS_GREEN",
        "ASANA_FIELD_STATUS_YELLOW",
        "ASANA_FIELD_STATUS_RED",
        "ASANA_FIELD_STATUS_LIVE",
        "ASANA_FIELD_STATUS_TERMINATED",
        "ASANA_FIELD_STATUS_COMPLETED",
        "ASANA_FIELD_ACCT_ID",
        # Action Items board
        "ASANA_AI_PROJECT_GID",
        "ASANA_AI_SECTION_TODAY",
        "ASANA_AI_SECTION_THIS_WEEK",
        "ASANA_AI_SECTION_LATER",
        "ASANA_AI_SECTION_WAITING",
        "ASANA_AI_FIELD_MERCHANT",
        "ASANA_AI_FIELD_TAG",
        "ASANA_AI_TAG_EMAIL",
        "ASANA_AI_TAG_REPLY",
        "ASANA_AI_TAG_RESEARCH",
        "ASANA_AI_TAG_PREP",
        "ASANA_AI_TAG_SCHEDULE",
        "ASANA_AI_TAG_TRACK",
        "ASANA_AI_TAG_LOG",
        "ASANA_AI_TAG_WAITING",
        "ASANA_AI_FIELD_COMPLEXITY",
        "ASANA_AI_COMPLEXITY_LOW",
        "ASANA_AI_COMPLEXITY_MEDIUM",
        "ASANA_AI_COMPLEXITY_HIGH",
    ]
    lines = []
    for key in canonical_order:
        if key in values:
            lines.append(f"{key}={values[key]}")
    leftover = sorted(k for k in values if k not in canonical_order)
    for key in leftover:
        lines.append(f"{key}={values[key]}")
    return "\n".join(lines) + "\n"


def write_env(path: Path, values: dict) -> None:
    """Atomically merge `values` into the .env at `path`.

    Existing keys are replaced in place; new keys are appended. Comments and
    blank lines are preserved. If the file doesn't exist, it is created with
    `values` only.
    """
    existing_lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in values:
                new_lines.append(f"{k}={values[k]}")
                seen.add(k)
                continue
        new_lines.append(line)
    appended = [k for k in values if k not in seen]
    if appended:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append("# ── Asana GIDs (discovered by setup-discover-asana.py) ──")
        for k in appended:
            new_lines.append(f"{k}={values[k]}")

    content = "\n".join(new_lines)
    if not content.endswith("\n"):
        content += "\n"

    # atomic write
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pat_group = parser.add_mutually_exclusive_group(required=True)
    pat_group.add_argument("--pat", help="Asana Personal Access Token (do not commit).")
    pat_group.add_argument(
        "--pat-from-env",
        action="store_true",
        help="Read ASANA_PAT from .env (then from the environment). Avoids leaking the token on the CLI.",
    )
    parser.add_argument("--main", required=True, help="Main board URL or GID.")
    parser.add_argument("--ai", required=True, help="Action Items board URL or GID.")
    parser.add_argument(
        "--write",
        help="Path to .env file to update in place. If omitted, prints lines to stdout.",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_FILE),
        help=f"Source .env when --pat-from-env is used. Default: {ENV_FILE}",
    )
    args = parser.parse_args()

    if args.pat_from_env:
        env = load_env(Path(args.env_file))
        pat = env.get("ASANA_PAT") or os.environ.get("ASANA_PAT", "")
        if not pat or pat.startswith("REPLACE"):
            print("ERROR: ASANA_PAT not found in .env or environment.", file=sys.stderr)
            sys.exit(1)
    else:
        pat = args.pat

    try:
        main_gid = parse_project_ref(args.main)
        ai_gid = parse_project_ref(args.ai)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Try to extract workspace GID from the URL before hitting the API
    url_workspace = parse_workspace_from_url(args.main) or parse_workspace_from_url(args.ai)

    try:
        workspace_gid, workspace_names = discover_workspace(pat)
    except AsanaError as e:
        if e.code == 401:
            print("ERROR: Asana PAT rejected (401). Generate a new one at app.asana.com/0/my-apps.", file=sys.stderr)
            sys.exit(1)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Prefer workspace GID from URL if available (avoids relying on API order)
    if url_workspace:
        workspace_gid = url_workspace

    values = {
        "ASANA_WORKSPACE_GID": workspace_gid,
        "ASANA_PROJECT_GID":   main_gid,
        "ASANA_AI_PROJECT_GID": ai_gid,
    }
    errors: list[str] = []

    try:
        values.update(discover_project(pat, main_gid, MAIN_SECTIONS, MAIN_FIELDS, errors))
        values.update(discover_project(pat, ai_gid, AI_SECTIONS, AI_FIELDS, errors))
    except AsanaError as e:
        if e.code == 401:
            print("ERROR: Asana PAT rejected mid-discovery (401).", file=sys.stderr)
            sys.exit(1)
        if e.code in (403, 404):
            print(f"ERROR: {e}. Confirm the board URLs and that the PAT can see them.", file=sys.stderr)
            sys.exit(1)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.write:
        target = Path(args.write)
        write_env(target, values)
        print(f"Wrote {len(values)} keys to {target} (workspace: {workspace_names[0]}).")
    else:
        sys.stdout.write(render_env_lines(values))
        print(f"# Discovered {len(values)} keys (workspace: {workspace_names[0]}).", file=sys.stderr)

    if errors:
        print("\nDiscovery completed with missing pieces:", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
        print(
            "\nAll found keys have been written. Fix the issues above in Asana,\n"
            "then re-run setup-discover-asana.py to fill in the remaining keys.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
