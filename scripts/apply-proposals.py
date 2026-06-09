#!/usr/bin/env python3
"""
Apply persisted comms-analyst proposals to Asana + local files.

This is the durable replacement for the inline dual-write that previously
happened in the LLM main thread (and was lost on 2026-05-12 when ~44 items
never landed in Asana). The LLM's job is now to produce structured
proposals (via /comms-analyst); this script's job is to apply them
reliably with retry, idempotency, and atomic per-item state.

Pipeline:
  /comms-analyst -> data/scan-proposals/<slug>-<YYYY-MM-DD>.json
                    -> apply-proposals.py
                    -> Asana + action-items.md + PROJECT.md + dual-write-log.md
                    -> data/scan-proposals/applied/<slug>-<YYYY-MM-DD>.json
                       (suffixed .applied.<ts>.json on success)

Usage:
  python3 scripts/apply-proposals.py --resume                  # apply all pending in data/scan-proposals/
  python3 scripts/apply-proposals.py --slug example-merchant               # apply just one merchant's pending file(s)
  python3 scripts/apply-proposals.py --file path/to/file.json  # apply a specific proposals file
  python3 scripts/apply-proposals.py --dry-run                 # show what would change, write nothing
  python3 scripts/apply-proposals.py --max-age-days 7          # skip proposals older than N days (default 7)

Idempotency:
  - Pre-flight per merchant: GET /tasks/{parent_gid}/subtasks once, build a
    normalized name set. Skips creates whose normalized description already
    exists (matches existing subtask name).
  - Per-item apply_status persisted to the proposals file before moving on,
    so re-running this script picks up where the previous run left off.
  - Auto-close PUTs are idempotent on the Asana side (completed:true is fine
    to set on an already-completed task).

Retry:
  - HTTP 429: honor Retry-After header, sleep, retry up to 5 attempts.
  - HTTP 5xx: exponential backoff (1, 2, 4, 8, 16s) up to 5 attempts.
  - HTTP 4xx other than 429: fail immediately, mark item pending_retry.

Auto-close confidence policy (Phase 5):
  - Only `confidence: high` auto-closes are applied automatically.
  - `confidence: medium` and `confidence: low` are routed to needs_human_review
    in the run report and not applied. Surface them in chat for explicit
    yes/no rather than letting the analyst silently close work.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = WORKSPACE_ROOT / "projects" / "active"
PROPOSALS_DIR = WORKSPACE_ROOT / "data" / "scan-proposals"
APPLIED_DIR = PROPOSALS_DIR / "applied"
DUAL_WRITE_LOG = WORKSPACE_ROOT / "data" / "runbooks" / "dual-write-log.md"
ENV_FILE = WORKSPACE_ROOT / ".env"


# ── Config ───────────────────────────────────────────────────────────────

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
PAT = ENV.get("ASANA_PAT", "")

AI_PROJECT = ENV.get("ASANA_AI_PROJECT_GID", "")
AI_SECTIONS = {
    "today":     ENV.get("ASANA_AI_SECTION_TODAY", ""),
    "this_week": ENV.get("ASANA_AI_SECTION_THIS_WEEK", ""),
    "later":     ENV.get("ASANA_AI_SECTION_LATER", ""),
    "waiting":   ENV.get("ASANA_AI_SECTION_WAITING", ""),
}
AI_FIELD_MERCHANT   = ENV.get("ASANA_AI_FIELD_MERCHANT", "")
AI_FIELD_TAG        = ENV.get("ASANA_AI_FIELD_TAG", "")
AI_FIELD_COMPLEXITY = ENV.get("ASANA_AI_FIELD_COMPLEXITY", "")
AI_COMPLEXITY = {
    "low":    ENV.get("ASANA_AI_COMPLEXITY_LOW", ""),
    "medium": ENV.get("ASANA_AI_COMPLEXITY_MEDIUM", ""),
    "high":   ENV.get("ASANA_AI_COMPLEXITY_HIGH", ""),
}
AI_TAG_OPTIONS = {
    "email":    ENV.get("ASANA_AI_TAG_EMAIL", ""),
    "reply":    ENV.get("ASANA_AI_TAG_REPLY", ""),
    "research": ENV.get("ASANA_AI_TAG_RESEARCH", ""),
    "prep":     ENV.get("ASANA_AI_TAG_PREP", ""),
    "schedule": ENV.get("ASANA_AI_TAG_SCHEDULE", ""),
    "track":    ENV.get("ASANA_AI_TAG_TRACK", ""),
    "log":      ENV.get("ASANA_AI_TAG_LOG", ""),
    "waiting":  ENV.get("ASANA_AI_TAG_WAITING", ""),
}


# ── API helper with retry + backoff ──────────────────────────────────────

class AsanaAPIError(Exception):
    """Raised when a call exhausts retries or hits an unrecoverable 4xx."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def api(method, path, data=None, max_attempts=5):
    """Make an Asana API call with retry + backoff.

    - 429: honor Retry-After (default 30s), retry up to max_attempts.
    - 5xx: exponential backoff (1, 2, 4, 8, 16s).
    - 4xx other than 429: raise AsanaAPIError immediately.
    - Network errors (URLError): treated like 5xx.
    """
    body = json.dumps(data).encode() if data else None
    url = f"https://app.asana.com/api/1.0{path}"
    last_err = None

    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {PAT}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            if resp.status == 204:
                return {}
            payload = json.loads(resp.read())
            if isinstance(payload, dict) and "data" in payload:
                return payload["data"]
            return payload
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode()[:300]
            except Exception:
                pass
            last_err = AsanaAPIError(
                f"{method} {path}: HTTP {e.code} {err_body}", status=e.code
            )
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", "30"))
                print(
                    f"    [retry] 429 rate limited; sleeping {retry_after}s"
                    f" (attempt {attempt}/{max_attempts})"
                )
                time.sleep(retry_after)
                continue
            if 500 <= e.code < 600:
                wait = 2 ** (attempt - 1)
                print(
                    f"    [retry] {e.code} server error; sleeping {wait}s"
                    f" (attempt {attempt}/{max_attempts})"
                )
                time.sleep(wait)
                continue
            raise last_err
        except urllib.error.URLError as e:
            wait = 2 ** (attempt - 1)
            print(
                f"    [retry] network error {e}; sleeping {wait}s"
                f" (attempt {attempt}/{max_attempts})"
            )
            last_err = AsanaAPIError(f"{method} {path}: network error {e}")
            time.sleep(wait)
            continue

    raise last_err or AsanaAPIError(f"{method} {path}: exhausted retries")


