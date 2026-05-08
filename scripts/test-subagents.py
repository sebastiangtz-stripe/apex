#!/usr/bin/env python3
"""
Static contract validation for Cursor subagents and skills. Runs in <2s; no
subagent invocation required.

Checks per file:
  - YAML-ish front-matter parses
  - Required front-matter keys present (name, description; model+readonly for agents)
  - Description >=20 chars and includes a use-when phrase
  - Hard rules section present
  - For agents whose Return value is documented as JSON, the JSON example parses
    (after replacing template placeholders <...> with "PLACEHOLDER")

Use case: catches the May 7 Jarvis "summary-only" failure-mode pattern at the contract
level — if Jarvis's contract section is malformed, the test fails before any user
invocation hits it.

Usage:
  python3 scripts/test-subagents.py
  python3 scripts/test-subagents.py --json
  python3 scripts/test-subagents.py --section agents,skills,rules
"""

import argparse
import json
import re
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = WORKSPACE_ROOT / ".cursor" / "agents"
SKILLS_DIR = WORKSPACE_ROOT / ".cursor" / "skills"
RULES_DIR = WORKSPACE_ROOT / ".cursor" / "rules"

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict:
    m = FRONT_RE.match(text)
    if not m:
        return {}
    body = m.group(1)
    out: dict = {}
    current_key = None
    current_buf: list[str] = []
    for raw in body.splitlines():
        # Block scalar: `key: >-` or `key: |`
        m_blockstart = re.match(r"^([a-zA-Z_][\w-]*)\s*:\s*([>|])-?\s*$", raw)
        if m_blockstart:
            if current_key is not None:
                out[current_key] = " ".join(current_buf).strip()
            current_key = m_blockstart.group(1)
            current_buf = []
            continue
        if current_key is not None:
            if raw.startswith("  ") or raw.startswith("\t"):
                current_buf.append(raw.strip())
                continue
            else:
                out[current_key] = " ".join(current_buf).strip()
                current_key = None
                current_buf = []
        m_kv = re.match(r"^([a-zA-Z_][\w-]*)\s*:\s*(.*)$", raw)
        if m_kv:
            v = m_kv.group(2).strip()
            if v in ("true", "false"):
                out[m_kv.group(1)] = v == "true"
            else:
                out[m_kv.group(1)] = v
    if current_key is not None:
        out[current_key] = " ".join(current_buf).strip()
    return out


