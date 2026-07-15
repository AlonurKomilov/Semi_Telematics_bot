# UX Psychology Audit Report
- Framework version: 1
- Scope mode: C targeted (code) — the composer status strip's idle state: after a run it now persists as "Waiting for your reply" and CARRIES the follow-up suggestion chips, collapsed behind a count badge (💡 6 ⌄). Replaces the free-floating 3-row chip block.
- Date: 2026-07-14 | Auditor session: copilot-status-strip
- Surfaces audited: 1 | Not yet audited: none in scope

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Idle status strip | Post-answer decision | APPLIED | APPLIED | APPLIED | N/A | OPPORTUNITY | APPLIED |

## Findings
### Idle status strip (Waiting for your reply · suggestions)
- **[P1 Smart Defaults — APPLIED]** Collapsed-by-default is the compact-first choice; the count badge (💡 6) advertises exactly how much is behind the toggle, so nothing is silently hidden. Chips re-collapse per answer — every turn starts tidy.
- **[P2 Goal Gradient — APPLIED]** The Done · Xs stamp swaps into "Waiting for your reply" after 4 s — a legible run-lifecycle (working → done → your turn) in one stable location.
- **[P3 Reciprocity — APPLIED]** The strip offers value (curated follow-ups) at the exact moment it asks for the user's next move, rather than a bare "waiting" nag. This was the fix for the earlier objection: a status banner must carry something useful to earn permanent screen space.
- **[P4 IKEA — N/A]** Read-only affordance.
- **[P5 Loss Aversion — OPPORTUNITY]** Suggestions vanish wholesale when the user types their own question and sends (send() clears them) — fine — but they also disappear if history fails to restore them on another device (browser-local by policy). Consider a one-line note in the strip on restored threads with no local chips ("Suggestions live on the device that ran the chat"). Low stakes. `Impact: low · Effort: S`
- **[P6 Contrast Effect — APPLIED]** Three visually distinct states (primary tint = working, ok tone = done, muted = your turn) make the assistant's state readable at a glance without reading text.

## Ethics gate
Honest: the count badge never understates hidden content; the strip disappears entirely when there is nothing useful to show (no suggestion inventory = no banner). No fake urgency, no nagging.

## Top 3 actions
1. Shipped — chips consolidated into the strip; ~3 rows reclaimed in the 420px panel. `done`
2. Future: "Waiting for your approval" strip state when a write-action proposal is pending (needs card→Chat state lift). `Impact: med · Effort: M`
3. P5 note above. `Impact: low · Effort: S`

## NEEDS-CONTEXT items
- none.
