# UX Psychology Audit Report
- Framework version: 1
- Session scope: Uncommitted changeset — DataTable v2 (server-synced column prefs, row grouping, pagination, export/copy, density) rolled across ~25 table pages; AI Chat threads/History panel; Alerts by-vehicle bulk-ack; Billing/Settings table migrations
- Date: 2026-07-06 | Auditor session: datatable-v2-ai-threads
- Surfaces audited: 8

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| DataTable toolbar (search/filter/sort/export/copy/density) | recurring use | APPLIED | N/A | N/A | APPLIED | N/A | N/A |
| Column customization + synced prefs + Reset | recurring / decision | APPLIED | N/A | N/A | APPLIED | OPPORTUNITY | N/A |
| Pagination footer | recurring use | APPLIED | APPLIED | N/A | APPLIED | N/A | N/A |
| Empty / filtered-to-zero table state | edge / exit point | OPPORTUNITY | N/A | N/A | N/A | OPPORTUNITY | N/A |
| Alerts by-vehicle view + bulk ack | recurring / decision | APPLIED | N/A | N/A | APPLIED | OPPORTUNITY | APPLIED |
| AI Chat History panel (threads, export, delete) | recurring / exit | APPLIED | N/A | N/A | APPLIED | OPPORTUNITY | N/A |
| AI Chat empty state + persona briefing | first-run | APPLIED | N/A | APPLIED | N/A | N/A | N/A |
| Billing usage & invoice tables | decision point | APPLIED | N/A | N/A | N/A | APPLIED | APPLIED |

## Findings

### DataTable toolbar
- **[P1 Smart Defaults — APPLIED]** Toolbar/pagination default on; consumers opt out (`enableToolbar={false}`) only for pure display lists. Density defaults to the balanced layout and the old device-local density choice migrates into the synced preference instead of resetting (`readLegacyDensity`).
- **[P2 Goal Gradient — N/A]** No multi-step flow on this surface.
- **[P3 Reciprocity — N/A]** Nothing is asked of the user here.
- **[P4 IKEA Effect — APPLIED]** Filter button carries a live count badge — the user's own narrowing is visible and owned, with one place to manage/clear it.
- **[P5 Loss Aversion — N/A]** Export/copy are non-destructive.
- **[P6 Contrast Effect — N/A]** No choice-set framing on this surface.

### Column customization + synced preferences + Reset
- **[P1 Smart Defaults — APPLIED]** Every table works untouched with declaration defaults; `defaultRowGroup` opens Alerts pre-grouped; reset returns to the *configured* default (not bare ungrouped). Good defaults-plus-tweak balance.
- **[P2 Goal Gradient — N/A]** Customization is optional, not a staged flow.
- **[P3 Reciprocity — N/A]** No gate or ask.
- **[P4 IKEA Effect — APPLIED]** Flagship application: visibility, order, pinning, widths, grouping, page size, and density persist per-user server-side (`useUserPreference`), so the operator's built-up layout follows them across devices — invested effort is preserved, not device-trapped. Legacy localStorage density is migrated rather than discarded.
- **[P5 Loss Aversion — OPPORTUNITY]** "Reset to defaults" (`resetAll`, DataTable.tsx:1332) wipes filters, sort, search, visibility, order, pinning, widths, and grouping in one click with no confirm or undo — and because prefs are server-synced, the operator's layout is destroyed on *all* devices. Concrete change: arm-style two-step confirm on the Reset button (same 3s pattern as chat delete), or an undo toast that restores the pre-reset preference snapshot. `Impact: med · Effort: S`
- **[P6 Contrast Effect — N/A]** No comparative choice set.

### Pagination footer
- **[P1 Smart Defaults — APPLIED]** Default 25 rows/page; chosen page size persists per table per user; page index deliberately in-memory.
- **[P2 Goal Gradient — APPLIED]** "1–25 of N" range keeps position-in-dataset visible; prev/next disable at bounds so remaining extent is legible.
- **[P3 Reciprocity — N/A]** No ask. **[P4 IKEA — APPLIED]** page-size choice is remembered as theirs. **[P5 Loss — N/A]** nothing destructible. **[P6 Contrast — N/A]** page-size options are a plain scale.

### Empty / filtered-to-zero table state
- **[P1 Smart Defaults — OPPORTUNITY]** The body renders a bare "No data" (DataTable.tsx:1768) regardless of *why* it's empty. The default for a guidance moment should be a helpful state, not a dead end. Concrete change: when `columnFilters.length > 0 || globalFilter`, render "No rows match your filters" plus an inline "Clear filters" button (clear `columnFilters` + `globalFilter` only — not the layout reset). `Impact: med · Effort: S`
- **[P5 Loss Aversion — OPPORTUNITY]** Same root cause, framed as loss: an operator with a forgotten filter reads "No data" as *their records are gone* — the footer's small "2 filters active" hint is easy to miss and only shows when pagination is enabled. The fix above resolves both; count this as one change. `Impact: med · Effort: S`
- **[P2/P3/P4/P6 — N/A]** No progression, ask, authorship, or comparison in an empty body.