# ── Normalization + dedup ────────────────────────────────────────────────

def normalize(text):
    """Normalize a description for fuzzy comparison."""
    if not text:
        return ""
    s = re.sub(r"#\w+\s*", "", text)
    s = re.sub(r"\s*—\s*(Complexity|Owner|Due|Source|Completed):.*", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    return s


def fuzzy_match(a, b, threshold=0.6):
    if not a or not b:
        return False
    if a == b:
        return True
    aw, bw = set(a.split()), set(b.split())
    if not aw or not bw:
        return False
    overlap = aw & bw
    smaller = min(len(aw), len(bw))
    return len(overlap) / smaller >= threshold


# ── Action-items.md helpers ──────────────────────────────────────────────

OPEN_ITEM_RE = re.compile(r"^- \[([ xX])\] (.+)$")


def read_action_items(path):
    """Return (open_items, lines, header_text) from an action-items.md file.

    open_items: list of {"raw": str, "norm": str, "line_idx": int} for open ([ ]) items only.
    lines: full file split by lines (no \\n).
    """
    if not path.exists():
        return [], [], ""
    text = path.read_text()
    lines = text.splitlines()
    open_items = []
    section = ""
    for i, line in enumerate(lines):
        if re.match(r"^## Open", line, re.I):
            section = "open"
            continue
        if re.match(r"^## (Completed|Done|Waiting)", line, re.I):
            section = "other"
            continue
        m = OPEN_ITEM_RE.match(line)
        if m and section == "open" and m.group(1) == " ":
            raw = m.group(2)
            open_items.append({"raw": raw, "norm": normalize(raw), "line_idx": i})
    return open_items, lines, text


def append_open_action_item(path, raw_line):
    """Append a new `- [ ] <raw_line>` to the bottom of the `## Open` section.

    Inserts before the next `## ` heading (Completed/Waiting/etc) or at end of file.
    Idempotent: if a fuzzy match already exists in the open section, returns False.
    """
    open_items, lines, _ = read_action_items(path)
    target_norm = normalize(raw_line)
    for item in open_items:
        if fuzzy_match(item["norm"], target_norm):
            return False  # already present

    open_section_start = -1
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"^## Open", line, re.I):
            open_section_start = i
            insert_at = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^## ", lines[j]):
                    insert_at = j
                    break
            break

    if open_section_start == -1:
        # No `## Open` section; create one at top after header
        new_lines = lines + ["", "## Open", f"- [ ] {raw_line}"]
    else:
        # Walk back from insert_at to skip trailing blank lines, so we
        # insert directly after the last item rather than after blank padding.
        while insert_at > open_section_start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        new_lines = lines[:insert_at] + [f"- [ ] {raw_line}"] + lines[insert_at:]

    path.write_text("\n".join(new_lines) + ("\n" if not new_lines or new_lines[-1] != "" else ""))
    return True


