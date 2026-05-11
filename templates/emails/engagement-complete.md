# Engagement Complete Email

Sent when a Stripe Accelerate engagement is wrapping. Two variants — pick based on merchant state:

- **Variant A — Successful wrap**: merchant is live or near-live, integration shipped, real progress to point at. Tone: warm, congratulatory, hand-off oriented.
- **Variant B — No-traction close**: silent / never engaged / scope evaporated. Tone: respectful, low-pressure, leaves the door open without begging for engagement.

Always cc the SFDC Opportunity Owner (AE) and any active Stripe contacts (CSM, AM) so the merchant has a path forward after Accelerate steps out.

Source for placeholder values: `projects/active/<slug>/PROJECT.md` (Key Contacts, SFDC Opportunity Owner, Products) + most recent `timeline.md` highlight.

---

## Variant A — Successful wrap

```
Subject: Stripe Accelerate — Wrapping our {{merchant_name}} engagement

Hi {{first_name}},

I'm writing to formally wrap our Stripe Accelerate engagement on {{merchant_name}}. From our side this has been a successful one — {{1-sentence highlight: e.g. "you went live on Payments + Tax in March and have been running cleanly since"}}.

A few notes as we hand off:
- **Day-to-day support**: {{ae_name}} ({{ae_email}}) remains your account owner at Stripe and is the right first stop for anything strategic. For implementation/technical questions, support.stripe.com is the fastest path.
- **What we covered together**: {{1-2 bullets pulled from timeline — e.g. "Tax registration in 12 states", "Connect onboarding flow review", "Go-live runbook"}}.
- **Open items, if any**: {{none / 1-2 lingering threads with owner}}.

If something comes up where you'd like another set of eyes from the Accelerate side, just reply to this thread — happy to loop back in.

Thanks for the partnership and best of luck with what's next.

Best,
[YOUR_NAME]
Stripe Accelerate
```

---

## Variant B — No-traction close

```
Subject: Stripe Accelerate — Closing out for now, {{merchant_name}}

Hi {{first_name}},

Given we haven't been able to find a window to dig in together, I'm closing out our Stripe Accelerate engagement on {{merchant_name}} on my side. No worries on timing — I know things move.

A few notes:
- **Going forward**: {{ae_name}} ({{ae_email}}) is your AE at Stripe and the right contact if priorities shift or you want to re-open the conversation. For day-to-day technical questions, support.stripe.com is the fastest path.
- **Door is open**: if Accelerate becomes useful again — new product, scaling moment, integration question — reply to this thread or reach out to {{ae_name}} and I can re-engage.

Wishing you the best with {{merchant_name}}.

Best,
[YOUR_NAME]
Stripe Accelerate
```

---

## Placeholders

| Token | Source |
|---|---|
| `{{merchant_name}}` | PROJECT.md H1 |
| `{{first_name}}` | PROJECT.md Key Contacts (primary contact) |
| `{{ae_name}}` / `{{ae_email}}` | PROJECT.md SFDC Opportunity Owner + lookup |
| `{{1-sentence highlight}}` | most recent meaningful timeline.md entry |
| `{{1-2 bullets pulled from timeline}}` | timeline.md scan for shipped/decided items |

## When to use this template

- Action item carries `#email` tag with description like "Notify <merchant> engagement is complete" or similar wrap/handoff language
- PROJECT.md `Status` field has been moved to `Completed` and Asana parent is in the **Completed** section but not yet archived
- After this email goes out + any reply is logged, the project is ready for `/lessons-extract` and the move to `projects/archive/`
