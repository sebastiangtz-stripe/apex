#!/usr/bin/env python3
"""
Parse a Slack handover thread into a structured proposal the
handover-create.py script can act on.

The canonical handover shape (verified from real threads in #accelerate-qualification):

  Root message (from a manifest reviewer / bot):
    "Hi <reviewer>, can you please help to review this manifest for Accelerate:
     <Merchant Name>: <Products>"
    - Current eligibility: <status>
    - SFDC:     https://stripe.lightning.force.com/.../Opportunity/<opp>/view
    - Manifest: https://admin.corp.stripe.com/account-manifest/accma_<id>
    - Contact:  <Full Name> - <email>
    - Territory: <segment>

  Reply (from the assigning AE):
    "@<your-handle> this one is coming to you, please review the details to
     align and setup the project. Please confirm once the welcome email is sent."

Modes:
  --text < paste.txt         — parse raw pasted text (manual-paste flow)
  --file path/to/text.txt    — parse text from a file
  --from-stdin               — read a Slack thread JSON (from /handover-scanner)
                                with shape { channel_id, thread_ts,
                                permalink, messages: [{ user, text, ts, ...}, ...] }

Output: a JSON proposal on stdout. Exit codes:
  0  proposal printed (may include "missing" fields)
  1  no recognizable handover content (not a candidate)
  2  malformed JSON input
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ── Patterns ──────────────────────────────────────────────────────────────────

ACCELERATE_HEADER_RE = re.compile(
    r"Accelerate\s*:\s*([^:\n]+?)(?:\s*:\s*([^\n]+?))?\s*(?:\n|$)",
    re.IGNORECASE,
)
MANIFEST_URL_RE = re.compile(
    r"https?://admin\.corp\.stripe\.com/account-manifest/(accma_\w+)\S*"
)
SFDC_URL_RE = re.compile(
    r"https?://stripe\.lightning\.force\.com/lightning/r/Opportunity/(\w{15,18})/view\S*"
)
ACCT_ID_RE = re.compile(r"\b(acct_\w{16,})\b")
CONTACT_LINE_RE = re.compile(
    r"-\s*Contact\s*:\s*([^\n<]+?)\s*[-–—]\s*([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})",
    re.IGNORECASE,
)
TERRITORY_RE = re.compile(r"-\s*Territory\s*:\s*([^\n]+)", re.IGNORECASE)
ELIGIBILITY_RE = re.compile(r"-\s*Current eligibility\s*:\s*([^\n]+)", re.IGNORECASE)
AONR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([KMB])?", re.IGNORECASE)
SFDC_OPP_INLINE_RE = re.compile(r"\b(006[A-Z0-9]{15})\b")

HANDOVER_PHRASE_RE = re.compile(
    r"(coming to you|handing.{0,10}over|please review the details|setup the project|set up the project)",
    re.IGNORECASE,
)
THREAD_PERMALINK_RE = re.compile(
    r"https?://[\w-]+\.slack\.com/archives/(C[A-Z0-9]+)/p(\d{10})(\d+)\S*"
)


# ── Slug ──────────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\[\](){}|,;:+/\\.]", " ", s)
    s = re.sub(r"[-–—]", " ", s)
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    return s or "unnamed-merchant"


# ── Permalink build ───────────────────────────────────────────────────────────

def build_permalink(channel_id: str, thread_ts: str) -> str:
    """Turn a 1234567890.123456 timestamp into a p1234567890123456 permalink."""
    ts_compact = thread_ts.replace(".", "")
    return f"https://stripe.slack.com/archives/{channel_id}/p{ts_compact}"


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_fields(text: str) -> dict:
    out: dict = {}

    m = ACCELERATE_HEADER_RE.search(text)
    if m:
        out["merchant_name"] = m.group(1).strip()
        if m.group(2):
            out["products_hint"] = m.group(2).strip()

    m = MANIFEST_URL_RE.search(text)
    if m:
        out["manifest_url"] = m.group(0).rstrip(">.,;)")

    m = SFDC_URL_RE.search(text)
    if m:
        out["sfdc_url"] = m.group(0).rstrip(">.,;)")
        out["sfdc_opp_id"] = m.group(1)
    else:
        m2 = SFDC_OPP_INLINE_RE.search(text)
        if m2:
            out["sfdc_opp_id"] = m2.group(1)

    m = ACCT_ID_RE.search(text)
    if m:
        out["acct_id"] = m.group(1)

    m = CONTACT_LINE_RE.search(text)
    if m:
        out["primary_contact"] = {
            "name": m.group(1).strip(),
            "email": m.group(2).strip(),
        }

    m = TERRITORY_RE.search(text)
    if m:
        out["territory"] = m.group(1).strip()

    m = ELIGIBILITY_RE.search(text)
    if m:
        out["eligibility"] = m.group(1).strip()

    m = AONR_RE.search(text)
    if m:
        amount = m.group(1).replace(",", "")
        suffix = (m.group(2) or "").upper()
        out["aonr"] = f"${m.group(1)}{suffix}" if suffix else f"${m.group(1)}"

    return out


def extract_ae_handle(messages: list[dict], handle: str | None = None) -> str | None:
    """Find the user who posted the handover phrase. Falls back to first non-bot
    sender that isn't the recipient."""
    for msg in messages:
        text = msg.get("text", "") or ""
        if HANDOVER_PHRASE_RE.search(text):
            sender = msg.get("user_name") or msg.get("user") or ""
            if sender and sender.lower() != (handle or "").lower():
                return sender.lstrip("@")
    return None