### Alerts by-vehicle view + bulk ack
- **[P1 Smart Defaults — APPLIED]** Opens pre-grouped by vehicle via `defaultRowGroup`; the operator's own grouping choice persists and wins thereafter. Select-all targets only ack-able (un-acknowledged) rows.
- **[P2 Goal Gradient — N/A]** No staged flow. **[P3 Reciprocity — N/A]** No ask.
- **[P4 IKEA Effect — APPLIED]** List vs by-vehicle modes keep separate layout/grouping prefs, so each mode stays the way the operator shaped it.
- **[P5 Loss Aversion — OPPORTUNITY]** "Acknowledge N" (AlertsHeader.tsx) fires immediately with no confirm or undo. Mitigations exist — the count is in the button label and acknowledged rows remain visible with a Status column — but a mis-click on a group-level select-all can acknowledge a whole vehicle's alerts silently. Concrete change: success toast "Acknowledged 12 alerts" with an Undo action (re-open ack within ~10s), or a two-step armed button. `Impact: low · Effort: M`
- **[P6 Contrast Effect — APPLIED]** Group header rows summarize severity counts + latest-seen per vehicle, so vehicles are comparable at a glance and the worst one stands out.

### AI Chat History panel (threads, export, delete)
- **[P1 Smart Defaults — APPLIED]** Loads straight into the most recent thread (value before configuration); "New chat" creates the thread lazily on first send so abandoned empties never litter the list.
- **[P2 Goal Gradient — N/A]** No progression. **[P3 Reciprocity — N/A]** No ask at this surface.
- **[P4 IKEA Effect — APPLIED]** Threads are the user's accumulated work, titled and timestamped; per-chat export lets them take their work with them (no data hostage).
- **[P5 Loss Aversion — OPPORTUNITY]** Delete has a proper two-step in-row confirm (good), but the panel presents threads as a durable archive while the backend age-caps `ai_chat_history` at 90 days plus a per-user row cap ([capabilities/ai/retention.py](../../capabilities/ai/retention.py)) — threads will silently vanish with no disclosure. That's an honest-expiry gap that will read as data loss/bug. Concrete change: a quiet footer line in the History panel — "Chats are kept for 90 days" (i18n key `chat.retention_note`) — and optionally a Download hint on threads nearing expiry. `Impact: high · Effort: S`
- **[P6 Contrast Effect — N/A]** No choice framing. (Ethics note: replacing raw model IDs with tier labels "Fast/Thinking/Reasoning" in bubbles is honest simplification — the labels map to real tiers the user picked — not a dark pattern.)

### AI Chat empty state + persona briefing
- **[P1 Smart Defaults — APPLIED]** Role-aware suggested-question chips and a one-click briefing button mean first-run is never a blank prompt box.
- **[P3 Reciprocity — APPLIED]** Briefing now serves *every* persona with role-derived content (recruiter gets hiring pipeline, accounting gets costs) — real value delivered in one click before the user has to compose anything.
- **[P2/P4/P5/P6 — N/A]** No staged flow, authorship, destructible state, or choice set in the empty state.

### Billing usage & invoice tables
- **[P1 Smart Defaults — APPLIED]** Correct chrome-off defaults (`enableToolbar/enablePagination={false}`) for short display lists — no fake power-tools on a 6-row summary.
- **[P2 Goal Gradient — N/A]** Display-only. **[P3 Reciprocity — N/A]** No ask. **[P4 IKEA — N/A]** Deliberately non-configurable display surface.
- **[P5 Loss Aversion — APPLIED]** Pre-migration months fall back to `vehicle_count` instead of "—", so old history never *looks* lost.
- **[P6 Contrast Effect — APPLIED]** "Active 42 (+8 idle)" anchors the billable number against the free idle count — an honest contrast that shows the customer what they're *not* paying for; extra vehicles get warn-tone emphasis. Would pass the explain-to-the-customer's-face test.

## Top 3 actions (highest impact first)
1. **Disclose the 90-day chat retention in the History panel** — one footer line (`chat.retention_note`); prevents "my chats disappeared" trust damage. `Impact: high · Effort: S`
2. **Filtered-to-zero empty state** — "No rows match your filters" + inline Clear-filters button in DataTable's empty body; fixes both the misleading default and the perceived data loss. `Impact: med · Effort: S`
3. **Guard "Reset to defaults"** — two-step armed confirm (or undo-toast snapshot restore) before wiping a server-synced, all-devices column layout. `Impact: med · Effort: S`

## NEEDS-CONTEXT items
- None — the bulk-ack action bar (AlertsHeader.tsx) and retention policy (capabilities/ai/retention.py) were both readable in-repo, so no principle was left unjudged.
