# UX Psychology Audit Report
- Framework version: 1
- Scope mode: A session — Vehicle Inventory component (InventoryCard + Add/Item dialogs, fleet-list attention badge, `features/vehicles/inventory/`)
- Date: 2026-07-14 | Auditor session: vehicle-inventory
- Surfaces audited: 4 | Not yet audited: Phase-2 hooks (PTI auto-verify, damaged→work-order, missing-item alert)

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Inventory card (vehicle detail) | recurring use / decision point | APPLIED | OPPORTUNITY | N/A | APPLIED | APPLIED | APPLIED |
| Add / Item dialogs | decision point | APPLIED | N/A | N/A | APPLIED | APPLIED | N/A |
| Fleet-list attention badge | recurring use | APPLIED | N/A | N/A | N/A | APPLIED | APPLIED |

## Findings
### Inventory card
- **[P1 Smart Defaults — APPLIED]** Fixed category set (camera / fuel card / toll / ELD / tablet / other) means no blank-taxonomy paralysis; new items default to `installed`; the empty state explains WHY to fill it ("so swaps and losses stay accountable") rather than showing a bare "no data". `Impact: — · Effort: —`
- **[P2 Goal Gradient — OPPORTUNITY]** No "inventory completeness" nudge — a truck with 1 tracked item and a truck with all 6 categories covered look equally "done". A subtle per-category hint (e.g. grey ghost rows or "ELD not tracked yet") would pull managers to finish the setup. Defer to Phase 2 — needs a per-account "expected categories" notion to avoid nagging fleets that genuinely lack tablets. `Impact: med · Effort: M`
- **[P4 IKEA Effect — APPLIED]** The manager builds each truck's inventory by hand (labels, serials, notes) — the list is visibly *their* record, and the event trail preserves their work permanently. `Impact: — · Effort: —`
- **[P5 Loss Aversion — APPLIED]** The entire feature is honest loss-surfacing: `missing`/`damaged` chips in danger tone, `verified · 41d ago` staleness via Freshness, "never verified" called out. No fake urgency anywhere. `Impact: — · Effort: —`
- **[P6 Contrast Effect — APPLIED]** Healthy rows are quiet; attention rows carry tone chips + Freshness cues — the eye lands on exactly the items that need action. `Impact: — · Effort: —`

### Add / Item dialogs
- **[P1 Smart Defaults — APPLIED]** Identifier field teaches its own purpose ("what makes THIS unit provable"); note placeholder recommends itself for damaged/missing. `Impact: — · Effort: —`
- **[P4 IKEA — APPLIED]** History timeline shows the manager's own past actions with names — reinforces ownership. `Impact: — · Effort: —`
- **[P5 Loss Aversion — APPLIED]** Status changes to damaged/missing prompt a reason note for the permanent trail; remove is soft and says the trail survives. `Impact: — · Effort: —`

### Fleet-list attention badge
- **[P1 Smart Defaults — APPLIED]** Shown ONLY when attention > 0 — zero noise on healthy fleets (the same only-when-it-matters rule as the Freshness dot). `Impact: — · Effort: —`
- **[P5/P6 — APPLIED]** A danger-tone count next to the unit number makes a truck with missing gear visually distinct in the list where dispatch decisions happen. `Impact: — · Effort: —`

### Fleet-wide Inventory page (same-day addendum)
Sidebar entry under Vehicles → /vehicles/inventory: DataGrid of every item
across the fleet (search by identifier/truck; category/company/status
filters; verification Freshness). Read/locate surface only — row click
jumps to the truck, where the card owns all actions (single editing home,
no dual-write surface: an IKEA/consistency decision). P1 APPLIED (filters
by cardinality per house rules); P5 APPLIED (missing/damaged one filter
away, fleet-wide); P6 APPLIED (tone chips make trouble rows pop in the
grid). No dark patterns.

## Top 3 actions (highest impact first)
1. Phase 2: PTI "inventory present" template item → auto-verify on passed inspection (turns weekly driver walk-arounds into free verifications). `Impact: high · Effort: M`
2. Phase 2: damaged → one-click Work Order; missing → owner alert. `Impact: med · Effort: M`
3. Completeness nudge per category (see P2 finding). `Impact: med · Effort: M`

## NEEDS-CONTEXT items
- none
