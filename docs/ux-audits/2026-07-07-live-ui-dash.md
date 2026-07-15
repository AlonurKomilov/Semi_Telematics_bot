# UX Psychology Audit Report
- Framework version: 1
- Scope mode: D live-UI — dash.4truck.us (Overview /, Billing & Plan /billing, Settings /settings)
- Date: 2026-07-07 | Auditor session: chrome-extension (findings verified/annotated + P6 resolved from code by session changeset-remainder-2)
- Surfaces audited: 3 | Not yet audited: Live Map, Vehicles, Alerts, Reports, KPI, Integrations, Storage, Permissions, Team Management, Knowledge Base, AI Assistant (live-UI pass; several have code-level coverage in the 2026-07-06 reports)

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Overview (/) | Landing / daily glance | APPLIED | N/A | APPLIED | OPPORTUNITY | N/A | N/A |
| Billing & Plan (/billing) | Reviewing cost / plan choice | APPLIED | N/A | APPLIED | N/A | OPPORTUNITY | APPLIED |
| Settings (/settings) | Configuring account | APPLIED | OPPORTUNITY | N/A | OPPORTUNITY | N/A | N/A |

## Findings

### Overview (/)
- **[P1 Smart Defaults — APPLIED]** Dashboard loads fully populated with real fleet data (184 vehicles, status split, snapshot bullets) — value is visible before any configuration. [ui]
- **[P2 Goal Gradient — N/A]** No multi-step flow on this surface; nothing to progress through. [ui]
- **[P3 Reciprocity — APPLIED]** User receives the entire operational picture (alerts, fuel warnings, fault counts, safe-zone breaches) with zero gate or ask up front. [ui]
- **[P4 IKEA Effect — OPPORTUNITY]** Layout is fixed; user can't reorder, pin, or hide the KPI cards / quick-actions to make the dashboard "theirs." Add drag-to-reorder or a simple "pin card" toggle persisted per user. [ui] `Impact: med · Effort: M` *(Reviewer note: the server-synced `useUserPreference` hook shipped for DataTable layouts can hold the card-order preference, trimming effort toward S–M.)*
- **[P5 Loss Aversion — N/A]** No exit/cancel/expiry moment on this surface. [ui]
- **[P6 Contrast Effect — N/A]** No comparative choice set presented here. [ui]

### Billing & Plan (/billing)
- **[P1 Smart Defaults — APPLIED]** Billing view is pre-computed and honest: current plan, extra-truck math, and est. total shown; no pre-checked paid add-ons or hidden opt-ins. [ui]
- **[P2 Goal Gradient — N/A]** No stepped setup flow on this surface. [ui]
- **[P3 Reciprocity — APPLIED]** Full AI usage breakdown, inactive-vehicle exclusions ("not billed"), and pricing explainer are given freely before any upsell action. [ui]
- **[P4 IKEA Effect — N/A]** Billing is not a surface the user is meant to personalize. [ui]
- **[P5 Loss Aversion — OPPORTUNITY]** The "Switch to Starter" (downgrade) button carries no honest loss preview — a first-time user can't see what Pro-only features they'd lose (Unlimited Samsara orgs, advanced AI reports & vision, priority support, custom KB). Add an inline confirm that lists the specific capabilities forfeited on downgrade. This is honest loss-framing, not confirm-shaming. [ui] `Impact: high · Effort: S`
- **[P6 Contrast Effect — APPLIED]** *(Resolved 2026-07-07 from code — originally NEEDS-CONTEXT: "is the same layout honest for a prospect on Starter?")* The highlight is state-driven, not hard-coded: `current={summary?.tier === 'starter'|'pro'}` (Billing.tsx:706, :720) drives the border/ring/"Current Plan" badge and greys that plan's button (:386-392, :407-412) — a Starter customer sees the mirror image (Starter highlighted, Pro's "Switch to Pro" active). Card order anchors the cheaper plan first (Starter :697, Pro :710) — the opposite of anchor-high upselling — and both cards share identical truck math (10 included, $2.99/extra), so the only contrast is the honest feature/price difference. A brand-new signup is auto-trialed ON Pro (`start_trial(tier="pro", days=14)`, adapters/storage/billing.py:104-114), so Pro showing "Current Plan" for a prospect is factually true, disclosed by a warn "trialing" status chip (Billing.tsx:232) and "⚠️ Trial ends ⟨date⟩" with the real end date (:308-309); trial→paid conversion exists via the "Manage payment" Stripe portal (:624-633). [code] Minor polish (not filed as its own status): the Pro card badge could read "Your trial plan" during trialing, and a "keep Pro — add payment" nudge near the trial-end warning would smooth conversion. `Impact: low · Effort: S`

### Settings (/settings)
- **[P1 Smart Defaults — APPLIED]** Account timezone is pre-selected (Eastern Time) with a genuinely helpful DST tip; Telegram token field shows a format placeholder. Sensible, honest defaults with value before edits. [ui]
- **[P2 Goal Gradient — OPPORTUNITY]** "Configuration — No settings configured yet" states an empty end-state with no sense of progress or what "done" looks like. Add a small setup checklist (timezone ✓ set, Telegram ○, companies ○) that starts above 0% using already-completed items. [ui] `Impact: med · Effort: S`
- **[P3 Reciprocity — N/A]** No gate or ask introduced on this surface. [ui]
- **[P4 IKEA Effect — OPPORTUNITY]** Telegram-bot connection is meaningful personalization, but it demands external work (create a bot via BotFather, paste token) before any payoff — heavy setup with no early value moment. [ui] `Impact: low · Effort: M` *(Reviewer note — proposal amended: the original suggestion of a "test notification to a shared 4truck demo bot" crosses a tenant-privacy boundary — per-account bots are a deliberate isolation decision (multi-bot registry), and routing one account's notifications through a shared bot fails the explain-to-their-face test. Ethical alternative with the same intent: a guided connect wizard — deep-link to BotFather, token field with live format validation, then an immediate "Send test message" button so payoff arrives seconds after connecting. The BotFather step itself is a Telegram platform constraint, not a switching-cost tactic.)*
- **[P5 Loss Aversion — N/A]** No cancel/expiry moment here. [ui]
- **[P6 Contrast Effect — N/A]** No comparative choice set. [ui]

## Top 3 actions (highest impact first)
1. **[Billing, P5]** Add an honest downgrade-loss preview to "Switch to Starter" listing the exact Pro-only features forfeited — protects users from silent capability loss and is transparently explainable. `Impact: high · Effort: S`
2. **[Settings, P2 + P4]** Replace "No settings configured yet" with a setup checklist pre-seeded from completed items (goal gradient starts >0%) and add the guided Telegram connect wizard + instant "Send test message" (amended from the shared-demo-bot idea — see P4 note). `Impact: med · Effort: S–M`
3. **[Overview, P4]** Let users pin/reorder/hide dashboard cards per account, persisting the layout via the existing `useUserPreference` store — turns a generic dashboard into "their" workspace. `Impact: med · Effort: M`

## NEEDS-CONTEXT items
- None remaining. The Billing P6 prospect-view question was resolved from code on 2026-07-07 (see finding). Original auditor note preserved: the audited account showed "Test mode — no real charges"; no billing actions were taken during the live session.
