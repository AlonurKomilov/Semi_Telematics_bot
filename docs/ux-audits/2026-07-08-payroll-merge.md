# UX Psychology Audit Report
- Framework version: 1
- Scope mode: C targeted — Payroll surfaces changed in the settlements merge: the "Set Driver Pay" editor (pay-model select + rate), the run-items grid (Loads/Extras columns + breakdown narration) in `Payroll.tsx`, and the "Payroll module" master switch on `Settings.tsx`. Code audit (`[code]`).
- Date: 2026-07-08 | Auditor session: payroll-merge (phases 1–2)
- Surfaces audited: 3 | Not yet audited: driver self-view (`/payroll/me` paystub) with the new components; Payroll empty state before the first run

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Set Driver Pay editor | decision point | OPPORTUNITY | N/A | N/A | APPLIED | N/A | N/A |
| Run items grid | recurring use | N/A | N/A | N/A | APPLIED | OPPORTUNITY | N/A |
| Payroll module switch | decision point | APPLIED | N/A | N/A | N/A | APPLIED | N/A |

## Findings
### Set Driver Pay editor
- **[P1 Smart Defaults — OPPORTUNITY]** `[code]` Three friction points where good defaults exist: (a) **Base pay is entered in CENTS** ("Base pay (cents)" — typing 100000 for $1,000 invites 100× errors on a money field); switch the input to dollars and convert on save. (b) **Driver ID is a raw text field** — a select fed from the Samsara roster endpoint (`/drivers/samsara`, exists) beats hand-typing an opaque id. (c) For drivers with a linked Datatruck tariff, **pre-fill pay model + rate** from `payment_tariff` (staged data) as the suggested default. `Impact: high · Effort: M`
- **[P2 Goal Gradient — N/A]** One-shot form, no multi-step flow.
- **[P3 Reciprocity — N/A]** Internal permission-gated tool.
- **[P4 IKEA Effect — APPLIED]** `[code]` The settings grid shows each driver's configured model ("28% of rate" / "$0.65/mi") — the operator's own pay structure is visible and preserved, not buried.
- **[P5 Loss Aversion — N/A]** No destructive action here; model changes affect future runs only.
- **[P6 Contrast Effect — N/A]** The three pay-model options are genuinely different mechanics, not tiers to anchor.

### Run items grid (statement)
- **[P1 Smart Defaults — N/A]** Read-only display.
- **[P2 Goal Gradient — N/A]** Not a progress flow.
- **[P3 Reciprocity — N/A]** Internal.
- **[P4 IKEA Effect — APPLIED]** `[code]` The Breakdown column narrates the operator's own rules and the computed components by name ("Score80 ($50.00), Load earnings ($1,400.00), Extra pay items ($450.00)") — the statement reads as *their* pay structure at work.
- **[P5 Loss Aversion — OPPORTUNITY]** `[code]` A delivered load with **no stored pay and no pay model contributes $0 silently** — the statement under-pays without warning. The engine already knows `loads_count` vs earnings: when a driver has delivered loads whose resolved pay is $0, render the Loads cell in the `warn` tone with a title "N loads had no pay figure — set a pay model or enter pay on the load". Real-money loss made visible at the moment it matters. `Impact: high · Effort: S`
- **[P6 Contrast Effect — N/A]** No choice set.

### Payroll module switch (Settings)
- **[P1 Smart Defaults — APPLIED]** `[code]` Default OFF is the honest choice for an opt-in module; no silent auto-enable.
- **[P2 Goal Gradient — N/A]** Single toggle.
- **[P3 Reciprocity — N/A]** Internal.
- **[P4 IKEA Effect — N/A]** Nothing to build here.
- **[P5 Loss Aversion — APPLIED]** `[code]` The copy states the consequence plainly: "When off, it's hidden for every role — even ones granted payroll access in the Permissions matrix." The exact confusion that prompted this card, disclosed at the decision point.
- **[P6 Contrast Effect — N/A]** No choice set.

## Ethics gate
No dark patterns. The zero-pay warning proposal surfaces a real loss to the account's own drivers — trust-building, not exploitative.

## Top 3 actions (highest impact first)
1. Zero-pay-loads warning on the statement (P5, run grid) — silent $0 earnings is the one genuinely dangerous gap. `high · S`
2. Dollars-not-cents input on Set Driver Pay (P1). `high · S` (subset of the P1 finding)
3. Driver picker + tariff pre-fill in the editor (P1). `med · M`

## NEEDS-CONTEXT items
- `/payroll/me` (driver self-view) — does the paystub render the new breakdown components readably for a driver? Needs `interfaces/bot/payroll.py` + the dashboard self-view path.
