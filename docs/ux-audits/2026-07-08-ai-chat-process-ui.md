# UX Psychology Audit Report
- Framework version: 1
- Scope mode: C targeted — AI Assistant chat surface (`interfaces/dashboard/src/features/ai/Chat.tsx`, `thoughtStore.ts`): live progress bubble, finished-answer "N steps" timeline, History panel, New chat, tier picker (optimistic switch), per-answer tier label, empty state. Code audit; no browser tools in session.
- Date: 2026-07-08 | Auditor session: ai-chat-process-ui
- Surfaces audited: 7 | Not yet audited: none in scope (rest of dashboard covered by prior reports in this folder)

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Live progress bubble | recurring (waiting) | N/A | APPLIED | APPLIED | N/A | OPPORTUNITY | N/A |
| Finished "N steps" timeline | recurring | N/A | APPLIED | APPLIED | N/A | OPPORTUNITY | N/A |
| History panel | recurring / decision | APPLIED | N/A | N/A | OPPORTUNITY | APPLIED | N/A |
| New chat button | recurring | APPLIED | N/A | N/A | N/A | OPPORTUNITY | N/A |
| Tier picker | decision point | APPLIED | N/A | N/A | N/A | APPLIED | APPLIED |
| Per-answer tier label | recurring | N/A | N/A | APPLIED | N/A | N/A | N/A |
| Empty state (briefing + chips) | first-run | APPLIED | N/A | APPLIED | N/A | N/A | N/A |

## Findings

### Live progress bubble
- **[P2 Goal Gradient — APPLIED]** Progress never reads as stalled: named steps accumulate with checkmarks, the header counts them live ("3 steps"), and the elapsed-seconds counter ticks — a long Reasoning turn reads as advancing work, not a hang. `[code]`
- **[P3 Reciprocity — APPLIED]** The user watches real work happen (tool labels like "Reviewing maintenance") before being asked anything — transparency as given value. `[code]`
- **[P5 Loss Aversion — OPPORTUNITY]** Clicking **Stop** or **New chat** mid-run aborts silently — after 30+ seconds of a Reasoning turn, that's real invested wait discarded with no guard. Add a lightweight confirm only when `elapsedSec > 10` ("Discard the answer in progress?") on New chat; keep Stop instant (it's the explicit purpose of that button). `Impact: med · Effort: S` `[code]`
- **[P1 Smart Defaults — N/A]** No user decision on this surface. **[P4 IKEA — N/A]** Nothing user-shaped. **[P6 Contrast — N/A]** No choice set.

### Finished "N steps" timeline
- **[P2 Goal Gradient — APPLIED]** "7 steps" on the toggle shows completed effort at a glance; expanding reveals the checked sequence — effort made visible after the fact.
- **[P3 Reciprocity — APPLIED]** Showing the chain-of-thought and which data was fetched builds trust before the user is ever asked to trust a recommendation.
- **[P5 Loss Aversion — OPPORTUNITY]** Thought logs are now device-local (localStorage) by policy. A user who reopens a thread on another device sees answers without their step logs and may read that as data loss. One-time expectation-setter: the first time a timeline renders, show a dismissible `text-3xs` note "Step logs are stored on this device only". `Impact: low · Effort: S` `[code]`
- **[P1 — N/A]** No decision. **[P4 — N/A]** Log is machine-generated, not user-built. **[P6 — N/A]** No choice set.

### History panel
- **[P1 Smart Defaults — APPLIED]** Page load opens the most recent conversation automatically — the user lands in context, never on a blank decision.
- **[P4 IKEA Effect — OPPORTUNITY]** Threads are the user's own work (titles derive from their first question) but rows show only title + date. `message_count` is already returned by the API and unused — render it ("12 messages") so accumulated investment is visible, strengthening attachment to the workspace. `Impact: low · Effort: S` `[code]`
- **[P5 Loss Aversion — APPLIED]** Per-chat delete is two-tap confirm (trash arms for 3s, second tap deletes) — a real loss guard; local thought logs are deleted consistently with the thread, honest to the user's mental model of "delete chat".
- **[P2 — N/A]** No goal flow. **[P3 — N/A]** No ask. **[P6 — N/A]** No comparative choice.

### New chat button
- **[P1 Smart Defaults — APPLIED]** Disabled when already on a fresh empty chat — no dead-end action offered; thread is created lazily so abandoning it leaves no clutter.
- **[P5 Loss Aversion — OPPORTUNITY]** Same mid-run abort as the live bubble (consolidated there).
- **[P2/P3/P4/P6 — N/A]** Single-action control.

### Tier picker
- **[P1 Smart Defaults — APPLIED]** Ships with a safe, cheap default (Fast); Auto exists for users who don't want the decision at all. Default favors the user's latency, not the vendor's upsell — passes the face-test.
- **[P5 Loss Aversion — APPLIED]** Optimistic switch reverts visibly to the previous tier with an error banner on failure — no silent state loss.
- **[P6 Contrast Effect — APPLIED]** Ordered Fast → Thinking → Reasoning with honest one-line descriptions and real model counts; the middle option reads as the balanced choice without any decoy. No pricing asymmetry to manipulate. Ethics: clean.
- **[P2 — N/A]** No progression. **[P3 — N/A]** No ask. **[P4 — N/A]** Preference, not creation.

### Per-answer tier label
- **[P3 Reciprocity — APPLIED]** Honest attribution (icon + tier frozen at receipt, never relabeled by later picker changes) — transparency that costs nothing and builds trust in what produced each answer.
- **[P1/P2/P4/P5/P6 — N/A]** Read-only badge.

### Empty state (briefing chip + suggested questions)
- **[P1 Smart Defaults — APPLIED]** First-run shows a primary pre-configured action (persona-correct briefing: "Hiring Briefing" for recruiters etc.) plus role-keyed suggested questions — never a blank prompt box as the only path.
- **[P3 Reciprocity — APPLIED]** One click yields a full operations briefing before the user has typed anything.
- **[P2 — N/A]** No multi-step flow. **[P4 — N/A]** Nothing to build here. **[P5 — N/A]** Nothing at stake yet. **[P6 — N/A]** No choice set.

## Ethics gate
All APPLIED patterns pass the face-test: the elapsed timer is real time, step counts are real steps, tier labels report the tier that actually answered (verified in this session's routing fixes), delete confirms are honest guards. No DARK-PATTERN-RISK found in scope.

## Top 3 actions (highest impact first)
1. **Mid-run abort guard** — confirm on New chat when `elapsedSec > 10` so a long Reasoning run isn't discarded by a stray click. `Impact: med · Effort: S`
2. **Show `message_count` on History rows** — data already in the API response; one line of JSX; makes the user's accumulated work visible. `Impact: low · Effort: S`
3. **One-time "stored on this device" note on the steps timeline** — sets the localStorage expectation honestly before a user meets it as a surprise on another device. `Impact: low · Effort: S`

## NEEDS-CONTEXT items
- None — all in-scope surfaces were readable in full.
