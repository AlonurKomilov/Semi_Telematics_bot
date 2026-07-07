# UX Psychology Audit Report
- Framework version: 1
- Scope mode: A session — carrier self-fill intake flow (public form, manager invite panel, review signals, invite + submitted emails) and the maintenance overlay touched this session
- Date: 2026-07-06 | Auditor session: carrier-intake build
- Surfaces audited: 6 | Not yet audited: none (session scope complete)

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Public carrier intake form (`PublicCarrierIntake.tsx`) | first-run | APPLIED | OPPORTUNITY | APPLIED | APPLIED | OPPORTUNITY | N/A |
| Invite panel (`CarrierProfile.tsx` InvitePanel) | decision point | APPLIED | N/A | N/A | N/A | APPLIED | N/A |
| Review signals (profile banner + list chip) | recurring use | APPLIED | N/A | N/A | N/A | N/A | N/A |
| Carrier invite email (`send_carrier_intake_email`) | first-run | N/A | OPPORTUNITY | APPLIED | N/A | APPLIED | N/A |
| Submitted-notify email (`send_carrier_intake_submitted_email`) | recurring use | N/A | N/A | N/A | N/A | N/A | N/A |
| Maintenance overlay (`MaintenanceOverlay.tsx`) | exit/error point | N/A | N/A | N/A | N/A | OPPORTUNITY | N/A |

## Findings

### Public carrier intake form
- **[P1 Smart Defaults — APPLIED]** The form opens prefilled with everything the agency already stored (server prefill merged over the ~70-field template; a newer local draft wins), so the carrier reviews rather than authors from blank.
- **[P2 Goal Gradient — OPPORTUNITY]** One long page, ~70 fields, zero progress feedback — the carrier can't see remaining effort shrink. Add a live fill counter near the submit button ("23 of 70 fields filled") computed client-side from non-blank values; with prefill it often starts above zero, which is exactly the goal-gradient effect. `Impact: high · Effort: M`
- **[P3 Reciprocity — APPLIED]** The header explains the benefit to the carrier ("so recruiters present your company accurately to driver candidates") before asking for their time; every field is skippable.
- **[P4 IKEA Effect — APPLIED]** "Add field" lets the carrier extend each section with their own rows; answers persist and stay revisable while the link is active — the sheet reads as *theirs*.
- **[P5 Loss Aversion — OPPORTUNITY]** Autosave exists but is invisible, and the link expiry (`expires_at` is already returned by the GET) is never shown. Display "Answers save automatically on this device" under the header and "Link active until ⟨date⟩" near the submit button — honest deadline + reassurance against perceived loss of work. `Impact: med · Effort: S`
- **[P6 Contrast Effect — N/A]** No choice set or tiered options on this surface.

### Invite panel (manager)
- **[P1 Smart Defaults — APPLIED]** Expiry pre-selected at 30 days, email optional, the created link is auto-copied — zero-config happy path.
- **[P2 Goal Gradient — N/A]** Single-action flow, no multi-step progress to show.
- **[P3 Reciprocity — N/A]** Internal tool; no give/ask gate exists.
- **[P4 IKEA Effect — N/A]** The profile editor (pre-existing) is the customization surface; the panel itself is a one-shot control.
- **[P5 Loss Aversion — APPLIED]** Real consequences are stated before both destructive actions: "Creating a new link replaces the current one — the previously sent link stops working" and the revoke confirm names what the carrier loses. Honest, no manufactured urgency.
- **[P6 Contrast Effect — N/A]** 7/30/90 days is a neutral parameter, not an anchored offer set.

### Review signals (banner + "Carrier updated" chip)
- **[P1 Smart Defaults — APPLIED]** The review flag raises itself on submission and clears as a side effect of the manager's natural save — no manual state to manage.
- **[P2 Goal Gradient — N/A]** Not a progress flow.
- **[P3 Reciprocity — N/A]** Internal signal only.
- **[P4 IKEA Effect — N/A]** No user-shaped artifact here.
- **[P5 Loss Aversion — N/A]** Informational badge; nothing at stake to frame as loss.
- **[P6 Contrast Effect — N/A]** No choice set.

### Carrier invite email
- **[P1 Smart Defaults — N/A]** No inputs; single clear CTA.
- **[P2 Goal Gradient — OPPORTUNITY]** The email honestly sets effort ("about 10–15 minutes") but never says the sheet may already be partly filled. When the profile's stored content is non-empty, add one line: "Some fields are already filled from our records — you only need to review and complete the rest." (Omit it for empty profiles — must stay honest.) `Impact: med · Effort: S`
- **[P3 Reciprocity — APPLIED]** Leads with the carrier's benefit (accurate representation to candidates) before the ask.
- **[P4 IKEA Effect — N/A]** Email touchpoint, nothing to customize.
- **[P5 Loss Aversion — APPLIED]** Honest expiry ("private to your company and expires in N days") — a real deadline, not fake scarcity.
- **[P6 Contrast Effect — N/A]** No options presented.

### Submitted-notify email (managers)
- All six principles **N/A** — a one-line internal notification with a single deep-link CTA to the exact profile; no defaults, progress, ask, customization, loss, or choice set to evaluate. Its job (route the manager to review fast) is done by the direct link.

### Maintenance overlay
- **[P1–P4, P6 — N/A]** No inputs, progress, ask, customization, or choices — a status card.
- **[P5 Loss Aversion — OPPORTUNITY]** On recovery it reloads the page without warning; a user mid-form when the restart hit loses typed input with no expectation set. Minimum honest fix: add "This page will refresh automatically once we're back" to the card copy so the reload isn't a surprise. `Impact: low · Effort: S`

## Top 3 actions (highest impact first)
1. **Fill-progress counter on the public intake form** — "N of M fields filled" near submit; starts above zero thanks to prefill. `Impact: high · Effort: M`
2. **Show autosave reassurance + link expiry on the public form** — the data (`expires_at`) is already in the API response, just unrendered. `Impact: med · Effort: S`
3. **Conditional "already pre-filled" line in the invite email** — lowers perceived effort honestly when stored content exists. `Impact: med · Effort: S`

## NEEDS-CONTEXT items
- None — session scope was self-contained.

## Ethics gate
All APPLIED patterns and proposed OPPORTUNITY fixes passed the explain-to-the-user's-face test: expiry dates are real, effort claims are honest (keep "10–15 minutes" truthful if the template grows), and no fake scarcity/progress is proposed. Zero DARK-PATTERN-RISK findings.

## Outcome (same-day)
Top-3 actions implemented 2026-07-06: fill-progress bar + counter and the
expiry date line on the public form (`PublicCarrierIntake.tsx`), and the
conditional "already filled from our records" line in the invite email
(`prefilled` flag computed from the carrier-visible sheet only). The
maintenance-overlay copy item (low impact) remains open.