def mark_action_item_complete(path, fuzzy_target_norm, completion_note=""):
    """Mark an open item matching the normalized target as `[x]` and optionally append note."""
    if not path.exists():
        return False
    text = path.read_text()
    lines = text.splitlines()
    section = ""
    changed = False
    for i, line in enumerate(lines):
        if re.match(r"^## Open", line, re.I):
            section = "open"
            continue
        if re.match(r"^## (Completed|Done|Waiting)", line, re.I):
            section = "other"
            continue
        m = OPEN_ITEM_RE.match(line)
        if not (m and section == "open" and m.group(1) == " "):
            continue
        raw = m.group(2)
        if fuzzy_match(normalize(raw), fuzzy_target_norm):
            new_line = f"- [x] {raw}"
            if completion_note and " — Completed:" not in raw:
                new_line += f" — Completed: {completion_note}"
            lines[i] = new_line
            changed = True
            break
    if changed:
        path.write_text("\n".join(lines) + "\n")
    return changed


# ── PROJECT.md inline-gap patcher ────────────────────────────────────────

def patch_project_contact(path, detail):
    """Patch the `**Stripe contacts**:` line in PROJECT.md to include `detail`.

    - If the current value is empty or `TBD`, replace it.
    - Otherwise, append `; <detail>` (semicolon-separated, matching existing format).
    - Returns True if patched, False if already present (idempotent fuzzy match).
    """
    if not path.exists():
        return False
    text = path.read_text()
    lines = text.splitlines()
    norm_detail = normalize(detail)

    # Look for the Stripe contacts line under ## Communication
    contacts_re = re.compile(r"^(- \*\*Stripe contacts\*\*:)\s*(.*)$")
    in_communication = False
    for i, line in enumerate(lines):
        if re.match(r"^## Communication", line, re.I):
            in_communication = True
            continue
        if in_communication and re.match(r"^## ", line):
            in_communication = False
        if not in_communication:
            continue
        m = contacts_re.match(line)
        if not m:
            continue
        prefix, current = m.group(1), m.group(2).strip()
        # Idempotency: if normalized detail already appears in the current value, skip.
        if normalize(current) and norm_detail and norm_detail in normalize(current):
            return False
        if not current or current.upper() == "TBD":
            new_line = f"{prefix} {detail}"
        else:
            new_line = f"{prefix} {current}; {detail}"
        lines[i] = new_line
        path.write_text("\n".join(lines) + "\n")
        return True
    return False


# ── Multi-home + custom-field helper (Action Items cross-project) ────────

def section_for_due(due_on, raw_lower):
    """Pick the AI Action Items section based on tag + due date."""
    today = datetime.now().strftime("%Y-%m-%d")
    week_end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    if "#waiting" in raw_lower:
        return AI_SECTIONS["waiting"]
    if not due_on:
        return AI_SECTIONS["later"]
    if due_on <= today:
        return AI_SECTIONS["today"]
    if due_on <= week_end:
        return AI_SECTIONS["this_week"]
    return AI_SECTIONS["later"]


def complexity_gid_for(level):
    return AI_COMPLEXITY.get((level or "").lower(), "")


def multi_home_subtask(subtask_gid, merchant_name, raw_lower, due_on, complexity):
    """Add subtask to the AI cross-project + set Merchant/Tag/Complexity custom fields."""
    if not AI_PROJECT:
        return
    section = section_for_due(due_on, raw_lower)
    api(
        "POST",
        f"/tasks/{subtask_gid}/addProject",
        {"data": {"project": AI_PROJECT, "section": section}},
    )
    custom = {}
    if AI_FIELD_MERCHANT:
        custom[AI_FIELD_MERCHANT] = merchant_name
    if AI_FIELD_TAG:
        for tag, gid in AI_TAG_OPTIONS.items():
            if f"#{tag}" in raw_lower and gid:
                custom[AI_FIELD_TAG] = gid
                break
    if AI_FIELD_COMPLEXITY:
        c = complexity_gid_for(complexity)
        if c:
            custom[AI_FIELD_COMPLEXITY] = c
    if custom:
        api("PUT", f"/tasks/{subtask_gid}", {"data": {"custom_fields": custom}})


# ── Proposal file I/O ────────────────────────────────────────────────────

def stable_id(slug, kind, idx, description):
    """Generate a stable item ID for tracking apply_status across runs."""
    desc_slug = re.sub(r"[^\w]+", "-", (description or "")[:40]).strip("-").lower()
    return f"{slug}-{kind}-{idx:02d}-{desc_slug}"


def load_proposals(path):
    """Load + normalize a proposals file, generating IDs for items if missing."""
    data = json.loads(path.read_text())
    slug = data.get("slug", path.stem.split("-")[0])

    def assign_ids(arr, kind):
        for i, item in enumerate(arr or []):
            if not item.get("id"):
                desc = item.get("description") or item.get("detail") or item.get("local_line_match", "")
                item["id"] = stable_id(slug, kind, i, desc)
        return arr or []

    data["auto_close"] = assign_ids(data.get("auto_close", []), "close")
    data["new_items"] = assign_ids(data.get("new_items", []), "create")
    data["inline_gaps"] = assign_ids(data.get("inline_gaps", []), "gap")
    if not isinstance(data.get("apply_status"), dict):
        data["apply_status"] = {}
    return data


