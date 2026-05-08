---
name: log-comms
description: >-
  Lightweight inbox for pasted communications. Append-only writes to
  raw/comms.md and timeline.md without invoking the full /merchant-scanner
  (which is heavier and depends on Gmail/Slack MCP). Use when the user pastes
  a transcript, email body, or Slack thread they want logged, or says "log
  that conversation with X", "append this to <merchant>", "save this to comms".
---

# Log Comms

Cheaper alternative to `/merchant-scanner` when the content is already in hand. The scanner exists to fetch + dedup + log new items from Gmail/Slack APIs. This skill exists to log content the user already pasted, no API calls required.

## Inputs

The user provides any of these, sometimes implicitly:

- **`slug`** (required): kebab-case merchant slug. If ambiguous, ask once. Apply the Product Brand Disambiguation rule from CLAUDE.md when a brand name is given instead of a slug.
- **`type`** (required): one of `email`, `slack`, `meeting`, `call`, `note`. If the pasted content has email headers (`From:`, `To:`, `Date:`), default to `email`.
- **`subject`** (required for `email` and `slack`): the subject line / channel + thread reference.
- **`direction`** (required): `Inbound` (merchant → us), `Outbound` (us → merchant), `Internal` (Stripe-only thread referencing the merchant).
- **`from`**, **`to`**, **`date`** (required for `email`): standard email headers.
- **`message_id`** (optional, encouraged for `email`): Gmail message ID for future dedup. If absent, mark `TBD` so the next `/merchant-scanner` run can backfill.
- **`permalink`** (optional, encouraged for `slack`): channel + ts permalink.
- **`body`** (required): the full message content, verbatim.
- **`significance`** (optional): 1-paragraph synthesis the agent adds. Useful for capturing your own gloss on what the message means.

If anything required is unclear from the user's message, ask once with a tight question. Don't guess `direction` or `type` from ambiguous content.

## Workflow

### 1. Resolve the slug

If the user gave a slug, validate it exists under `projects/active/`. If they gave a brand or merchant name, apply Product Brand Disambiguation per CLAUDE.md. Never silently fall back.

### 2. Compose the `raw/comms.md` entry

Append (never overwrite) using this exact format:

```
## [YYYY-MM-DD] — <type> — <subject> (<direction-phrase>)

- **From**: <from>
- **To**: <to>
- **Date**: <full date including TZ if available>
- **Message-ID**: <id or TBD>
- **Link**: <gmail web URL or "TBD">    # for type=email
- **Permalink**: <slack permalink>       # for type=slack
- **Channel**: <#channel or DM ref>      # for type=slack

> <body, indented as blockquote OR rendered as code block if it's an error / API response>

**Significance**: <agent's 1-paragraph synthesis if user wants it; omit otherwise>

---
```

Notes:
- Always separate entries with `---` on its own line.
- Indent the body as a `>` blockquote so the entry's structure stays scannable.
- For email replies, preserve the literal `Re:` and threading subject. Don't normalize.
- For long emails (>500 lines), include the full body anyway — `raw/comms.md` is meant to be the verbatim record.

### 3. Compose the `timeline.md` entry

Append (never overwrite) using:

```
## [YYYY-MM-DD] — <type>

- **Source**: <subject> / <thread ref>
- **Direction**: <Inbound | Outbound | Internal>
- **Summary**: <1-3 sentence summary of the content + why it matters>
- **Key Decisions** (optional): <if any decision was made or recorded>
```

### 4. Update `scan-state.json`

To avoid re-logging when the next `/merchant-scanner` runs Gmail/Slack search:
- For `type=email` with a real `Message-ID`: append the ID to `logged_email_ids`.
- For `type=email` with `Message-ID: TBD`: do NOT add to `logged_email_ids` (the next scanner run should pick it up and dedupe by content match — actually it may double-log; flag this in the response).
- For `type=slack` with permalink: parse `<channel_id>/<ts>`, append to `logged_slack_thread_ids`.
- Do NOT bump `last_email_scan` / `last_slack_scan` — those represent actual scanner runs, not manual logs.

### 5. Significance check (optional)

If the user asked for a significance note OR if the content clearly contains a question / commitment / decision, ask the user (tight prompt): "Want me to draft a 1-line significance note?" Don't write it without confirmation.

### 6. Asana comment (if significant)

For inbound merchant replies, escalations, decisions, or commitments — post a brief Asana comment to the merchant's task per CLAUDE.md dual-write rules. Skip for outbound, internal-only threads, and routine pings. Read `asana.json` for the task GID.

### 7. Return value

A 3-line confirmation:

```
Logged to projects/active/<slug>/raw/comms.md (~+N lines)
Timeline entry added (## [YYYY-MM-DD] — <type>)
scan-state.json updated (logged_email_ids/logged_slack_thread_ids: +1)
```

If a follow-up action item is implied (e.g. inbound merchant question that needs reply), surface it ("Looks like this needs a `#reply`. Want me to add one to action-items.md + Asana?") but do NOT auto-create.

## Hard rules

- **Append-only.** Never overwrite. Never reorder existing entries.
- **Verbatim body.** Never paraphrase the body. The whole point of `raw/comms.md` is verbatim.
- **No fetching.** This skill never calls Gmail / Slack APIs. If the content is missing fields you need, ask the user, don't try to fetch.
- **Apply Product Brand Disambiguation.** Per CLAUDE.md General Rules — if the slug is ambiguous, ask before writing.
- **One slug per call.** If the conversation references multiple merchants (e.g. AE handover thread mentioning two), ask the user which slug(s) to log to. Loop the call if needed.
- **No action item creation here.** Surface the suggestion, but routes through normal dual-write flows.
