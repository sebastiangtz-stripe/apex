#!/bin/bash
# Warn when scan-state.json has timestamps in the future or duplicates in logged_*_ids.
# Triggered on every save to projects/active/<slug>/scan-state.json.

input=$(cat)
file=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))" 2>/dev/null)

if [[ -z "$file" || ! -f "$file" ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

if [[ ! "$file" =~ projects/active/[^/]+/scan-state\.json$ ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

msg=$(python3 - "$file" <<'PY'
import json, sys, datetime
from collections import Counter
from pathlib import Path

p = Path(sys.argv[1])
try:
    data = json.loads(p.read_text())
except Exception as e:
    print(f"scan-state.json failed to parse: {e}")
    sys.exit(0)

now = datetime.datetime.now(datetime.timezone.utc)
warnings = []

for k in ("last_email_scan", "last_slack_scan"):
    v = data.get(k)
    if not v:
        continue
    try:
        ts = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        warnings.append(f"{k} is not a valid ISO timestamp: {v}")
        continue
    if ts > now + datetime.timedelta(hours=1):
        warnings.append(f"{k} is in the FUTURE ({v}) — clock issue or copy-paste mistake.")

for k in ("logged_email_ids", "logged_slack_thread_ids"):
    arr = data.get(k, []) or []
    if not isinstance(arr, list):
        warnings.append(f"{k} should be a list")
        continue
    counts = Counter(arr)
    dupes = [x for x, c in counts.items() if c > 1]
    if dupes:
        sample = dupes[:3]
        warnings.append(f"{k} has {len(dupes)} duplicate ID(s) (sample: {sample})")

if warnings:
    print("scan-state.json hygiene:\n- " + "\n- ".join(warnings))
PY
)

if [[ -z "$msg" ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

escaped=$(echo "$msg" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
echo "{\"additional_context\": ${escaped}}"
exit 0
