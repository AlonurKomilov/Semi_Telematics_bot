# UX Psychology Audit Report
- Framework version: 1
- Scope mode: C targeted — the two Loads money-entry surfaces built this session: the "Extra pay & costs" line-item editor in `LoadManageDialog.tsx` and `LayoverDialog.tsx` (+ its "Add layover" entry button on `Loads.tsx`). Code audit (`[code]`); no browser available this session.
- Date: 2026-07-08 | Auditor session: loads-line-items (Package A)
- Surfaces audited: 2 | Not yet audited: Loads page gross/extra-pay display columns (not yet surfaced in the table), KPI page impact of layover rows

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Extra pay & costs (load dialog) | recurring use | APPLIED | N/A | N/A | OPPORTUNITY | OPPORTUNITY | N/A |
| Layover dialog | recurring use | OPPORTUNITY | N/A | N/A | N/A | APPLIED | N/A |

## Findings
### Extra pay & costs (LoadManageDialog section)
- **[P1 Smart Defaults — APPLIED]** `[code]` Kind pre-selects `tonu` (most common), bucket auto-derives from kind server-side, item date + driver + dispatcher inherit from the load — the operator only types an amount. Honest defaults; no business-favoring pre-selection.
- **[P2 Goal Gradient — N/A]** Single-row entry, no multi-step flow to show progress on.
- **[P3 Reciprocity — N/A]** Internal permission-gated tool; nothing is asked of the user beyond the record itself.
- **[P4 IKEA Effect — OPPORTUNITY]** `[code]` After adding an item the list refreshes but the dialog never shows the *effect* — the load's updated gross. Showing "Gross: $2,160 (was $2,310)" under the section would make each entry's impact visible and make the cost record feel like the operator's own bookkeeping, not a void. Concrete change: after `addItem`/`removeItem`, recompute `rate − pay − costs − Σitems` from the dialog's own draft + items state and render one muted line. `Impact: med · Effort: S`
- **[P5 Loss Aversion — OPPORTUNITY]** `[code]` The per-item Remove (12px trash icon) deletes a money record instantly — no confirm, no undo. Mis-taps silently change a load's gross and the dispatcher's KPI. Concrete change: either a `toast` with an Undo action (re-`POST` the same item), or a two-tap confirm on the icon (first tap arms, second deletes) like other destructive rows in the app. `Impact: med · Effort: S`
- **[P6 Contrast Effect — N/A]** No comparative choice set; the kinds dropdown is a flat vocabulary, already ordered most-used-first.

### Layover dialog (+ "Add layover" button on Loads page)
- **[P1 Smart Defaults — OPPORTUNITY]** `[code]` Three blanks the operator fills every time that have obvious defaults: (a) **Date** — default to *today* (layover is usually entered same/next day; one keystroke saved every use); (b) **Amount** — remember the last-used layover amount per account (fleets pay a standard day rate, e.g. $250) via localStorage or account setting; (c) **Dispatcher** — when a driver is picked, pre-select the dispatcher most recently attached to that driver's loads (data already on screen in `people`). Also note a functional edge: options derive from loads currently loaded, so a brand-new driver with zero loads isn't pickable — acceptable v1 tradeoff, but worth a hint line if support tickets appear. `Impact: med · Effort: S`
- **[P2 Goal Gradient — N/A]** One-shot form.
- **[P3 Reciprocity — N/A]** Internal tool, no gate.
- **[P4 IKEA Effect — N/A]** A layover record is an event log entry, not a user-built artifact; no attachment value to cultivate.
- **[P5 Loss Aversion — APPLIED]** `[code]` The dialog states plainly: "It counts against the responsible dispatcher's KPI for that day." Real consequence, disclosed at the moment of entry, to the person entering it — honest use of consequence-framing, and it doubles as an accuracy incentive (attribute the right dispatcher). Passes the explain-to-their-face test.
- **[P6 Contrast Effect — N/A]** No choice set.

## Ethics gate
No dark patterns found. The KPI-attribution disclosure is the opposite of one — it surfaces the consequence *before* the action. The delete-without-confirm finding is an accident risk, not manipulation.

## Top 3 actions (highest impact first)
1. Undo/confirm on line-item removal (P5, dialog) — money records shouldn't die on one mis-tap. `med · S`
2. Layover smart defaults: date=today, remembered amount, auto-picked dispatcher from the driver's recent loads (P1). `med · S`
3. Show the load's recalculated gross inside the Extra pay & costs section after add/remove (P4). `med · S`

## NEEDS-CONTEXT items
- Whether the Loads table should show an "extras" indicator column (the API now returns `extra_driver_pay`/`extra_costs`, but no column renders them) — needs a product call on table density before proposing.