def detect_handover(messages: list[dict]) -> bool:
    return any(HANDOVER_PHRASE_RE.search((m.get("text") or "")) for m in messages)


# ── Mode: --text / --file ─────────────────────────────────────────────────────

def parse_text(text: str) -> dict:
    """Parse a raw pasted handover (e.g. a thread copy-pasted into chat)."""
    fields = extract_fields(text)
    proposal: dict = {
        "source": "paste",
        "thread_permalink": None,
        "channel_id": None,
        "thread_ts": None,
    }

    pm = THREAD_PERMALINK_RE.search(text)
    if pm:
        channel_id, ts_secs, ts_micros = pm.group(1), pm.group(2), pm.group(3)
        proposal["channel_id"] = channel_id
        proposal["thread_ts"] = f"{ts_secs}.{ts_micros}"
        proposal["thread_permalink"] = pm.group(0).rstrip(">.,;)")

    proposal.update(fields)
    if "merchant_name" in proposal:
        proposal["slug"] = slugify(proposal["merchant_name"])

    # In pasted text we look for the canonical handover line
    # "<ae_handle>: @<target> this one is coming to you ..."
    # The AE is the speaker BEFORE the colon (not the @-target after it).
    sender_match = re.search(
        r"(?:^|\n)\s*([\w._-]+)\s*:\s*@[\w._-]+\s+(?:this one is coming to you|please review the details)",
        text, re.IGNORECASE,
    )
    if sender_match:
        proposal["ae"] = sender_match.group(1).lstrip("@")

    proposal["missing"] = compute_missing(proposal)
    return proposal


# ── Mode: --from-stdin (Slack JSON) ───────────────────────────────────────────

def parse_slack_thread(payload: dict, my_handle: str | None = None) -> dict:
    """Parse a thread JSON from /handover-scanner.

    Expected shape:
        {
          "channel_id": "C...",
          "thread_ts": "1234567890.123456",
          "permalink": "https://stripe.slack.com/...",
          "messages": [
            { "user_name": "dylanpiv", "text": "...", "ts": "..." },
            ...
          ]
        }
    """
    messages = payload.get("messages") or []
    full_text = "\n\n".join((m.get("text") or "") for m in messages)
    fields = extract_fields(full_text)

    channel_id = payload.get("channel_id")
    thread_ts = payload.get("thread_ts")
    permalink = payload.get("permalink") or (
        build_permalink(channel_id, thread_ts) if (channel_id and thread_ts) else None
    )

    proposal: dict = {
        "source": "scan",
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "thread_permalink": permalink,
    }
    proposal.update(fields)
    if "merchant_name" in proposal:
        proposal["slug"] = slugify(proposal["merchant_name"])

    ae = extract_ae_handle(messages, handle=my_handle)
    if ae:
        proposal["ae"] = ae

    if not detect_handover(messages):
        proposal["not_a_handover"] = True

    proposal["missing"] = compute_missing(proposal)
    return proposal


# ── Missing-field bookkeeping ─────────────────────────────────────────────────

REQUIRED_FOR_BOOTSTRAP = ["merchant_name", "slug", "thread_permalink"]
NICE_TO_HAVE = ["manifest_url", "sfdc_opp_id", "primary_contact", "ae", "products_hint"]


def compute_missing(proposal: dict) -> list[str]:
    missing: list[str] = []
    for f in REQUIRED_FOR_BOOTSTRAP + NICE_TO_HAVE:
        if f == "primary_contact":
            if not proposal.get("primary_contact"):
                missing.append(f)
        elif not proposal.get(f):
            missing.append(f)
    return missing


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--text", action="store_true",
                     help="Read raw pasted text from stdin.")
    grp.add_argument("--file", type=str,
                     help="Read raw pasted text from a file.")
    grp.add_argument("--from-stdin", action="store_true",
                     help="Read a Slack thread JSON blob from stdin.")
    ap.add_argument("--my-handle", default=None,
                    help="Your Slack handle (without @). Used to identify the AE "
                         "as 'the message author who isn't you'. Defaults to env "
                         "SLACK_HANDLE if set.")
    args = ap.parse_args()

    my_handle = args.my_handle
    if not my_handle:
        import os
        my_handle = os.environ.get("SLACK_HANDLE") or _from_env_file("SLACK_HANDLE")

    try:
        if args.text:
            text = sys.stdin.read()
            proposal = parse_text(text)
        elif args.file:
            text = Path(args.file).read_text()
            proposal = parse_text(text)
        else:
            payload = json.load(sys.stdin)
            proposal = parse_slack_thread(payload, my_handle=my_handle)
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed JSON on stdin: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    required_missing = [f for f in REQUIRED_FOR_BOOTSTRAP if f in proposal.get("missing", [])]
    if required_missing:
        print(
            f"WARNING: required fields missing: {required_missing}. "
            f"Proposal still emitted; bootstrap will fail until they're filled.",
            file=sys.stderr,
        )

    print(json.dumps(proposal, indent=2))


def _from_env_file(key: str) -> str | None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return None


if __name__ == "__main__":
    main()
