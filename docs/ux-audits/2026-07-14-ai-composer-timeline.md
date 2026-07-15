# UX Psychology Audit Report
- Framework version: 1
- Scope mode: C targeted (code) — the AI chat composer + live process timeline after 4 refinements: (1) live per-step durations + a tinted "Running · Xs" active row, (2) deep-link chip→button, (3) integrated composer, (4) textarea stays editable while a reply streams.
- Date: 2026-07-14 | Auditor session: copilot-composer-polish
- Surfaces audited: 2 (composer, live timeline) | Not yet audited: none in scope

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Composer | Recurring / compose | APPLIED | N/A | N/A | APPLIED | APPLIED | APPLIED |
| Live timeline | In-progress wait | N/A | APPLIED | N/A | N/A | APPLIED | APPLIED |

## Findings
### Composer (the input field + send/stop)
- **[P1 Smart Defaults — APPLIED]** Placeholder does double duty: names the subject ("Ask about <X>…") and advertises the slash-command affordance ("or type / for commands") — the user is never staring at a blank, purpose-less box.
- **[P2 Goal Gradient — N/A]** Single input, no multi-step flow to show progress on.
- **[P3 Reciprocity — N/A]** Not a give-before-ask surface.
- **[P4 IKEA Effect — APPLIED (improved by #4)]** The field now stays editable while a reply streams, so a half-typed next question is preserved and can keep being shaped instead of being locked and abandoned. The user's in-progress words stay *theirs*.
- **[P5 Loss Aversion — APPLIED (improved by #4)]** Previously the composer was disabled during generation — a user who started typing lost the field's focus/usability and their momentum. Now they keep drafting; Enter is still safely blocked (submit() guards on `loading`) so nothing sends mid-stream. Stop aborts without losing the thread.
- **[P6 Contrast Effect — APPLIED]** One primary action at a time: a filled-primary circular Send, which morphs into a muted circular Stop while running. No competing buttons; the right action is always the obvious one.

### Live process timeline (what the assistant is doing, in real time)
- **[P1 Smart Defaults — N/A]** Read-only status surface.
- **[P2 Goal Gradient — APPLIED (the core win of #1)]** Every completed step now shows its duration and the active step shows a tinted card with a live "Running · Xs" counter — a continuously advancing, legible progress signal. A long Reasoning-tier turn reads as *work happening*, not a hang; each closed step with a time is a small completed sub-goal.
- **[P3 Reciprocity — N/A]**
- **[P4 IKEA Effect — N/A]**
- **[P5 Loss Aversion — APPLIED]** The visible ticking + Stop control means the user can bail a turn that's clearly going wrong early, rather than feeling committed to an opaque wait — reduces the sunk-cost/"is it stuck?" anxiety.
- **[P6 Contrast Effect — APPLIED]** The active step is visually distinct (tint + beacon + timer) against the muted, checked, completed steps — the eye lands on "what's happening now" without hunting.

## Ethics gate
All six are honest transparency/friction-reduction, not manipulation — durations are real (client-measured live, server-authoritative when finished), and "editable while running" removes a limitation rather than adding pressure. No dark patterns.

## Top 3 actions (highest impact first)
1. Already shipped — the per-step durations + active-timer (#1) and editable-while-running (#4) are the two highest-value UX moves here. `done`
2. (Optional, future) Show a subtle "your message will send when this finishes" affordance if the user presses Enter mid-stream, so the blocked-Enter isn't silently ignored. `Impact: low · Effort: S`
3. (Optional, future) Persist the active-step timer's final value into the finished timeline visually as a "took Xs" so live and finished read identically. `Impact: low · Effort: S`

## NEEDS-CONTEXT items
- none.