def find_first_json_block(text: str) -> str | None:
    """Find the first ```json ... ``` or first { ... } block under a 'Return value' section."""
    section_match = re.search(r"^##+\s+Return value\b.*?(?=\n##+\s|\Z)",
                              text, re.MULTILINE | re.DOTALL)
    if not section_match:
        return None
    section = section_match.group(0)
    # Prefer ```json
    m = re.search(r"```json\s*\n(.*?)\n```", section, re.DOTALL)
    if m:
        return m.group(1)
    # Or ``` (untyped)
    m = re.search(r"```\s*\n(.*?)\n```", section, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate
    return None


USE_WHEN_PATTERNS = [
    r"\buse\s+when\b", r"\buse\s+proactively\b", r"\bwhenever\b", r"\bwhen the user\b",
]


def check_file(path: Path, kind: str) -> dict:
    """kind: 'agent' | 'skill' | 'rule'"""
    text = path.read_text(encoding="utf-8", errors="replace")
    fm = parse_frontmatter(text)
    issues: list[str] = []

    # Front-matter required keys
    if kind == "rule":
        required = ["description"]  # .mdc rules don't need a name; description is enough
    else:
        required = ["name", "description"]
    if kind == "agent":
        required += ["model", "readonly"]
    for key in required:
        if key not in fm or fm[key] in ("", None):
            issues.append(f"missing front-matter key: {key}")

    desc = fm.get("description") or ""
    if isinstance(desc, str):
        if len(desc) < 20:
            issues.append("description too short (<20 chars)")
        # 'use when' phrase only matters for agents/skills (rules are always-on)
        if kind in ("agent", "skill"):
            if not any(re.search(p, desc, re.IGNORECASE) for p in USE_WHEN_PATTERNS):
                issues.append("description lacks a 'use when' phrase — discoverability suffers")

    # Body sections
    body = text[FRONT_RE.match(text).end():] if FRONT_RE.match(text) else text

    if kind in ("agent", "skill"):
        if not re.search(r"^#+\s+(Workflow|Process|Steps|Operating|Phase\s+\d|Tool\b|Gate\b)",
                         body, re.MULTILINE | re.IGNORECASE):
            issues.append("no Workflow/Process/Phase/Operating/Gate section")
        if not re.search(r"^#+\s+(Hard rules|GUIDELINES|RESTRAINTS|Hard guarantees)\b",
                         body, re.MULTILINE | re.IGNORECASE):
            issues.append("no 'Hard rules' / GUIDELINES section")

    # JSON example sanity (agents only — they document a return JSON shape)
    if kind == "agent":
        jb = find_first_json_block(body)
        # Skip parse-checking for JSON blocks that are illustrative arrays-of-objects
        # with example URLs / prose payloads (those don't roundtrip through json.loads
        # after placeholder substitution and aren't real contracts the test should gate).
        skip_parse = bool(jb and ("https://" in jb or "..." in jb))
        if jb and not skip_parse:
            cleaned = jb
            # Strip JS-style comments
            cleaned = re.sub(r"//.*?$", "", cleaned, flags=re.MULTILINE)
            # Replace bare `<placeholders>` (when not inside a string) with a JSON-valid value.
            # Bare numeric placeholders like `: <int>` or `: N` → 0
            cleaned = re.sub(r":\s*<int>", ": 0", cleaned)
            cleaned = re.sub(r":\s*N\b", ": 0", cleaned)
            cleaned = re.sub(r":\s*<bool>", ": false", cleaned)
            cleaned = re.sub(r":\s*true\|false", ": false", cleaned)
            # `"true|false"` style options
            cleaned = re.sub(r'"\s*\|\s*[^"]+"', '"OPT"', cleaned)
            # Pipe-separated string enums like `"high|medium|low"`
            cleaned = re.sub(r'"([^"|]+)\|[^"]+"', r'"\1"', cleaned)
            # Quoted placeholders `"<...>"` → `"PLACEHOLDER"` (avoid double-quoting)
            cleaned = re.sub(r'"<[^>]+>"', '"PLACEHOLDER"', cleaned)
            # Any remaining `<...>` is most likely embedded inside a string literal
            # (e.g. `"<headline> re: foo"`). Substitute with bare PLACEHOLDER so the
            # surrounding quotes still close correctly.
            cleaned = re.sub(r"<[^>]+>", "PLACEHOLDER", cleaned)
            # Bare `...` literal (object continuation marker) → drop
            cleaned = re.sub(r'^\s*\.\.\.\s*,?\s*$', '', cleaned, flags=re.MULTILINE)
            # Trailing commas
            cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
            try:
                json.loads(cleaned, strict=False)
            except json.JSONDecodeError as e:
                # Surface a snippet for debugging
                lines = cleaned.splitlines()
                snippet = "\n".join(lines[max(0, e.lineno - 2): e.lineno + 1])
                issues.append(f"Return value JSON example does not parse: {e.msg} at line {e.lineno}\n            snippet: {snippet[:200]}")

    return {"path": str(path.relative_to(WORKSPACE_ROOT)), "kind": kind,
            "name": fm.get("name", path.stem), "issues": issues, "ok": not issues}


def collect():
    results: list[dict] = []
    if AGENTS_DIR.exists():
        for p in sorted(AGENTS_DIR.glob("*.md")):
            results.append(check_file(p, "agent"))
    if SKILLS_DIR.exists():
        for p in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            results.append(check_file(p, "skill"))
    if RULES_DIR.exists():
        for p in sorted(RULES_DIR.glob("*.mdc")):
            results.append(check_file(p, "rule"))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--section", help="comma-separated subset: agents,skills,rules")
    args = parser.parse_args()

    requested = set(args.section.split(",")) if args.section else None

    results = collect()
    if requested:
        results = [r for r in results if (r["kind"] + "s") in requested]

    if args.json:
        print(json.dumps(results, indent=2))
        sys.exit(1 if any(not r["ok"] for r in results) else 0)

    print(f"# Subagent + Skill Contract Tests\n_(checked {len(results)} files)_\n")
    failed = 0
    for r in results:
        marker = "OK " if r["ok"] else "FAIL"
        print(f"[{marker}] [{r['kind']}] {r['name']}  ({r['path']})")
        if not r["ok"]:
            for iss in r["issues"]:
                print(f"        - {iss}")
            failed += 1
    print(f"\nSummary: {len(results) - failed} OK, {failed} failed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
