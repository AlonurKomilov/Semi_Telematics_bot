# UX Psychology Audit Report
- Framework version: 1
- Scope mode: A session — "Billing → Subscription" display rename (nav label ×9 locales, page title, permissions-matrix row, Telegram billing notifications, co-owner emails, operator Scheduler categories)
- Date: 2026-07-10 | Auditor session: subscription-rename
- Surfaces audited: 4 | Not yet audited: none

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Sidebar nav "Subscription" | recurring use | APPLIED | N/A | N/A | N/A | N/A | APPLIED |
| Matrix row "Subscription" | decision point | APPLIED | N/A | N/A | N/A | N/A | APPLIED |
| Telegram payment/comp notifications | decision point | N/A | N/A | N/A | N/A | APPLIED | N/A |
| Operator Scheduler categories | recurring use | N/A | N/A | N/A | N/A | N/A | APPLIED |

## Findings
### Sidebar nav "Subscription"
- **[P1 Smart Defaults — APPLIED]** The label now names what the page IS (the account's plan), removing the "Billing = invoices to my brokers?" mis-read a carrier owner could make. `Impact: — · Effort: —`
- **[P6 Contrast Effect — APPLIED]** "Subscription" and "Payroll" no longer share a money-word family in the sidebar — the two adjacent concepts are now visually and semantically distinct. `Impact: — · Effort: —`
- **[P2/P3/P4/P5 — N/A]** Label rename; no flow, gate, authorship, or loss framing involved.

### Permissions-matrix row "Subscription"
- **[P1 Smart Defaults — APPLIED]** Row description "The account's plan & payment — not driver pay (that's Payroll)" pre-answers the exact confusion an admin would hit granting money-related permissions. `Impact: — · Effort: —`
- **[P6 Contrast Effect — APPLIED]** The Accounting band's "Subscription" row vs the separate "Payroll" band now read as different domains, not near-duplicates. `Impact: — · Effort: —`

### Telegram payment/comp notifications
- **[P5 Loss Aversion — APPLIED]** Copy unchanged in substance (grace-window and suspension consequences already stated honestly); the link label "Subscription page" now matches the destination page's actual name, so the recovery path is unambiguous at the moment of a payment failure. No fake urgency present. `Impact: — · Effort: —`
- **[P1–P4, P6 — N/A]** Transactional notices; no defaults, flow, gate, authorship, or choice set.

### Operator Scheduler categories ("Payroll" vs "Platform billing")
- **[P6 Contrast Effect — APPLIED]** Tenant subsystems listed first, platform subsystems last — the console's grouping now teaches the system/account boundary instead of blurring it. `Impact: — · Effort: —`

## Top 3 actions (highest impact first)
1. None required — this change *is* a confusion-reduction pass; no dark patterns, no regressions found.
2. (Optional, future) When/if carrier invoicing ships, audit that its naming stays clear of "Subscription".
3. —

## NEEDS-CONTEXT items
- none
