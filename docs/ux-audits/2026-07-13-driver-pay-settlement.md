# UX Psychology Audit Report
- Framework version: 1
- Scope mode: A session — the Driver Pay settlement surfaces shipped this session (driver picker, CSV export, delete-confirm, inline draft edit, zero-pay banner, print stamp, dollars input)
- Date: 2026-07-13 | Auditor session: driver-pay-ritual-closeout
- Surfaces audited: 7 | Not yet audited: bot `/driver-pay` my-pay message; miniapp MyPaystubs card (read-only, low behavioral surface)

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Set Driver Pay form | recurring config | APPLIED | N/A | N/A | APPLIED | OPPORTUNITY | N/A |
| Create draft-run form | decision point | OPPORTUNITY | N/A | N/A | N/A | N/A | N/A |
| Runs list | recurring | N/A | OPPORTUNITY | N/A | N/A | N/A | N/A |
| Run detail + Export CSV | decision point | APPLIED | N/A | APPLIED | N/A | APPLIED | N/A |
| Statement drawer | decision point | APPLIED | N/A | APPLIED | APPLIED | APPLIED | N/A |
| Inline "Add to statement" | decision point | APPLIED | N/A | N/A | APPLIED | OPPORTUNITY | N/A |
| Delete line-item confirm | exit/destructive | N/A | N/A | N/A | N/A | APPLIED | N/A |

## Findings

### Set Driver Pay form
- **[P1 Smart Defaults — APPLIED]** New roster datalist means a driver is chosen by name (with a "→ Name" confirm), not a hand-typed Samsara id; `opt_in` defaults true. Blank-decision friction removed. `Impact: — · Effort: —`
- **[P1 Smart Defaults — OPPORTUNITY]** `pay_model`/`pay_rate` start empty. For Datatruck-linked drivers the payment tariff is already known — pre-fill the pay model/rate from it so the common case needs no typing. `Impact: med · Effort: M` (needs backend to expose per-driver tariff)
- **[P5 Loss Aversion — OPPORTUNITY]** A driver saved with `opt_in=true` but no base pay AND no pay model silently resolves to $0 in every run. The form is where this is *preventable*; today the warning only appears later in the statement. Add an inline caution on save/row: "No pay basis — this driver will settle to $0." `Impact: med · Effort: S`
- **[P4 IKEA — APPLIED]** Per-driver pay setup (model, rate, base) is visibly theirs in the settings grid; configuring it is real investment. `Impact: — · Effort: —`
- **[P2 Goal / P3 Reciprocity / P6 Contrast — N/A]** Single-step config surface, no gate, no choice set.

### Create draft-run form
- **[P1 Smart Defaults — OPPORTUNITY]** `periodStart`/`periodEnd` both default to `''` (DriverPay.tsx:208-209), so every single run forces two manual date picks — yet "the previous calendar month" is the overwhelmingly common driver-pay period. Pre-fill last month (1st→last day); the user overrides only for off-cycle runs. Highest-leverage fix on these surfaces. `Impact: high · Effort: S`
- **[P2–P6 — N/A]** Single creating action; no progress arc, gate, ownership, loss, or choice set at this step.

### Runs list
- **[P2 Goal Gradient — OPPORTUNITY]** A run's life is a real arc (draft → finalized → paid), but the list shows only a status word. A lightweight lifecycle chip would show "where each run is" and what's left to do. Blocked on payment-tracking (paid state doesn't exist yet). `Impact: low · Effort: M`
- **[P1/P3/P4/P5/P6 — N/A]** Read-only record list.

### Run detail + Export CSV
- **[P3 Reciprocity — APPLIED]** "Export CSV" hands the accountant a real, usable deliverable (per-driver batch) directly from the run — value delivered, not gated. `Impact: — · Effort: —`
- **[P1 Smart Defaults — APPLIED]** Export filename is pre-composed (`driver-pay-run-{id}_{start}_to_{end}.csv`) — no naming decision. `Impact: — · Effort: —`
- **[P5 Loss Aversion — APPLIED]** Finalize confirm states the consequence ("cannot be edited afterward"); cancel confirms too. Honest, specific. `Impact: — · Effort: —`
- **[P2/P4/P6 — N/A]**

### Statement drawer
- **[P5 Loss Aversion — APPLIED]** Two strong, *pro-user* loss cues: the zero-pay banner ("N delivered loads resolved to $0 — they'll be underpaid") frames the driver's loss, and the draft/final print stamp + generated date prevents paying a draft twice. `Impact: — · Effort: —`
- **[P4 IKEA — APPLIED]** Inline editor lets the operator shape the statement (add/adjust) in place — it becomes *their* reconciled document. `Impact: — · Effort: —`
- **[P3 Reciprocity — APPLIED]** Full computed settlement (base + loads + extras + bonuses − deductions = net) is shown before any finalize/pay ask. `Impact: — · Effort: —`
- **[P1 Smart Defaults — APPLIED]** No blank state — statement is fully computed; date auto-stamped. `Impact: — · Effort: —`
- **[P2 Goal / P6 Contrast — N/A]** Document view, no multi-step arc or choice set.

### Inline "Add to this statement" editor
- **[P4 IKEA — APPLIED]** Fixing a forgotten entry in place (no round-trip to the load) makes the statement feel authored, not generated. `Impact: — · Effort: —`
- **[P1 Smart Defaults — APPLIED]** Sensible default entry kind (first addition); recompute is automatic. `Impact: — · Effort: —`
- **[P5 Loss Aversion — OPPORTUNITY]** An added deduction immediately lowers net with no in-drawer undo (removal lives back on the load). Low urgency, but a toast with undo would soften a mis-entry. `Impact: low · Effort: M`
- **[P2/P3/P6 — N/A]**

### Delete line-item confirm (LoadManageDialog)
- **[P5 Loss Aversion — APPLIED]** The new confirm names the exact stake ("Remove detention ($150.00)? This can't be undone.") — concrete loss framing that replaced a silent one-click money delete. `Impact: — · Effort: —`
- **[P1/P2/P3/P4/P6 — N/A]** Single destructive action.

## Ethics gate
No dark patterns. Notably the strongest behavioral cue — the zero-pay banner — works *for the driver* (prevents silent underpayment) even though the operator is the employer; it would be comfortable to explain to either party. Delete-confirm and finalize warnings are honest consequence statements, not confirm-shaming. All defaults are the safe/honest choice, none pre-check a business-favorable option.

## Top 3 actions (highest impact first)
1. **Create-run date defaults** — pre-fill last calendar month into `periodStart`/`periodEnd`. `Impact: high · Effort: S` — mine, no migration, no co-dev overlap.
2. **No-pay-basis caution in Set Driver Pay** — flag a driver saved with opt-in but no base + no pay model, at config time. `Impact: med · Effort: S`
3. **Tariff pre-fill of pay model** — default pay model/rate from the driver's Datatruck tariff. `Impact: med · Effort: M` — needs backend to expose per-driver tariff.

## NEEDS-CONTEXT items
- Payment-tracking (mark-paid) state is not built yet, so the runs-list Goal-Gradient chip (draft→finalized→**paid**) can't be completed until those columns exist.
