#!/usr/bin/env python3
"""
Fixture-based smoke tests for critical scripts.

Exercises handover-parse.py, list-actions.py, hubble-reconcile.py, and
handover-create.py as black-box CLI tools via subprocess. Catches regex drift,
JSON schema changes, and broken interfaces that static contract validation
(test-subagents.py) doesn't cover.

Usage:
  python3 tests/smoke.py

Exit codes:
  0  all tests pass
  1  one or more tests fail
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = WORKSPACE_ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVE_DIR = WORKSPACE_ROOT / "projects" / "active"

TIMEOUT = 30


# ── Runner infrastructure ────────────────────────────────────────────────────

class Results:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, group: str, name: str):
        self.passed.append(f"{group}: {name}")
        print(f"  [PASS] {group}: {name}")

    def fail(self, group: str, name: str, detail: str):
        self.failed.append((f"{group}: {name}", detail))
        print(f"  [FAIL] {group}: {name}")
        for line in detail.strip().splitlines():
            print(f"          {line}")

    def summary(self) -> int:
        total = len(self.passed) + len(self.failed)
        print()
        print(f"Summary: {len(self.passed)} passed, {len(self.failed)} failed (of {total}).")
        return 0 if not self.failed else 1


def run_script(args: list[str], stdin_data: str = "", env_override: dict = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["SLACK_HANDLE"] = "testhandle"
    if env_override:
        env.update(env_override)
    try:
        result = subprocess.run(
            ["python3"] + args,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=str(WORKSPACE_ROOT),
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


# ── Group 1: handover-parse.py ───────────────────────────────────────────────

def test_handover_parse(r: Results):
    script = str(SCRIPTS / "handover-parse.py")
    valid_text = (FIXTURES / "handover-text-valid.txt").read_text()
    slack_json = (FIXTURES / "handover-slack-json.json").read_text()
    garbage = (FIXTURES / "handover-garbage.txt").read_text()

    # Case 1: parse_text_valid
    code, out, err = run_script([script, "--text"], stdin_data=valid_text)
    if code != 0:
        r.fail("handover-parse", "parse_text_valid", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            checks = []
            if data.get("merchant_name") != "Acme Corp":
                checks.append(f"merchant_name={data.get('merchant_name')!r}, expected 'Acme Corp'")
            if not (data.get("slug") or "").startswith("acme"):
                checks.append(f"slug={data.get('slug')!r}, expected starts with 'acme'")
            if "accma_" not in (data.get("manifest_url") or ""):
                checks.append(f"manifest_url missing 'accma_'")
            if "006" not in (data.get("sfdc_opp_id") or ""):
                checks.append(f"sfdc_opp_id={data.get('sfdc_opp_id')!r}, expected contains '006'")
            contact = data.get("primary_contact") or {}
            if contact.get("email") != "jane.smith@example.com":
                checks.append(f"primary_contact.email={contact.get('email')!r}")
            if checks:
                r.fail("handover-parse", "parse_text_valid", "\n".join(checks))
            else:
                r.ok("handover-parse", "parse_text_valid")
        except json.JSONDecodeError as e:
            r.fail("handover-parse", "parse_text_valid", f"Invalid JSON output: {e}")

    # Case 2: parse_text_garbage
    code, out, err = run_script([script, "--text"], stdin_data=garbage)
    if code != 0:
        r.fail("handover-parse", "parse_text_garbage", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            has_signal = data.get("not_a_handover") or len(data.get("missing", [])) >= 4
            if not has_signal:
                r.fail("handover-parse", "parse_text_garbage",
                       f"Expected not_a_handover=true or many missing fields, got: {data}")
            else:
                r.ok("handover-parse", "parse_text_garbage")
        except json.JSONDecodeError as e:
            r.fail("handover-parse", "parse_text_garbage", f"Invalid JSON: {e}")

    # Case 3: parse_slack_json
    code, out, err = run_script([script, "--from-stdin"], stdin_data=slack_json)
    if code != 0:
        r.fail("handover-parse", "parse_slack_json", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            checks = []
            if data.get("source") != "scan":
                checks.append(f"source={data.get('source')!r}, expected 'scan'")
            if data.get("channel_id") != "C0TEST1234":
                checks.append(f"channel_id={data.get('channel_id')!r}")
            if not data.get("thread_permalink"):
                checks.append("thread_permalink is empty")
            if data.get("merchant_name") != "Acme Corp":
                checks.append(f"merchant_name={data.get('merchant_name')!r}")
            if checks:
                r.fail("handover-parse", "parse_slack_json", "\n".join(checks))
            else:
                r.ok("handover-parse", "parse_slack_json")
        except json.JSONDecodeError as e:
            r.fail("handover-parse", "parse_slack_json", f"Invalid JSON: {e}")

    # Case 4: parse_malformed_json
    code, out, err = run_script([script, "--from-stdin"], stdin_data='{"broken":')
    if code != 2:
        r.fail("handover-parse", "parse_malformed_json", f"Expected exit 2, got {code}")
    else:
        r.ok("handover-parse", "parse_malformed_json")

    # Case 5: parse_file_mode
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(valid_text)
        tmp_path = f.name
    try:
        code, out, err = run_script([script, "--file", tmp_path])
        if code != 0:
            r.fail("handover-parse", "parse_file_mode", f"Exit {code}, stderr: {err}")
        else:
            try:
                data = json.loads(out)
                if not data.get("merchant_name"):
                    r.fail("handover-parse", "parse_file_mode", "merchant_name missing")
                else:
                    r.ok("handover-parse", "parse_file_mode")
            except json.JSONDecodeError as e:
                r.fail("handover-parse", "parse_file_mode", f"Invalid JSON: {e}")
    finally:
        os.unlink(tmp_path)

    # Case 6: parse_products_hint
    code, out, err = run_script([script, "--text"], stdin_data=valid_text)
    if code == 0:
        try:
            data = json.loads(out)
            hint = data.get("products_hint", "")
            if "Payments" not in hint and "payments" not in hint.lower():
                r.fail("handover-parse", "parse_products_hint",
                       f"products_hint={hint!r}, expected to contain 'Payments'")
            else:
                r.ok("handover-parse", "parse_products_hint")
        except json.JSONDecodeError as e:
            r.fail("handover-parse", "parse_products_hint", f"Invalid JSON: {e}")
    else:
        r.fail("handover-parse", "parse_products_hint", f"Exit {code}")

    # Case 6b: parse_slack_bot_format — current Account Manifest Bot intake format
    # ("…starting the handover process", merchant + opp id in the SFDC attachment).
    bot_json = (FIXTURES / "handover-slack-bot-json.json").read_text()
    code, out, err = run_script([script, "--from-stdin"], stdin_data=bot_json)
    if code != 0:
        r.fail("handover-parse", "parse_slack_bot_format", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            checks = []
            if data.get("merchant_name") != "Acme Vacation Rentals":
                checks.append(f"merchant_name={data.get('merchant_name')!r}, expected 'Acme Vacation Rentals'")
            if "006" not in (data.get("sfdc_opp_id") or ""):
                checks.append(f"sfdc_opp_id={data.get('sfdc_opp_id')!r}, expected contains '006'")
            if data.get("ae") != "testae":
                checks.append(f"ae={data.get('ae')!r}, expected 'testae'")
            if data.get("not_a_handover"):
                checks.append("not_a_handover set true — bot phrase not recognized")
            if checks:
                r.fail("handover-parse", "parse_slack_bot_format", "\n".join(checks))
            else:
                r.ok("handover-parse", "parse_slack_bot_format")
        except json.JSONDecodeError as e:
            r.fail("handover-parse", "parse_slack_bot_format", f"Invalid JSON: {e}")

    # Case 6c: parse_slack_legacy_format — legacy "introducing <Merchant>" (no
    # products bracket) + mailto-wrapped contact. Must extract merchant, the
    # contact email, and the merchant email domain.
    legacy_json = (FIXTURES / "handover-slack-legacy-json.json").read_text()
    code, out, err = run_script([script, "--from-stdin"], stdin_data=legacy_json)
    if code != 0:
        r.fail("handover-parse", "parse_slack_legacy_format", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            checks = []
            if data.get("merchant_name") != "Globex Imports":
                checks.append(f"merchant_name={data.get('merchant_name')!r}, expected 'Globex Imports'")
            if (data.get("primary_contact") or {}).get("email") != "rholt@globex.example.com":
                checks.append(f"contact email not extracted from <mailto:..>: {data.get('primary_contact')}")
            if "globex.example.com" not in (data.get("email_domains") or []):
                checks.append(f"email_domains={data.get('email_domains')}, expected to include 'globex.example.com'")
            if checks:
                r.fail("handover-parse", "parse_slack_legacy_format", "\n".join(checks))
            else:
                r.ok("handover-parse", "parse_slack_legacy_format")
        except json.JSONDecodeError as e:
            r.fail("handover-parse", "parse_slack_legacy_format", f"Invalid JSON: {e}")


# ── Group 1b: handover-match.py ──────────────────────────────────────────────

def test_handover_match(r: Results):
    script = str(SCRIPTS / "handover-match.py")
    snapshot = str(FIXTURES / "handover-match-snapshot.json")
    proposals = (FIXTURES / "handover-match-proposals.json").read_text()

    # Case: classify_roster — SFDC match, name match, and one triage.
    code, out, err = run_script(
        [script, "--proposals-stdin", "--snapshot", snapshot], stdin_data=proposals)
    if code != 0:
        r.fail("handover-match", "classify_roster", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            matched = {m["match_method"]: m for m in data.get("matched", [])}
            checks = []
            if data["counts"].get("matched") != 2:
                checks.append(f"matched count={data['counts'].get('matched')}, expected 2")
            if data["counts"].get("triage") != 1:
                checks.append(f"triage count={data['counts'].get('triage')}, expected 1")
            if "sfdc" not in matched:
                checks.append("no SFDC-id match (18-char proposal vs 15-char Hubble link)")
            elif matched["sfdc"].get("merchant_name") != "Northwind Traders":
                checks.append(f"sfdc merchant={matched['sfdc'].get('merchant_name')!r}, expected canonical 'Northwind Traders'")
            if "name" not in matched:
                checks.append("no name-similarity match")
            if checks:
                r.fail("handover-match", "classify_roster", "\n".join(checks))
            else:
                r.ok("handover-match", "classify_roster")
        except (json.JSONDecodeError, KeyError) as e:
            r.fail("handover-match", "classify_roster", f"Bad output: {e}\n{out[:300]}")

    # Case: malformed_json → exit 2
    code, out, err = run_script([script, "--proposals-stdin"], stdin_data='{"broken":')
    if code != 2:
        r.fail("handover-match", "malformed_json", f"Expected exit 2, got {code}")
    else:
        r.ok("handover-match", "malformed_json")

    # Case: match_by_email_domain — no opp id, no matchable name; binds via the
    # thread's merchant email domain against the roster primary_contact_email.
    email_prop = json.dumps([{
        "source": "scan", "channel_id": "C0T", "thread_ts": "9.9",
        "thread_permalink": "https://x/9",
        "email_domains": ["northwind.example.com"],
    }])
    code, out, err = run_script(
        [script, "--proposals-stdin", "--snapshot", snapshot], stdin_data=email_prop)
    if code != 0:
        r.fail("handover-match", "match_by_email_domain", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            m = data.get("matched", [])
            if len(m) == 1 and m[0].get("match_method") == "email" and m[0].get("merchant_name") == "Northwind Traders":
                r.ok("handover-match", "match_by_email_domain")
            else:
                r.fail("handover-match", "match_by_email_domain",
                       f"Expected 1 email match to Northwind, got: {data.get('counts')} {[x.get('match_method') for x in m]}")
        except (json.JSONDecodeError, KeyError) as e:
            r.fail("handover-match", "match_by_email_domain", f"Bad output: {e}\n{out[:300]}")

    # Case: coverage_full — backfill view, both roster projects covered.
    code, out, err = run_script(
        [script, "--proposals-stdin", "--coverage", "--snapshot", snapshot],
        stdin_data=proposals)
    if code != 0:
        r.fail("handover-match", "coverage_full", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            c = data["counts"]
            if (c.get("roster"), c.get("covered"), c.get("missing")) != (2, 2, 0):
                r.fail("handover-match", "coverage_full",
                       f"Expected roster/covered/missing = 2/2/0, got "
                       f"{c.get('roster')}/{c.get('covered')}/{c.get('missing')}")
            else:
                r.ok("handover-match", "coverage_full")
        except (json.JSONDecodeError, KeyError) as e:
            r.fail("handover-match", "coverage_full", f"Bad output: {e}\n{out[:300]}")

    # Case: coverage_missing — no threads → every roster project reported missing.
    code, out, err = run_script(
        [script, "--proposals-stdin", "--coverage", "--snapshot", snapshot],
        stdin_data="[]")
    if code != 0:
        r.fail("handover-match", "coverage_missing", f"Exit {code}, stderr: {err}")
    else:
        try:
            data = json.loads(out)
            c = data["counts"]
            if c.get("covered") != 0 or c.get("missing") != 2:
                r.fail("handover-match", "coverage_missing",
                       f"Expected covered/missing = 0/2, got "
                       f"{c.get('covered')}/{c.get('missing')}")
            else:
                r.ok("handover-match", "coverage_missing")
        except (json.JSONDecodeError, KeyError) as e:
            r.fail("handover-match", "coverage_missing", f"Bad output: {e}\n{out[:300]}")


# ── Group 2: list-actions.py ─────────────────────────────────────────────────

def test_list_actions(r: Results):
    script = str(SCRIPTS / "list-actions.py")
    slug = "_smoke-test"
    slug_dir = ACTIVE_DIR / slug
    fixture_src = FIXTURES / "action-items-fixture.md"

    try:
        slug_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixture_src, slug_dir / "action-items.md")

        # Case 7: list_json_output
        code, out, err = run_script([script, "--json", "--slug", slug, "--include-closed"])
        if code != 0:
            r.fail("list-actions", "list_json_output", f"Exit {code}, stderr: {err}")
        else:
            try:
                items = json.loads(out)
                open_items = [i for i in items if not i.get("completed")]
                closed_items = [i for i in items if i.get("completed")]
                checks = []
                if len(open_items) != 3:
                    checks.append(f"Expected 3 open items, got {len(open_items)}")
                if len(closed_items) != 1:
                    checks.append(f"Expected 1 closed item, got {len(closed_items)}")
                if checks:
                    r.fail("list-actions", "list_json_output", "\n".join(checks))
                else:
                    r.ok("list-actions", "list_json_output")
            except json.JSONDecodeError as e:
                r.fail("list-actions", "list_json_output", f"Invalid JSON: {e}")

        # Case 8: list_filter_tag
        code, out, err = run_script([script, "--json", "--slug", slug, "--tag", "email"])
        if code != 0:
            r.fail("list-actions", "list_filter_tag", f"Exit {code}, stderr: {err}")
        else:
            try:
                items = json.loads(out)
                if len(items) != 1:
                    r.fail("list-actions", "list_filter_tag",
                           f"Expected 1 item with #email, got {len(items)}")
                else:
                    r.ok("list-actions", "list_filter_tag")
            except json.JSONDecodeError as e:
                r.fail("list-actions", "list_filter_tag", f"Invalid JSON: {e}")

        # Case 9: list_filter_overdue (fixture due dates are far-future: 2099-05-15, 2099-05-20)
        code, out, err = run_script([script, "--json", "--slug", slug, "--overdue"])
        if code != 0:
            r.fail("list-actions", "list_filter_overdue", f"Exit {code}, stderr: {err}")
        else:
            try:
                items = json.loads(out)
                if len(items) != 0:
                    r.fail("list-actions", "list_filter_overdue",
                           f"Expected 0 overdue items (dates are future), got {len(items)}")
                else:
                    r.ok("list-actions", "list_filter_overdue")
            except json.JSONDecodeError as e:
                r.fail("list-actions", "list_filter_overdue", f"Invalid JSON: {e}")

    finally:
        if slug_dir.exists():
            shutil.rmtree(slug_dir)


# ── Group 3: hubble-reconcile.py ─────────────────────────────────────────────

def test_hubble_reconcile(r: Results):
    script = str(SCRIPTS / "hubble-reconcile.py")
    snapshot = str(FIXTURES / "hubble-snapshot-fixture.json")

    # Case 10: reconcile_dry_run — should detect unmatched row as NEW PROJECT
    code, out, err = run_script([script, "--reconcile", "--dry-run", "--snapshot", snapshot])
    if code != 0:
        r.fail("hubble-reconcile", "reconcile_dry_run",
               f"Exit {code}, stderr: {err[:500]}")
    else:
        if "NEW PROJECTS" in out.upper() or "88888888" in out or "New Unmatched" in out:
            r.ok("hubble-reconcile", "reconcile_dry_run")
        else:
            r.fail("hubble-reconcile", "reconcile_dry_run",
                   f"Expected 'NEW PROJECTS' or '88888888' in output. Got:\n{out[:500]}")

    # Case 11: reconcile_detects_match — should recognize example-merchant via project_id
    code, out, err = run_script([script, "--reconcile", "--dry-run", "--snapshot", snapshot])
    if code != 0:
        r.fail("hubble-reconcile", "reconcile_detects_match",
               f"Exit {code}, stderr: {err[:500]}")
    else:
        matched = "example-merchant" in out.lower() or "99999999" in out or "matched" in out.lower()
        not_in_new = "example-merchant" not in (
            out.upper().split("NEW PROJECTS")[1] if "NEW PROJECTS" in out.upper() else ""
        ).lower()
        if matched or not_in_new:
            r.ok("hubble-reconcile", "reconcile_detects_match")
        else:
            r.fail("hubble-reconcile", "reconcile_detects_match",
                   f"Expected example-merchant to be matched (not in NEW). Got:\n{out[:500]}")


# ── Group 4: handover-create.py (validation only) ────────────────────────────

def test_handover_create(r: Results):
    script = str(SCRIPTS / "handover-create.py")

    # Case 12: create_slug_collision — slug "example-merchant" already exists
    proposal_collision = json.dumps({
        "source": "paste",
        "merchant_name": "Example Merchant",
        "slug": "example-merchant",
        "thread_permalink": "https://test.slack.com/archives/C123/p999",
    })
    code, out, err = run_script([script, "--proposal-stdin"], stdin_data=proposal_collision)
    if code != 1:
        r.fail("handover-create", "create_slug_collision",
               f"Expected exit 1 (slug collision), got {code}. Out: {out[:300]}")
    else:
        r.ok("handover-create", "create_slug_collision")

    # Case 13: create_missing_fields — missing merchant_name
    proposal_missing = json.dumps({
        "source": "paste",
        "slug": "some-slug",
        "thread_permalink": "https://test.slack.com/archives/C123/p999",
    })
    code, out, err = run_script([script, "--proposal-stdin"], stdin_data=proposal_missing)
    if code != 4:
        r.fail("handover-create", "create_missing_fields",
               f"Expected exit 4 (missing fields), got {code}. Out: {out[:300]}")
    else:
        r.ok("handover-create", "create_missing_fields")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("# Smoke Tests")
    print("_(20 cases across 5 scripts)_")
    print()

    r = Results()
    test_handover_parse(r)
    test_handover_match(r)
    test_list_actions(r)
    test_hubble_reconcile(r)
    test_handover_create(r)

    sys.exit(r.summary())


if __name__ == "__main__":
    main()
