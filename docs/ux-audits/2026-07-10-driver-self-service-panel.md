# UX Psychology Audit Report
- Framework version: 1
- Scope mode: C targeted — the new "Driver — self-service" panel + Driver-column removal on the Permissions page (`interfaces/dashboard/src/features/permissions/Permissions.tsx`)
- Date: 2026-07-10 | Auditor session: driver-self-service-panel
- Surfaces audited: 2 | Not yet audited: none

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Driver — self-service panel | Decision point (admin configures driver access) | APPLIED | N/A | N/A | APPLIED | OPPORTUNITY | N/A |
| Driver removed from staff matrix | Decision point (admin scans role grid) | APPLIED | N/A | N/A | N/A | APPLIED | APPLIED |

## Findings
### Driver — self-service panel
- **[P1 Smart Defaults — APPLIED]** Toggles render from the `driver` role's stored/seeded perms (`data.current['driver']`), never a blank slate — an admin opening the panel sees the sensible current config, not an empty form. `Impact: — · Effort: —`
- **[P2 Goal Gradient — N/A]** Single-surface config panel, not a multi-step flow — no progress to show.
- **[P3 Reciprocity — N/A]** Internal admin control surface; no user gate/give-before-ask moment.
- **[P4 IKEA Effect — APPLIED]** The admin actively shapes what each driver sees in the mini app; the config is visibly "their" fleet's driver setup, preserved across sessions and reflected live in the app — investment the model rewards. `Impact: — · Effort: —`
- **[P5 Loss Aversion — OPPORTUNITY]** Turning **Vehicle & Assistant** off silently strips the driver's **Alerts inbox + AI tab** too (both derived from `can_vehicle_*` in `derive_service_perms`). The panel intro states the positive ("come automatically with the Vehicle grant") but the moment of *loss* is invisible — the confirm dialog lists only "Vehicles → No access", not the cascade. Fix: when the Vehicle toggle goes off, show an inline warning row ("Also removes the Alerts inbox + AI assistant for all drivers") and/or a line in the confirm dialog. Makes the consequence honest at the point of action. `Impact: med · Effort: S`
- **[P6 Contrast Effect — N/A]** No competing options/tiers to anchor — a flat capability list.

### Driver removed from staff matrix
- **[P1 Smart Defaults — APPLIED]** The matrix no longer presents nonsensical Driver cells (Permissions, Storage, Manage-*) an admin would have to reason about and leave blank — removing the impossible choice IS the smart default. `Impact: — · Effort: —`
- **[P2 Goal Gradient / P3 Reciprocity — N/A]** Not a flow / not a user gate.
- **[P4 IKEA Effect — N/A]** Read-side layout change, no configuration authored here.
- **[P5 Loss Aversion — APPLIED]** Narrows cognitive load — the admin can't accidentally mis-grant a management power to a driver, so there's no silent authorization-leak to lose sleep over. `Impact: — · Effort: —`
- **[P6 Contrast Effect — APPLIED]** Pulling the odd-one-out (a role with a totally different grant shape) out of the grid makes the remaining staff columns genuinely comparable to each other — the grid now compares like-with-like. `Impact: — · Effort: —`

## Top 3 actions (highest impact first)
1. Surface the derived-loss cascade when the driver **Vehicle & Assistant** toggle is turned off — inline warning + a confirm-dialog line noting Alerts inbox + AI assistant also drop. `Impact: med · Effort: S`
2. (Optional) A subtle "reset to recommended" affordance on the panel if an admin over-tweaks — reinforces Smart Defaults as an expert baseline. `Impact: low · Effort: S`
3. None further — the surface is honest and low-risk; no dark patterns found.

## NEEDS-CONTEXT items
- none
