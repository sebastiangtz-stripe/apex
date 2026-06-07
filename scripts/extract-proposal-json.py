#!/usr/bin/env python3
"""
Extract a structured proposal JSON from comms-analyst output text.

Comms-analyst subagents return JSON inside commentary text (markdown fenced
blocks). This script extracts, validates, and outputs the cleanest JSON.

Usage:
  python3 scripts/extract-proposal-json.py --file path/to/output.txt
  python3 scripts/extract-proposal-json.py --text "..."
  cat output.txt | python3 scripts/extract-proposal-json.py

Options:
  --output <path>   Write result to file instead of stdout
  --slug <slug>     Override slug for error context when input lacks it

Output: clean JSON to stdout on success; error JSON to stderr + exit 1 on failure.
"""

import argparse
import json
import re
import sys
from pathlib import Path

FENCE_RE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
REQUIRED_KEY = "slug"
EXPECTED_KEYS = {"slug", "task_gid", "auto_close", "new_items", "headline",
                 "asana_comments", "timeline_summaries", "waiting_on_merchant",
                 "commitments", "dedupe_skipped", "inline_gaps"}


def extract_json_blocks(text: str) -> list[str]:
    return FENCE_RE.findall(text)


def validate_proposal(obj: dict) -> bool:
    return isinstance(obj, dict) and bool(obj.get(REQUIRED_KEY))


def score_proposal(obj: dict) -> int:
    return sum(1 for k in EXPECTED_KEYS if obj.get(k) is not None)


def extract(text: str) -> dict:
    blocks = extract_json_blocks(text)
    if not blocks:
        raise ValueError("No ```json fenced blocks found in input")

    candidates = []
    for block in blocks:
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if validate_proposal(obj):
            candidates.append(obj)

    if not candidates:
        raise ValueError(
            f"Found {len(blocks)} JSON block(s) but none contain a valid "
            f"proposal (missing '{REQUIRED_KEY}' key)"
        )

    candidates.sort(key=score_proposal, reverse=True)
    return candidates[0]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--text", type=str, help="Raw text to extract from")
    ap.add_argument("--file", type=str, help="File path to read text from")
    ap.add_argument("--output", type=str, help="Output file path (default: stdout)")
    ap.add_argument("--slug", type=str, help="Slug hint for error reporting")
    args = ap.parse_args()

    if args.text:
        text = args.text
    elif args.file:
        text = Path(args.file).read_text()
    else:
        text = sys.stdin.read()

    if not text.strip():
        error = {"error": "empty input", "slug": args.slug}
        print(json.dumps(error), file=sys.stderr)
        sys.exit(1)

    try:
        result = extract(text)
    except ValueError as e:
        slug_match = re.search(r'"slug"\s*:\s*"([^"]+)"', text)
        slug = args.slug or (slug_match.group(1) if slug_match else None)
        error = {"error": str(e), "slug": slug}
        print(json.dumps(error), file=sys.stderr)
        sys.exit(1)

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output + "\n")
    else:
        print(output)


if __name__ == "__main__":
    main()
