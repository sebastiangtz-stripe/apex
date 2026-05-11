# Runbook — File Templates

Reference templates for every project file. Point CLAUDE.md or skills here rather
than inlining (this is stable content that doesn't need to live in every conversation
context).

## PROJECT.md

```markdown
# [Merchant Name]

## Overview
- **Account ID(s)**: [acct_xxx]
- **Products**: [e.g., Payments, Connect, Billing]
- **Status**: [Discovery / Integration / Testing / Go-Live / Live / On Hold]
- **Priority**: [High / Medium / Low]
- **Started**: [date]
- **Due**: [YYYY-MM-DD]
- **AONR**: [$estimated annual revenue]
- **SFDC Opportunity Owner**: [AE name]

## Key Contacts
- [Name — role — email]

## Communication
- **Email search**: [Gmail query — see Email Query Format below]
- **Slack channels**: [channels]
- **Stripe contacts**: [names]

## External Links
- Handover: [Slack permalink from the handover thread, e.g. https://stripe.slack.com/archives/C0.../p1...]
- Manifest: [https://admin.corp.stripe.com/account-manifest/accma_xxx]
- Salesforce: [https://stripe.lightning.force.com/lightning/r/Opportunity/<opp_id>/view]
- Kantata Project ID: [numeric ID from Hubble, e.g. 45320322]
- Kantata Workspace: [https://app.mavenlink.com/workspaces/<kantata_id>]
- CSAT: [survey link]

## Product Activation
- [ ] [Product Name]

## Notes
[Free-form notes]
```

## Optional sections

### Related Projects (when same merchant has multiple Accelerate engagements)

Insert just below Overview when applicable. Documented in [`merge-slugs.md`](./merge-slugs.md):

```markdown
## Related Projects

This merchant has more than one Accelerate engagement. They are distinct deals — do not merge.

- **<other-slug>** ([projects/active/<other-slug>/PROJECT.md](../<other-slug>/PROJECT.md)) — <one-line reason>
```

## Other file formats

| File | Format |
|---|---|
| `timeline.md` | `## [YYYY-MM-DD] — [type]` with Source, Direction, Summary, Key Decisions (optional) |
| `action-items.md` | `- [ ] #tag — [Description] — Complexity: [L/M/H] — Owner: [who] — Due: [date] — Source: [ref]` (full spec in [`action-items-format.md`](./action-items-format.md)) |
| `issues/*.md` | Status, Reported, Description, Investigation Notes, Resolution |
| `drafts/*.md` | Context, Research, Sources, Draft, Sent (date/to/via). The `## Sent` section MUST be populated when the draft is actually sent — the stale-draft sweeper depends on it. |
| `raw/comms.md` | `## [YYYY-MM-DD] — [type] — [subject]` with From, To, Date, full body. Entries separated by `---`. |
| `commitments.md` | `- [ ] <YYYY-MM-DD made_on> — Promised: <due> — "<promise>" — Source: <ref> — Status: open\|fulfilled\|overdue` (auto-maintained by scan-review) |
| `specialist-runs.json` | `{ runs: [{ date, topic, prompt_path, hypothesis_path, claims_count, diagnostics_count, primary_deliverable, trigger, status, agent_id, output_path, outcome }] }` |
| `asana.json` | `{ task_gid, project_gid, section, subtask_gids: { "<key>": "<gid>" } }` |
| `scan-state.json` | `{ last_email_scan, last_slack_scan, logged_email_ids: [], logged_slack_thread_ids: [] }` |
| `hubble.json` | `{ project_id, project_name, sfdc_opp_link, kantata_workspace_link, csat_link, account_segment, accelerate_type, project_geography, last_synced }` |
