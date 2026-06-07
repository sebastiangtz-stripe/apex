# Example Merchant

> This folder is a worked example showing the canonical project layout. Delete
> it once you've created your own real merchant projects, or keep it as a
> reference. Real merchant data is gitignored — only this `example-merchant/`
> folder is tracked.

## Overview
- **Account ID(s)**: acct_1ExampleAccountId123
- **Products**: Payments, Connect
- **Status**: Integration
- **Priority**: Medium
- **Started**: 2026-01-15
- **Due**: 2026-04-15
- **AONR**: $50,000
- **SFDC Opportunity Owner**: [AE name]

## Key Contacts
- [Primary contact name] — [role] — primary@example.com
- [Engineering contact] — [role] — engineering@example.com

## Communication
- **Scan source**: managed
- **Email search**: from:example.com OR to:example.com
- **Slack channels**: #gamma-llc-stripe
- **Stripe contacts**: [your AE], [your SE]

## External Links
- Kantata Project ID: 99999999
- Kantata Workspace: https://app.mavenlink.com/workspaces/99999999
- Salesforce: https://stripe.lightning.force.com/lightning/r/Opportunity/REPLACE/view
- Dashboard: https://dashboard.stripe.com/REPLACE
- CSAT: https://stripe.co1.qualtrics.com/jfe/form/REPLACE

## Product Activation
- [ ] Payments
- [ ] Connect Standard

## Notes

This is a reference scaffold. Use it as a starting point for new merchant
folders. The conversational mappings in [`CLAUDE.md`](../../../CLAUDE.md) will
fill in real values when you say *"new project: <merchant name>, <acct_id>"* —
or copy this folder manually:

```bash
cp -R projects/active/example-merchant projects/active/<your-merchant-slug>
```
