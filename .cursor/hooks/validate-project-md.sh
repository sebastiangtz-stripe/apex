#!/bin/bash
# Warn when PROJECT.md has Key Contacts populated but Email search is still TBD.
# Triggered on every save to projects/active/<slug>/PROJECT.md.

input=$(cat)
file=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))" 2>/dev/null)

if [[ -z "$file" || ! -f "$file" ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

# Only operate on PROJECT.md files
if [[ ! "$file" =~ projects/active/[^/]+/PROJECT\.md$ ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

warnings=$(python3 - "$file" <<'PY'
import re, sys, json, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text(errors="replace")

def field(name):
    m = re.search(rf"^\s*-\s*\*\*{re.escape(name)}\*\*\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    return (m.group(1).strip() if m else "")

email_search = field("Email search")
status = field("Status")
priority = field("Priority")

warnings = []

if email_search and "tbd" in email_search.lower():
    kc_match = re.search(r"## Key Contacts\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if kc_match:
        contact_lines = [l for l in kc_match.group(1).splitlines()
                         if l.strip().startswith("- ") and "@" in l]
        if contact_lines:
            warnings.append(f"Email search is TBD but {len(contact_lines)} Key Contacts populated. "
                            f"Update the query per CLAUDE.md Email Query Format (domain + name + specific-address).")

if not status:
    warnings.append("Status field is empty.")
if not priority:
    warnings.append("Priority field is empty.")

acct = field("Account ID(s)") or field("Account ID")
if (not acct or "TBD" in acct.upper()) and "account-manifest" not in text.lower():
    warnings.append("No Account ID(s) and no Account Manifest URL — add at least one for Stripe-side reference.")

print(json.dumps("\n- ".join(warnings)))
PY
)

# warnings is a JSON-encoded string; if empty string, exit silently
if [[ "$warnings" == '""' || -z "$warnings" ]]; then
  echo '{"additional_context": ""}'
  exit 0
fi

# Strip the outer JSON quotes for embedding into a message
msg=$(python3 -c "import sys,json; v=json.loads(sys.stdin.read()); print('PROJECT.md hygiene:\n- ' + v if v else '')" <<<"$warnings")

if [[ -n "$msg" ]]; then
  escaped=$(echo "$msg" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
  echo "{\"additional_context\": ${escaped}}"
else
  echo '{"additional_context": ""}'
fi
exit 0
