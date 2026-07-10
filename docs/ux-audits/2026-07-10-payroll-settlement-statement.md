# UX Psychology Audit Report
- Framework version: 1
- Scope mode: C targeted — the payroll settlement-statement surfaces built this session: StatementDrawer.tsx, the Deductions tab + run-items grid in Payroll.tsx, and the bot `/payroll` paystub in interfaces/bot/payroll.py. Code audit (`[code]`); no browser this session.
- Date: 2026-07-10 | Auditor session: payroll-settlement (Phases A–C)
- Surfaces audited: 4 | Not yet audited: run finalize/cancel flow; Bonus Rules & Driver Settings tabs (pre-existing)

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Statement drawer | recurring use | N/A | N/A | N/A | APPLIED | OPPORTUNITY | APPLIED |
| Deductions tab | decision point | OPPORTUNITY | N/A | N/A | APPLIED | OPPORTUNITY | N/A |
| Run-items grid | recurring use | N/A | N/A | N/A | N/A | APPLIED | APPLIED |
| Bot paystub | recurring use | N/A | N/A | APPLIED | N/A | N/A | APPLIED |

## Findings
### Statement drawer (StatementDrawer.tsx)
- **[P1 Smart Defaults — N/A]** Read-only document, no inputs.
- **[P2 Goal Gradient — N/A]** Not a multi-step flow.
- **[P3 Reciprocity — N/A]** Internal accounting tool.
- **[P4 IKEA Effect — APPLIED]** `[code]` The statement is built entirely from the account's own data — each load line, each addition/deduction they entered, their pay model applied — so it reads as *their* books, not a generic report.
- **[P5 Loss Aversion — OPPORTUNITY]** `[code]` The CSV filename embeds the driver + period but the print header doesn't stamp *when* it was generated or the run's finalized/draft status — a printed draft can be mistaken for a final settlement and paid twice, or a stale reprint passed off as current. Add a small header line "Draft · generated 2026-07-10" (draft in `warn` tone) so a paper copy self-identifies. `Impact: med · Effort: S`
- **[P6 Contrast Effect — APPLIED]** `[code]` The Gross → Deductions → **Net pay** footer orders the numbers so Net (the amount that matters) is last, bold, and larger — the eye lands on the payable figure, with deductions shown in `danger` tone as the subtraction that produced it. Honest, not manipulative.

### Deductions tab (Payroll.tsx)
- **[P1 Smart Defaults — OPPORTUNITY]** `[code]` Two friction points: (a) **date is blank** for a one-off — default to *today* (an advance is usually entered the day it's given). (b) The **recurring checkbox disables the date** but there's no default cadence hint — fine as-is, but pre-selecting `advance` as the kind (already done) is good; extend the same to a today-date default. `Impact: low · Effort: S`
- **[P2 Goal Gradient — N/A]** Single-row entry.
- **[P3 Reciprocity — N/A]** Internal.
- **[P4 IKEA Effect — APPLIED]** `[code]` Recurring deductions (insurance/escrow) are entered once and auto-apply every run — the operator's setup persists and does work for them, which is the good kind of invested-effort payoff.
- **[P5 Loss Aversion — OPPORTUNITY]** `[code]` Delete is a single trash-icon click with no confirm — removing a recurring insurance deduction silently changes every future run's net pay. Add an Undo toast (re-POST the same row) or a two-tap confirm, matching the load line-item finding from the 2026-07-08 audit. `Impact: med · Effort: S`
- **[P6 Contrast Effect — N/A]** No comparative choice set.

### Run-items grid (Payroll.tsx RunsTab)
- **[P1 Smart Defaults — N/A]** Read-only table.
- **[P2/P3/P4 — N/A]** Display surface, no flow/gate/customization.
- **[P5 Loss Aversion — APPLIED]** `[code]` The Deductions column renders in `danger` tone with a leading "−", making withheld money visually distinct from earnings — the operator can't miss that a driver's net is reduced.
- **[P6 Contrast Effect — APPLIED]** `[code]` Columns run Base · Loads · Extras · Bonus · **Gross** · Deductions · **Net** — the derivation reads left-to-right and Net (bold) is the terminal, emphasized figure, so the row's "answer" is unambiguous.

### Bot paystub (interfaces/bot/payroll.py)
- **[P1/P2 — N/A]** Read-only Telegram message, no inputs or steps.
- **[P3 Reciprocity — APPLIED]** `[code]` The driver gets a complete, itemized statement — every load they hauled with its pay, additions, deductions, and net — pushed to them for free in Telegram, no login/app. Real value delivered where the driver already is; builds trust in the pay process.
- **[P4 IKEA Effect — N/A]** Driver doesn't build anything here.
- **[P5 Loss Aversion — N/A]** Informational; no action to lose.
- **[P6 Contrast Effect — APPLIED]** `[code]` The message ends with Gross then **bold Net pay** — the last, emphasized line is what they're actually paid, matching the dashboard statement's ordering so driver and accountant read the same shape.

## Ethics gate
No dark patterns. The Net-pay emphasis and deductions-in-danger-tone are honest disclosure — they surface real subtractions to both the accountant AND the driver, the opposite of hiding fees. The proposed loss-aversion fixes protect against accidental double-pay / silent net changes, i.e. protect the user's own money.

## Top 3 actions (highest impact first)
1. Draft/generated stamp on the printed statement (P5, drawer) — a printed draft mistaken for final is a real double-pay risk. `med · S`
2. Undo/confirm on deduction delete (P5, deductions) — a mis-tapped recurring delete silently changes every future net. `med · S`
3. Today-date default on one-off deductions (P1, deductions). `low · S`

## NEEDS-CONTEXT items
- The run finalize action (`POST /payroll/runs/{id}/finalize`) — does finalizing lock the statement snapshot against a recompute, and does the UI warn that finalize is irreversible? Needs the finalize handler + its confirm UI.
