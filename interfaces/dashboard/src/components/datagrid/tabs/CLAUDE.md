# components/datagrid/tabs — personal saved tabs

The user-managed **saved tabs** sub-feature of DataGrid: `savedTabs.ts`
(pure scope/match engine + tests) and `SavedTabDialog.tsx` (build-a-tab
dialog). The wiring that turns these into tab segments lives in the parent
[`../DataGrid.tsx`](../DataGrid.tsx). The parent
[datagrid/CLAUDE.md](../CLAUDE.md) carries the one-line pointer; full rules
here.

Two kinds of tab share the DataGrid tab strip — don't confuse them:
- **Built-in `segments`** — code-defined, account-wide (Active/Archive).
  Rule + `<Feature>Hero` counts: [datagrid/CLAUDE.md](../CLAUDE.md)
  §"Lifecycle tabs".
- **Saved tabs** (THIS folder) — user-defined, per-user, built from the
  grid's own filter state. Told apart at runtime by the `TAB_PREFIX` key
  prefix / `isTab` check in `../DataGrid.tsx`.

## The `savedTabs` prop

Opt a `tableId` grid in with `savedTabs`, and a "+ New tab" affordance lets
an operator save the CURRENT filters + search as a named tab. **Key design:
a saved tab applies as an ISOLATED SCOPE, not a removable filter** — it
becomes a `DataGridSegment` whose `match` is the captured filters, so it
flows through the exact `sourceData.filter(match)` scoping as Active/Archive
(no cross-tab leak; sort / export / select-all stay inside). The matching
reuses `rowPassesColFilter` (in [`savedTabs.ts`](savedTabs.ts), pure +
tested) so a tab scopes identically to its live filters.

- **Persistence**: per-user, key `table.<id>.views` — the ORIGINAL stored
  key name, deliberately kept through the view→tab rename. Don't "fix" it
  to `.tabs` or you orphan every user's saved tabs.
- **Ordering**: saved tabs sit AFTER built-in `segments` (an implicit "All"
  leads when a grid has none); a tab saved while on a built-in segment
  COMPOSES with it (a "Critical" tab made on Active shows active criticals,
  not archived ones).
- **Sort**: a tab carries the sort you had when you saved it (applied on
  select; re-sort freely after). NOT captured per-tab: row-grouping +
  column layout — those stay the grid's GLOBAL per-user prefs (a per-tab
  version would stomp them; deferred).
- **Management is by RIGHT-CLICK** on the tab (Edit / set-as-default /
  reorder / **Duplicate** / delete — no ⋮ button, by owner decision:
  right-click is the correct action, a per-tab ⋮ is clutter). The menu uses
  the shared context-menu primitive ([../../ui/CLAUDE.md](../../ui/CLAUDE.md)),
  which is input-agnostic: **mouse** right-click, **touch** long-press, and
  **keyboard** Menu-key / Shift+F10 (the tab is a focusable `<button>`) all
  open it. Discoverability rests on convention + the "Right-click to
  manage" hover hint (also shown on keyboard focus via `aria-keyshortcuts`)
  + a one-time coach toast on the first personal tab.
- **Customization** (personal recognition — NO effect on scope): a tab may
  carry a `tone` (colours the COUNT BADGE only, via `toneClasses` — not the
  whole tab) and an `icon` (a leading lucide key from
  [`tabIcons.ts`](tabIcons.ts), rendered before the label in place of the
  dot). Both are optional and set in the New/Edit dialog. Color palette is
  the semantic tones (`ok/warn/danger/info/neutral`) — never freeform hex.
  Icons are a curated lucide set — never emoji (dashboard icon rule).
  Accepted tradeoff (UX audit): the toned badge reuses the app's STATUS
  palette, so a red count could read as "problems" rather than "my colour" —
  deemed OK because a saved tab is PERSONAL + self-set. Safety nets:
  deleting a tab shows an **Undo** toast (a tab is effortful to rebuild),
  and creating the first personal tab shows a one-time coach toast teaching
  the right-click menu.
- Account-shared tabs were dropped by the owner (personal-only, matching
  the per-user isolation everywhere else).

Don't hand-roll saved-filter tabs on a page — this folder is the SSOT.
