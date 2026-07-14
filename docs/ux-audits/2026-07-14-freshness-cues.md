# UX Psychology Audit Report
- Framework version: 1
- Scope mode: A session — per-metric freshness cues (`components/Freshness.tsx`, `Row ts` prop, Vehicle Info card wiring)
- Date: 2026-07-14 | Auditor session: freshness-cues
- Surfaces audited: 5 | Not yet audited: miniapp (planned later)

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Freshness primitive (tooltip + escalating dot) | recurring use | APPLIED | N/A | N/A | N/A | APPLIED | APPLIED |
| Vehicle Info card (fuel/odometer/engine-hours/engine/DEF) | recurring use / decision point | APPLIED | N/A | N/A | N/A | APPLIED | N/A |

## Findings
### Freshness primitive
- **[P1 Smart Defaults — APPLIED]** Fresh readings (<1h) show nothing extra — zero decision fatigue in the healthy case; the cue only appears when the user actually needs to distrust a number. Escalation thresholds (1h dot, 24h dot+age) are pre-chosen sensible defaults, no configuration demanded. `Impact: — · Effort: —`
- **[P5 Loss Aversion — APPLIED]** This feature IS an honest loss-framing mechanism: instead of a stale value silently masquerading as truth (the user acting on a 5-day-old truck position — a real incident this week), the age is disclosed at the point of reading. Honest disclosure, no fake urgency. `Impact: — · Effort: —`
- **[P6 Contrast Effect — APPLIED]** Stale values become visually distinct from fresh ones (dot / inline age), so within one card the eye immediately separates trustworthy from doubtful readings — the comparison the card previously flattened. `Impact: — · Effort: —`
- **[P2/P3/P4 — N/A]** No flow, gate, or user authorship involved.

### Vehicle Info card
- **[P1 Smart Defaults — APPLIED]** Per-metric timestamps ride the existing payload (no new fetch); each row discloses its OWN clock — fuel can honestly show a different age than GPS. `Impact: — · Effort: —`
- **[P5 Loss Aversion — APPLIED]** The two production incidents this week (frozen warehouse; duplicate Samsara relic) both manifested as confidently-displayed stale data. With this card wired, both would have been visible as "· 5d ago" at first glance. `Impact: — · Effort: —`
- **[P6 Contrast — N/A]** Single-vehicle card; cross-row contrast covered under the primitive.

## Phase 3 addendum (same day)
Location card (one GPS clock: cue on Address, tooltip-only on Speed/Coordinates —
deliberate anti-noise choice), Health card (7 per-sensor clocks + cabin
temp/barometer), vehicles-list Status cell (row-level freshest-reading time).
Same primitive, same thresholds — P1/P5/P6 verdicts carry over; the
tooltip-only `cue=false` variant is itself a Smart-Defaults call (one stale
GPS fix should not paint three dots in one card).

## Top 3 actions (highest impact first)
1. DONE same-day (see addendum): Location + Health cards, vehicles-list rows. `Impact: high · Effort: S`
2. Consider i18n for the tooltip wording — co-dev just added `common.updated_*` keys; `formatRelative` is currently English-only across the app, so migrate both together rather than piecemeal. `Impact: low · Effort: M`
3. Miniapp parity later — drivers benefit most from "GPS updated 3d ago" on their own truck. `Impact: med · Effort: M`

## NEEDS-CONTEXT items
- none
