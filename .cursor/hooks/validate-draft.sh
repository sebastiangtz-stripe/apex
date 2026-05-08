#!/bin/bash
# Warn when editing a draft >14 days old that has no `## Sent` section populated.
# Triggered on save to projects/active/<slug>/drafts/*.md.

input=$(cat)
file=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))" 2>/dev/null)

if [[ -z "$file" || ! -f "$file" ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

if [[ ! "$file" =~ projects/active/[^/]+/drafts/.+\.md$ ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

msg=$(python3 - "$file" <<'PY'
import os, re, sys, datetime
from pathlib import Path

p = Path(sys.argv[1])
mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
age_days = (datetime.datetime.now() - mtime).days

text = p.read_text(errors="replace")

# Sent section present AND non-empty?
m = re.search(r"^##\s+Sent\b", text, re.MULTILINE)
if m:
    body = text[m.end():]
    next_h2 = re.search(r"^##\s+", body, re.MULTILINE)
    section = body[: next_h2.start()] if next_h2 else body
    if section.strip():
        sys.exit(0)  # has Sent content; nothing to warn

# Only warn if the draft is OLDER than 14 days
if age_days < 14:
    sys.exit(0)

slug = p.parts[p.parts.index("active") + 1] if "active" in p.parts else "<slug>"
print(f"Draft hygiene: `{p.name}` (slug `{slug}`) is {age_days}d old without a populated `## Sent` section. "
      f"If sent, add `## Sent` with date + recipients + alias. If abandoned, archive or delete.")
PY
)

if [[ -z "$msg" ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

escaped=$(echo "$msg" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
echo "{\"additional_context\": ${escaped}}"
exit 0
