#!/bin/bash
input=$(cat)
file=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))" 2>/dev/null)

if [[ -z "$file" || ! -f "$file" ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

warnings=""
prev_date=""

while IFS= read -r line; do
  if [[ "$line" =~ ^##\ \[?([0-9]{4}-[0-9]{2}-[0-9]{2})\]? ]]; then
    current_date="${BASH_REMATCH[1]}"
    if [[ -n "$prev_date" && "$current_date" > "$prev_date" ]]; then
      warnings="${warnings}\n- Non-chronological: $current_date appears after $prev_date (entries should be newest-first)"
    fi
    prev_date="$current_date"
  fi
done < "$file"

if [[ -n "$warnings" ]]; then
  msg="Timeline validation warnings:${warnings}"
  escaped=$(echo "$msg" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
  echo "{\"additional_context\": ${escaped}}"
else
  echo '{"additional_context": ""}'
fi
exit 0
