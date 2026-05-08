#!/bin/bash
input=$(cat)
file=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))" 2>/dev/null)

if [[ -z "$file" || ! -f "$file" ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

warnings=""
has_completion=false

while IFS= read -r line; do
  if [[ "$line" =~ ^-\ \[.\]\ .+ ]]; then
    if [[ ! "$line" =~ \#[a-z]+ ]]; then
      warnings="${warnings}\n- Missing #tag: ${line:0:80}"
    fi

    if [[ "$line" =~ \[\ \] ]] && [[ ! "$line" =~ Due:\ [0-9]{4}-[0-9]{2}-[0-9]{2} ]] && [[ ! "$line" =~ Due:\ ASAP ]]; then
      warnings="${warnings}\n- Missing due date: ${line:0:80}"
    fi

    if [[ "$line" =~ \[[xX]\] ]]; then
      has_completion=true
    fi
  fi
done < "$file"

asana_reminder=""
if $has_completion; then
  asana_reminder="\n\nDUAL-WRITE CHECK: This file contains completed items [x]. Did you also complete the matching Asana subtask? Read asana.json for the task GID and PUT /tasks/{subtask_gid} with completed: true. Both sides must be updated."
fi

if [[ -n "$warnings" || -n "$asana_reminder" ]]; then
  msg="Action item validation:${warnings}${asana_reminder}"
  escaped=$(echo "$msg" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
  echo "{\"additional_context\": ${escaped}}"
else
  echo '{"additional_context": ""}'
fi
exit 0