def save_proposals(path, data):
    """Write proposals back to disk atomically."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


TERMINAL_STATES = (
    "applied",
    "skipped_dedup",
    "skipped_low_confidence",
    "skipped_human_review",
)


def all_applied(data):
    """True if every actionable item has a terminal apply_status.

    A file with zero actionable items (true no-op return from analyst) is
    considered applied so the discovery pass can move it out of the pending
    queue immediately. A file with items but no apply_status entries (or
    items missing IDs) is NOT applied — the applier still needs to process it.
    """
    apply_status = data.get("apply_status", {}) or {}
    actionable_count = (
        len(data.get("auto_close") or [])
        + len(data.get("new_items") or [])
        + len(data.get("inline_gaps") or [])
    )
    if actionable_count == 0:
        return True

    terminal_count = 0
    for arr_key in ("auto_close", "new_items", "inline_gaps"):
        for it in data.get(arr_key) or []:
            item_id = it.get("id")
            if not item_id:
                return False  # IDs not assigned yet → must still apply
            st = apply_status.get(item_id, {}).get("status")
            if st in TERMINAL_STATES:
                terminal_count += 1
    return terminal_count == actionable_count


# ── Per-merchant pre-flight ──────────────────────────────────────────────

def fetch_existing_subtasks(parent_gid):
    """GET subtasks once and return list of {gid, name, completed, norm}."""
    try:
        result = api("GET", f"/tasks/{parent_gid}/subtasks?opt_fields=name,completed,due_on")
    except AsanaAPIError as e:
        print(f"    [warn] could not fetch existing subtasks: {e}")
        return []
    if not isinstance(result, list):
        return []
    return [
        {
            "gid": s["gid"],
            "name": s.get("name", ""),
            "completed": s.get("completed", False),
            "norm": normalize(s.get("name", "")),
        }
        for s in result
    ]


def find_existing_subtask(existing, description):
    norm = normalize(description)
    for s in existing:
        if fuzzy_match(s["norm"], norm):
            return s
    return None


# ── Apply: auto_close ────────────────────────────────────────────────────

def apply_auto_close(item, mapping_subtask_gids, slug, dry_run, run_report):
    confidence = (item.get("confidence") or "").lower()
    item_id = item["id"]

    if confidence != "high":
        run_report["needs_human_review"].append(
            {
                "slug": slug,
                "kind": "auto_close",
                "id": item_id,
                "confidence": confidence or "unspecified",
                "summary": item.get("local_line_match", "")[:120],
                "outbound": f"{item.get('outbound_subject','')} ({item.get('outbound_date','')})",
            }
        )
        return {"status": "skipped_low_confidence"}

    subtask_gid = item.get("subtask_gid")
    if not subtask_gid:
        local_line = item.get("local_line_match", "")
        for key, gid in mapping_subtask_gids.items():
            if local_line.startswith("- [") and key in local_line:
                subtask_gid = gid
                break
        if not subtask_gid:
            return {"status": "pending_retry", "error": "no subtask_gid in proposal or asana.json"}

    if dry_run:
        run_report["auto_closed"].append(
            {"slug": slug, "id": item_id, "subtask_gid": subtask_gid, "dry_run": True}
        )
        return {"status": "applied", "subtask_gid": subtask_gid, "dry_run": True}

    try:
        api("PUT", f"/tasks/{subtask_gid}", {"data": {"completed": True}})
    except AsanaAPIError as e:
        return {"status": "pending_retry", "error": str(e)}

    completion_note = ""
    if item.get("outbound_date") and item.get("outbound_alias"):
        completion_note = f"{item['outbound_date']} (sent via {item['outbound_alias']})"

    actions_md = PROJECTS_DIR / slug / "action-items.md"
    target_norm = normalize(item.get("local_line_match", ""))
    if target_norm:
        mark_action_item_complete(actions_md, target_norm, completion_note=completion_note)

    run_report["auto_closed"].append(
        {"slug": slug, "id": item_id, "subtask_gid": subtask_gid}
    )
    return {
        "status": "applied",
        "subtask_gid": subtask_gid,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Apply: new_items ─────────────────────────────────────────────────────

def build_local_line(item):
    """Compose the action-items.md line from a proposal item."""
    tags = " ".join(f"#{t}" for t in (item.get("tags") or []))
    description = item.get("description") or ""
    complexity = (item.get("complexity") or "")[:1].upper() or "M"
    owner = item.get("owner") or "[YOUR_NAME]"
    due = item.get("due_on") or "TBD"
    source = item.get("source") or ""
    parts = [f"{tags} — {description}".strip(" —")]
    parts.append(f"Complexity: {complexity}")
    parts.append(f"Owner: {owner}")
    parts.append(f"Due: {due}")
    if source:
        parts.append(f"Source: {source}")
    return " — ".join(parts)


def build_subtask_notes(item):
    """Compose the Asana subtask body: analyst notes + Suggested Resources."""
    notes = (item.get("notes") or "").strip()
    resources = item.get("suggested_resources") or []
    parts = [notes] if notes else []
    if resources:
        parts.append("")
        parts.append("Suggested Resources:")
        for r in resources:
            if isinstance(r, str):
                parts.append(f"- Ref — {r}")
                continue
            kind = (r.get("kind") or "").lower()
            label_kind = {
                "email": "Email",
                "slack": "Slack",
                "doc": "Docs",
                "ref": "Ref",
            }.get(kind, kind.title() or "Ref")
            if kind == "doc" and r.get("verify"):
                label_kind = f"{label_kind} (verify)"
            label = r.get("label", "")
            url = r.get("url")
            if url:
                parts.append(f"- {label_kind} — {label} — {url}")
            else:
                parts.append(f"- {label_kind} — {label}")
    return "\n".join(parts).strip()


def apply_new_item(item, parent_gid, merchant_name, asana_json_path, mapping, existing_subtasks, slug, dry_run, run_report):
    item_id = item["id"]
    description = item.get("description") or ""
    if not description.strip():
        return {"status": "pending_retry", "error": "empty description"}

    # Idempotency: skip if a fuzzy match exists in Asana subtasks
    match = find_existing_subtask(existing_subtasks, description)
    if match:
        run_report["dedup_skipped"].append(
            {
                "slug": slug,
                "id": item_id,
                "matched_subtask_gid": match["gid"],
                "matched_name": match["name"][:80],
            }
        )
        return {"status": "skipped_dedup", "subtask_gid": match["gid"]}

    raw_line = build_local_line(item)
    raw_lower = raw_line.lower()
    due_on = item.get("due_on") or None

    if dry_run:
        run_report["created"].append(
            {"slug": slug, "id": item_id, "description": description[:80], "due_on": due_on, "dry_run": True}
        )
        return {"status": "applied", "dry_run": True}

    sub_data = {"name": description[:1000]}
    if due_on:
        sub_data["due_on"] = due_on
    notes = build_subtask_notes(item)
    if notes:
        sub_data["notes"] = notes

    try:
        sub = api("POST", f"/tasks/{parent_gid}/subtasks", {"data": sub_data})
    except AsanaAPIError as e:
        return {"status": "pending_retry", "error": f"create subtask: {e}"}

    if not sub or not sub.get("gid"):
        return {"status": "pending_retry", "error": "create subtask returned no gid"}

    subtask_gid = sub["gid"]

    try:
        multi_home_subtask(
            subtask_gid,
            merchant_name,
            raw_lower,
            due_on,
            item.get("complexity"),
        )
    except AsanaAPIError as e:
        # Subtask was created but multi-home/custom-fields failed. Persist GID
        # so a retry can pick up where we left off without double-creating.
        return {
            "status": "pending_retry",
            "subtask_gid": subtask_gid,
            "error": f"multi-home: {e}",
        }

    actions_md = PROJECTS_DIR / slug / "action-items.md"
    appended = append_open_action_item(actions_md, raw_line)

    # Persist subtask_gid back to asana.json (key matches sync-to-asana convention: raw[:80])
    mapping.setdefault("subtask_gids", {})
    mapping["subtask_gids"][raw_line[:80]] = subtask_gid
    asana_json_path.write_text(json.dumps(mapping, indent=2) + "\n")

    # Update existing-subtasks cache so subsequent items in same merchant see this one
    existing_subtasks.append(
        {"gid": subtask_gid, "name": description, "completed": False, "norm": normalize(description)}
    )

    run_report["created"].append(
        {
            "slug": slug,
            "id": item_id,
            "subtask_gid": subtask_gid,
            "description": description[:80],
            "due_on": due_on,
            "local_appended": appended,
        }
    )
    return {
        "status": "applied",
        "subtask_gid": subtask_gid,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Apply: inline_gaps ───────────────────────────────────────────────────

def apply_inline_gap(item, slug, dry_run, run_report):
    item_id = item["id"]
    kind = (item.get("kind") or "").lower()
    detail = item.get("detail") or ""

    if kind != "contact":
        run_report["needs_human_review"].append(
            {
                "slug": slug,
                "kind": f"inline_gap:{kind}",
                "id": item_id,
                "detail": detail[:120],
                "source": item.get("source", ""),
            }
        )
        return {"status": "skipped_human_review"}

    if dry_run:
        run_report["inline_patched"].append(
            {"slug": slug, "id": item_id, "kind": kind, "detail": detail[:80], "dry_run": True}
        )
        return {"status": "applied", "dry_run": True}

    project_md = PROJECTS_DIR / slug / "PROJECT.md"
    patched = patch_project_contact(project_md, detail)
    if not patched:
        # Either already present (fuzzy match) or no Stripe contacts line found
        run_report["inline_patched"].append(
            {"slug": slug, "id": item_id, "kind": kind, "detail": detail[:80], "noop": True}
        )
        return {
            "status": "applied",
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "noop_reason": "already_present_or_no_anchor",
        }

    run_report["inline_patched"].append(
        {"slug": slug, "id": item_id, "kind": kind, "detail": detail[:80]}
    )
    return {"status": "applied", "applied_at": datetime.now(timezone.utc).isoformat()}


# ── Apply: timeline summaries ─────────────────────────────────────────────

def _patch_timeline_summary(slug, ts_item):
    """Replace _pending_ with the actual summary in timeline.md. Returns True if patched."""
    timeline_path = PROJECTS_DIR / slug / "timeline.md"
    if not timeline_path.exists():
        return False
    text = timeline_path.read_text()
    entry_ref = ts_item.get("entry_ref", "")
    message_id = ts_item.get("message_id", "")
    summary = ts_item.get("summary", "")
    if not summary:
        return False

    # Find the timeline entry by date+type or message_id
    lines = text.splitlines()
    found_idx = None
    for i, line in enumerate(lines):
        if entry_ref and entry_ref in line and line.startswith("## "):
            found_idx = i
            break
        if message_id and message_id in line:
            # Found the Source line with this message_id — the entry header is above
            for j in range(i, max(i - 8, -1), -1):
                if lines[j].startswith("## "):
                    found_idx = j
                    break
            break

    if found_idx is None:
        return False

    # Look for _pending_ within the next few lines of this entry
    for i in range(found_idx, min(found_idx + 10, len(lines))):
        if "**Summary**: _pending_" in lines[i]:
            lines[i] = lines[i].replace("_pending_", summary)
            timeline_path.write_text("\n".join(lines) + "\n")
            print(f"  [timeline] patched summary for {entry_ref or message_id}")
            return True

    return False


# ── Per-file orchestration ───────────────────────────────────────────────

def merchant_name_from_project_md(slug):
    p = PROJECTS_DIR / slug / "PROJECT.md"
    if not p.exists():
        return slug
    first = p.read_text().splitlines()[0]
    return first.lstrip("# ").strip() or slug


def apply_file(path, dry_run, run_report):
    print(f"\n[{path.name}]")
    data = load_proposals(path)
    slug = data["slug"]
    parent_gid = data.get("task_gid")
    project_dir = PROJECTS_DIR / slug
    asana_json_path = project_dir / "asana.json"

    if not parent_gid:
        if asana_json_path.exists():
            parent_gid = json.loads(asana_json_path.read_text()).get("task_gid")
    if not parent_gid:
        print(f"  [skip] no task_gid in proposals or asana.json for {slug}")
        run_report["skipped_files"].append({"path": str(path), "reason": "no task_gid"})
        return

    mapping = json.loads(asana_json_path.read_text()) if asana_json_path.exists() else {
        "task_gid": parent_gid,
        "subtask_gids": {},
    }

    auto_close = data.get("auto_close", [])
    new_items = data.get("new_items", [])
    inline_gaps = data.get("inline_gaps", [])
    apply_status = data.get("apply_status", {})
    if not isinstance(apply_status, dict):
        apply_status = {}
    data["apply_status"] = apply_status

    actionable_creates = [
        it for it in new_items if apply_status.get(it["id"], {}).get("status") not in
        ("applied", "skipped_dedup")
    ]
    actionable_closes = [
        it for it in auto_close if apply_status.get(it["id"], {}).get("status") not in
        ("applied", "skipped_low_confidence")
    ]
    actionable_gaps = [
        it for it in inline_gaps if apply_status.get(it["id"], {}).get("status") not in
        ("applied", "skipped_human_review")
    ]

    print(
        f"  slug={slug} parent={parent_gid} "
        f"creates={len(actionable_creates)}/{len(new_items)} "
        f"closes={len(actionable_closes)}/{len(auto_close)} "
        f"gaps={len(actionable_gaps)}/{len(inline_gaps)}"
    )

    if not (actionable_creates or actionable_closes or actionable_gaps):
        print("  [skip] all items already terminal")
        return

    merchant_name = merchant_name_from_project_md(slug)

    existing = []
    if actionable_creates and not dry_run:
        existing = fetch_existing_subtasks(parent_gid)
        print(f"  pre-flight: {len(existing)} existing Asana subtasks")

    # Auto-closes first (cheap, no rate-limit risk)
    for item in actionable_closes:
        result = apply_auto_close(item, mapping.get("subtask_gids", {}), slug, dry_run, run_report)
        if not dry_run:
            apply_status[item["id"]] = {**apply_status.get(item["id"], {}), **result}
            save_proposals(path, data)

    # New items (atomic per item, rate-limit aware)
    for i, item in enumerate(actionable_creates):
        result = apply_new_item(
            item, parent_gid, merchant_name, asana_json_path, mapping, existing, slug, dry_run, run_report
        )
        if not dry_run:
            apply_status[item["id"]] = {**apply_status.get(item["id"], {}), **result}
            save_proposals(path, data)
            time.sleep(0.2)  # gentle pacing under the 150 req/min limit

    # Inline gaps (fast, local-only mostly)
    for item in actionable_gaps:
        result = apply_inline_gap(item, slug, dry_run, run_report)
        if not dry_run:
            apply_status[item["id"]] = {**apply_status.get(item["id"], {}), **result}
            save_proposals(path, data)

    # Asana comments (post to merchant task)
    for comment in data.get("asana_comments", []):
        if apply_status.get(f"comment-{comment.get('trigger', '')}", {}).get("status") == "applied":
            continue
        if dry_run:
            print(f"  [dry-run] would post Asana comment: {comment.get('comment_text', '')[:60]}")
        else:
            try:
                api("POST", f"/tasks/{parent_gid}/stories", {"data": {"text": comment["comment_text"]}})
                apply_status[f"comment-{comment.get('trigger', '')}"] = {"status": "applied"}
                save_proposals(path, data)
                print(f"  [comment] posted: {comment.get('comment_text', '')[:60]}")
            except Exception as e:
                print(f"  [comment-error] {e}")

    # Timeline summary enrichment (patch _pending_ entries)
    for ts_item in data.get("timeline_summaries", []):
        ts_key = f"timeline-{ts_item.get('message_id', ts_item.get('entry_ref', ''))}"
        if apply_status.get(ts_key, {}).get("status") == "applied":
            continue
        if dry_run:
            print(f"  [dry-run] would patch timeline: {ts_item.get('entry_ref', '')}")
        else:
            patched = _patch_timeline_summary(slug, ts_item)
            if patched:
                apply_status[ts_key] = {"status": "applied"}
            else:
                apply_status[ts_key] = {"status": "skipped_not_found"}
            save_proposals(path, data)

    # If everything terminal and not a dry-run, archive
    if not dry_run and all_applied(data):
        APPLIED_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        archive_path = APPLIED_DIR / f"{path.stem}.applied.{ts}{path.suffix}"
        shutil.move(str(path), str(archive_path))
        print(f"  [archived] {archive_path.name}")


# ── File discovery ───────────────────────────────────────────────────────

def discover_pending_files(slug=None, max_age_days=7):
    """Return (pending, stale, terminal) proposal Path lists, oldest first.

    pending: files with at least one non-terminal item; need to be applied
    stale:   pending files older than max_age_days; surfaced separately
    terminal: files where every item is in a terminal state (or zero items);
              these can be auto-archived even if no work runs this pass
    """
    if not PROPOSALS_DIR.exists():
        return [], [], []
    now = datetime.now()
    pending = []
    stale = []
    terminal = []
    for p in sorted(PROPOSALS_DIR.glob("*.json")):
        if p.parent != PROPOSALS_DIR:
            continue
        if slug and not p.stem.startswith(f"{slug}-"):
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        if all_applied(data):
            terminal.append(p)
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        age_days = (now - mtime).days
        if age_days > max_age_days:
            stale.append((p, age_days))
            continue
        pending.append(p)
    return pending, stale, terminal


def archive_terminal_file(path):
    """Move a fully-terminal proposal file to applied/ with timestamp suffix."""
    APPLIED_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    archive_path = APPLIED_DIR / f"{path.stem}.applied.{ts}{path.suffix}"
    shutil.move(str(path), str(archive_path))
    return archive_path


# ── Run report + dual-write log ──────────────────────────────────────────

def empty_run_report():
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "auto_closed": [],
        "created": [],
        "dedup_skipped": [],
        "inline_patched": [],
        "needs_human_review": [],
        "skipped_files": [],
        "errors": [],
    }


def print_run_report(report, dry_run):
    print("\n" + "=" * 60)
    print(f"Run report ({'DRY RUN' if dry_run else 'LIVE'})")
    print("=" * 60)
    print(f"  Auto-closed:        {len(report['auto_closed'])}")
    print(f"  Created:            {len(report['created'])}")
    print(f"  Dedup-skipped:      {len(report['dedup_skipped'])}")
    print(f"  Inline patched:     {len(report['inline_patched'])}")
    print(f"  Needs human review: {len(report['needs_human_review'])}")
    if report["needs_human_review"]:
        print("\n  Needs human review:")
        for r in report["needs_human_review"]:
            kind = r.get("kind", "")
            slug = r.get("slug", "")
            summary = r.get("summary") or r.get("detail") or ""
            extra = ""
            if r.get("confidence"):
                extra = f" [{r['confidence']}]"
            print(f"    - [{slug}] {kind}{extra} — {summary[:90]}")
    if report["skipped_files"]:
        print("\n  Skipped files:")
        for s in report["skipped_files"]:
            print(f"    - {s['path']}: {s['reason']}")


def append_dual_write_log(report, dry_run, file_count):
    if dry_run:
        return
    DUAL_WRITE_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not DUAL_WRITE_LOG.exists():
        DUAL_WRITE_LOG.write_text(
            "# Dual-Write Log\n\n"
            "Append-only audit trail. Every `apply-proposals.py` run writes one entry.\n\n"
        )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "",
        f"## {ts} — {file_count} file(s)",
        f"- Auto-closed: {len(report['auto_closed'])}",
        f"- Created: {len(report['created'])}",
        f"- Dedup-skipped: {len(report['dedup_skipped'])}",
        f"- Inline patched: {len(report['inline_patched'])}",
        f"- Needs human review: {len(report['needs_human_review'])}",
    ]
    if report["needs_human_review"]:
        lines.append("- Review queue:")
        for r in report["needs_human_review"]:
            lines.append(
                f"  - [{r.get('slug','')}] {r.get('kind','')} "
                f"{('['+r['confidence']+'] ') if r.get('confidence') else ''}"
                f"{(r.get('summary') or r.get('detail') or '')[:100]}"
            )
    with DUAL_WRITE_LOG.open("a") as f:
        f.write("\n".join(lines) + "\n")


# ── Entrypoint ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Apply persisted scan-review proposals.")
    parser.add_argument("--resume", action="store_true", help="Apply all pending files in data/scan-proposals/")
    parser.add_argument("--slug", help="Limit to one merchant slug")
    parser.add_argument("--file", help="Apply a specific proposals file path")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--max-age-days", type=int, default=7, help="Skip proposals older than N days")
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip post-apply verification pass (default: verify is on)",
    )
    args = parser.parse_args()

    if not PAT and not args.dry_run:
        print("Error: ASANA_PAT not in .env")
        sys.exit(1)

    if not (args.resume or args.slug or args.file):
        print("Specify --resume, --slug X, or --file path")
        sys.exit(1)

    files = []
    stale = []
    terminal = []
    if args.file:
        p = Path(args.file)
        if not p.is_absolute():
            p = WORKSPACE_ROOT / p
        if not p.exists():
            print(f"Error: file not found: {p}")
            sys.exit(1)
        files = [p]
    else:
        files, stale, terminal = discover_pending_files(
            slug=args.slug, max_age_days=args.max_age_days
        )

    if stale:
        print(f"\n[warn] {len(stale)} stale file(s) skipped (>{args.max_age_days}d):")
        for p, age in stale:
            print(f"  - {p.name} ({age}d old)")

    if terminal and not args.dry_run:
        print(f"\nArchiving {len(terminal)} fully-terminal file(s) (no-op or all-skipped):")
        for p in terminal:
            archived = archive_terminal_file(p)
            print(f"  - {p.name} -> {archived.name}")

    if not files:
        if not (terminal and not args.dry_run):
            print("No pending proposal files to apply.")
        return

    print(f"\nApplying {len(files)} proposal file(s) {'(DRY RUN)' if args.dry_run else ''}")
    report = empty_run_report()
    for path in files:
        try:
            apply_file(path, args.dry_run, report)
        except Exception as e:
            print(f"  [error] {path.name}: {e}")
            report["errors"].append({"path": str(path), "error": str(e)})

    print_run_report(report, args.dry_run)
    append_dual_write_log(report, args.dry_run, len(files))

    if not args.dry_run and not args.no_verify:
        run_post_apply_verification(files)


def run_post_apply_verification(files):
    """Invoke verify-proposals.py against the same files we just applied.

    The verifier targets archived files (we just moved them) by name, so it
    catches drift between what we recorded as `applied` and what Asana / local
    actually reflect. Drift count is appended to the dual-write log.
    """
    if not files:
        return
    verify_script = WORKSPACE_ROOT / "scripts" / "verify-proposals.py"
    if not verify_script.exists():
        return
    import subprocess

    archived_names = {p.stem for p in files}
    archived_paths = []
    if APPLIED_DIR.exists():
        for p in APPLIED_DIR.glob("*.json"):
            stem = p.stem
            for original_stem in archived_names:
                if stem.startswith(original_stem + ".applied."):
                    archived_paths.append(p)
                    break

    if not archived_paths:
        return

    print("\n" + "=" * 60)
    print("Post-apply verification")
    print("=" * 60)
    drift_total = 0
    for ap in archived_paths:
        try:
            result = subprocess.run(
                ["python3", str(verify_script), "--file", str(ap), "--json"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as e:
            print(f"  [warn] verify failed for {ap.name}: {e}")
            continue
        if result.returncode != 0:
            print(f"  [warn] verify exited {result.returncode} for {ap.name}: {result.stderr[:200]}")
            continue
        try:
            payload = json.loads(result.stdout)
        except Exception:
            print(f"  [warn] verify returned non-JSON for {ap.name}")
            continue
        drift_total += payload.get("drift_count", 0)
        for d in payload.get("drift", []):
            print(
                f"  [drift] [{d.get('slug','?')}] {d.get('kind','?')}: {d.get('detail','')[:100]}"
            )
    if drift_total == 0:
        print("  No drift detected.")
    else:
        print(f"\n  Total drift: {drift_total} item(s) — review and resolve manually.")
        try:
            with DUAL_WRITE_LOG.open("a") as f:
                f.write(f"- Verification drift: {drift_total} item(s)\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
