# UX Psychology Audit Report
- Framework version: 1
- Scope mode: C targeted (code) — the AI write-action approve/reject card (`interfaces/dashboard/src/features/ai/artifacts/ActionProposalCard.tsx`), the copilot "hands" UI shown inline in the chat stream when the assistant proposes a write (create maintenance task, acknowledge alerts).
- Date: 2026-07-13 | Auditor session: copilot-phase4-write-actions
- Surfaces audited: 1 | Not yet audited: none (single-surface scope)

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Action-proposal card | Decision point | APPLIED | N/A | APPLIED | OPPORTUNITY | OPPORTUNITY | APPLIED |

## Findings
### Action-proposal card (approve / reject a proposed write)
- **[P1 Smart Defaults — APPLIED]** The user never faces a blank form: the AI pre-composes the whole payload (vehicle, task type, due date / alert set) and the card renders it as a ready plain-language `summary`. The safe non-default matters here too — neither button is auto-focused / Enter-bound, so a keystroke can't auto-approve a write. Correct for an always-confirm design.
- **[P2 Goal Gradient — N/A]** Single-step approve, not a multi-step flow — no progress meter applies. (The pending→working→done phase copy is status feedback, not goal progress.)
- **[P3 Reciprocity — APPLIED]** The assistant does the work (resolves the vehicle, drafts the task) *before* asking for approval — the user receives a finished, actionable proposal rather than being sent to fill a form. Value precedes the ask.
- **[P4 IKEA Effect — OPPORTUNITY]** The card is approve-as-is or reject; the user can't shape the proposal (e.g. nudge the due date, drop one alert from the batch) before committing. A "not quite — adjust" affordance, or inline-editable key fields, would raise ownership and cut reject→re-ask round-trips. Defer to a richer per-feature card in `features/<x>/ai_actions/`; the generic card staying read-only is a fine 4.0 default. `Impact: med · Effort: L`
- **[P5 Loss Aversion — OPPORTUNITY]** Reversibility is signalled on the *decline* path ("Declined — nothing changed.") but **not at the approve moment**. A low-risk write shows no hint of whether it can be undone. Add a one-line consequence/reversibility note under the summary keyed off the action, e.g. "Creates a task — you can delete it anytime" (reversible) vs. a firmer "Acknowledging clears these alerts" (harder to undo). This makes the commit legible without a modal. `Impact: med · Effort: S`
- **[P6 Contrast Effect — APPLIED (ethics-noted)]** Approve is `bg-primary` (dominant), Reject is `text-muted-foreground` (recessive) — approve-forward emphasis. Honest here because the user *asked* the assistant to act, always-confirm is enforced, Reject stays clearly labelled, and high-risk writes are globally gated off (`HIGH_RISK_WRITES_ENABLED=False`). **Forward-looking guard:** if high-risk writes are ever enabled, invert the emphasis for `risk!=='low'` (make the user deliberately reach for Approve, mute it relative to Reject) so button weight can't nudge someone into an irreversible action. `Impact: med (future) · Effort: S`

**Trust/legibility note (not a principle, worth recording):** the on-mount `aiGetActionStatus` reconcile is a strong trust move — a proposal approved on another device or already expired shows Done/expired instead of a live button that would lie. The failed/expired copy both give a recovery path ("Ask the assistant to try again" / "Ask again to get a fresh one"). Keep this.

## Top 3 actions (highest impact first)
1. **P5** — Add a reversibility/consequence line at the approve moment, per-action (reversible vs. clearing state). Small, high-trust. `S`
2. **P6 (future guard)** — When high-risk writes are enabled, invert button emphasis for non-low risk so visual weight can't push an irreversible approve. `S`
3. **P4** — Let the user adjust the proposal (or a "not quite — adjust" re-prompt) before approving; ship as a richer per-feature card, not in the generic one. `L`

## NEEDS-CONTEXT items
- none for this surface.
