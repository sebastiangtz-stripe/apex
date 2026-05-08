# Runbook — Merging Duplicate Project Slugs

When `/drift-audit` (Section C) flags a slug collision, follow this protocol.

## Step 0: Confirm it's actually a duplicate

Before touching any files, validate:

1. **Same SFDC Opportunity ID?** Compare `External Links → Salesforce` in each `PROJECT.md`.
   Different opp IDs → these are **different deals** (possibly same merchant), NOT duplicates.
   In that case, exit this runbook and instead apply the "Related Projects" cross-reference
   pattern (see end of this doc).
2. **Same Stripe Account ID / Account Manifest URL?** Compare `Account ID(s)`. Different
   account IDs → different deals.
3. **Same Hubble project_id?** Compare `hubble.json` if both have it. Different IDs → not
   duplicates. Same ID under two slugs → CRITICAL drift, this runbook applies.
4. **Same Asana task GID?** Read both `asana.json`. Different task GIDs is normal; same
   task GID under two slugs is broken state from an earlier sync error.

If after validation steps 1-4 they ARE duplicates, proceed.

## Step 1: Choose the canonical slug

Prefer:
- The slug that matches the merchant's legal name (kebab-case)
- The slug whose `PROJECT.md` H1 most closely matches Hubble `project_name` after stripping
  brackets
- The slug with the populated `hubble.json`
- Tie-break: the slug with the longer `raw/comms.md` (more historical context)

Call the canonical slug `<canonical>` and the loser `<loser>` below.

## Step 2: Merge `raw/comms.md` chronologically

```
# In <canonical>/raw/comms.md, append all entries from <loser>/raw/comms.md
# in chronological order. Use the date headers (## [YYYY-MM-DD] — ...) as sort keys.
```

Manual: open both files, sort the `## [YYYY-MM-DD]` blocks chronologically, write the
merged file. Preserve every entry exactly. Add a one-line provenance note at the top:

```
> Merged from former slug `<loser>/raw/comms.md` on YYYY-MM-DD.
```

## Step 3: Merge `timeline.md`

Same chronological merge as Step 2 — sort by `## [YYYY-MM-DD]` headers.

## Step 4: Merge `action-items.md`

- Move every Open `[ ]` item from `<loser>/action-items.md` into `<canonical>/action-items.md`
  Open section. Preserve all metadata (tags, complexity, due, source).
- Move every Completed `[x]` item to the canonical Completed section.
- Dedup by exact description match (drop the loser's entry if the canonical has the same line).

## Step 5: Merge `scan-state.json`

Combine the two files:

```python
import json
canonical = json.load(open("projects/active/<canonical>/scan-state.json"))
loser     = json.load(open("projects/active/<loser>/scan-state.json"))

merged = {
  "last_email_scan": max(filter(None, [canonical.get("last_email_scan"), loser.get("last_email_scan")])),
  "last_slack_scan": max(filter(None, [canonical.get("last_slack_scan"), loser.get("last_slack_scan")])),
  "logged_email_ids": sorted(set((canonical.get("logged_email_ids") or []) + (loser.get("logged_email_ids") or []))),
  "logged_slack_thread_ids": sorted(set((canonical.get("logged_slack_thread_ids") or []) + (loser.get("logged_slack_thread_ids") or []))),
}
json.dump(merged, open("projects/active/<canonical>/scan-state.json", "w"), indent=2)
```

## Step 6: Merge `PROJECT.md`

- Keep the canonical H1 + Overview block.
- Merge Key Contacts (dedup by email/name).
- Merge `Email search` query (combine domains + names + addresses; dedup).
- Merge Slack channels and Stripe contacts.
- Merge Product Activation lists (dedup by product name; preserve any `[x]` that appears
  on either side).
- Merge Notes section.

## Step 7: Merge `drafts/`, `issues/`

```
mv projects/active/<loser>/drafts/* projects/active/<canonical>/drafts/
mv projects/active/<loser>/issues/* projects/active/<canonical>/issues/
```

Resolve filename collisions by prefixing the loser's files with `from-<loser>--`.

## Step 8: Merge `commitments.md` (if present)

Future: when commitments.md is in use, dedup by promise+date.

## Step 9: Reconcile Asana

Two possibilities:

**Case A — both slugs already have separate Asana tasks (most common)**

Choose the canonical Asana task GID (whichever has more subtasks / more recent activity):

1. Move every Open subtask from the loser's Asana task to the canonical task:
   - For each loser subtask: read its name, due_on, notes, custom fields.
   - Create a new subtask on the canonical task with the same fields:
     `POST /tasks/<canonical_gid>/subtasks`.
   - Multi-home to Action Items project + section + custom fields per the dual-write rules
     in CLAUDE.md.
   - Delete or complete the loser subtask: `PUT /tasks/<loser_subtask_gid>` with
     `{ completed: true }`.
   - Update `<canonical>/asana.json` `subtask_gids` map.
2. Add a final comment to the loser's Asana task: "Merged into task <canonical_gid>;
   archiving."
3. Complete the loser Asana task: `PUT /tasks/<loser_gid>` with `{ completed: true }`.
4. Update `<canonical>/asana.json` `task_gid` to the canonical's.

**Case B — one slug has Asana, the other doesn't**

Just delete the loser's `asana.json` after Step 7.

## Step 10: Move loser to archive (preserve as audit trail)

```
mv projects/active/<loser> projects/archive/<loser>--merged-into-<canonical>-YYYY-MM-DD
```

Add a single-line `MERGED.md` inside the archived folder:

```
# MERGED

This project was merged into `projects/active/<canonical>/` on YYYY-MM-DD.
Reason: duplicate of <canonical> (same SFDC Opportunity / Account ID / Hubble project_id).
```

## Step 11: Regenerate INDEX.md

```
python3 scripts/regenerate-index.py
```

## Step 12: Verify clean state

```
python3 scripts/drift-audit.py --section A,C
```

Should report 0 collisions and 0 archived-but-listed entries. If anything remains, debug.

---

## "Related Projects" cross-reference pattern (for non-duplicates flagged by the audit)

When two slugs are flagged but Step 0 confirms they are **distinct deals at the same merchant**
(different SFDC Opps, different Account IDs), do NOT merge. Instead add a cross-reference
to both PROJECT.md files so the slug-collision noise is explained on the next audit.

Insert this section into both `PROJECT.md` files just below the `## Overview` block:

```markdown
## Related Projects

This merchant has more than one Accelerate engagement. They are distinct deals — do not
merge.

- **<other-slug>** ([projects/active/<other-slug>/PROJECT.md](../<other-slug>/PROJECT.md)) —
  <one-line reason: e.g. "newer deal for 3PI Direct Distributors line, $39K AONR, kicked
  off 2026-03-26">
```

The `/drift-audit` skill will still surface the collision (deterministic detector), but
during human triage the cross-reference makes the answer immediate.
