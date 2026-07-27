# components/datagrid — DataGrid rules

Full rules for the DataGrid family. The main dashboard file
[interfaces/dashboard/CLAUDE.md](../../../CLAUDE.md) carries the short
consumer pointer; this file holds the detail so the main file stays lean.
**DataGrid is the SSOT for every tabular list of records in the dashboard**
— card / header tint / zebra / sort / pin / filter / pagination / theme,
plus the opt-in capabilities below (each a prop — never hand-rolled).

## Tables = DataGrid, always

Any tabular list of rows — even a 5-row read-only summary — uses
[`DataGrid`](DataGrid.tsx). **Never** roll a raw `<table>` for a data list.
For minimal display tables (no chrome needed) pass `enableToolbar={false}
enablePagination={false}` and DataGrid renders just the header + rows inside
a bordered card. For bulk-select checkboxes use the `bulkSelection` prop
(see Bulk below); `firstColumnLeading={{ header, cell }}` is for OTHER
leading content (expand toggle, row-number, …) that follows whichever column
is currently leftmost. The narrow exceptions where raw `<table>` is still
correct: (a) permission / config **matrices** (form UI, not a list), (b)
**form-embedded** line-item editors (Work-order parts), (c) **headerless
layout tables** used as form scaffolding (Forum routing rows). If it's a
list of records the operator would want to sort or filter, it's DataGrid.

## Bulk selection + actions = props, never hand-rolled

DataGrid owns row selection (the checkbox column — header select-all with
indeterminate, per-row, group select-all) and the **bulk-action bar** — a
TOP strip between the toolbar and the table (icon-only buttons with `<Tip>`
tooltips, `tone: 'danger'` paints the icon red), shown when 1+ rows are
selected. Turn it on with `bulkSelection` and pass `bulkActions={[{ label,
icon?, tone?, confirm?, options?, onRun(selectedRows, value?) }]}` — each
`onRun` receives the selected ORIGINAL rows (never tanstack ids) and
DataGrid clears the selection when it resolves. `options` makes the button a
dropdown (e.g. "Change status ▾"), passing the chosen value to `onRun`.
`bulkRowLabel` gives per-row a11y; `isRowSelectable(row)` gates which rows
get a checkbox (e.g. only ackable alerts); `onBulkSelectionChange` mirrors
the set out (e.g. AI page-context). The checkbox lives in its OWN dedicated
column (a synthetic locked, force-pinned-left, 44px column — id
`__select__`), NOT riding inside the first data cell — so the select box
never crowds the Vehicle/Name value. Do NOT re-implement a `selectedIds`
Set, checkbox `<input>`s via `firstColumnLeading`, or a `fixed bottom-4` bar
on a page — that's the old copy-pasted pattern this replaced.
`firstColumnLeading` remains ONLY for genuinely non-selection leading
content (expand toggle, row-number); when `bulkSelection` is also on it
attaches to the first DATA column, one slot right of the select column.
Checkbox selection and Ctrl/Cmd-click Copy share one set + one bar.
Controlled selection: pass `selectedIds` + `onSelectedIdsChange` when a page
must OWN the set (e.g. Alerts' shared context); omit both for the default
DataGrid-owned selection (Alerts Results is the controlled example — its
selection lives in a shared `AlertsSelectionContext`).

Active view state auto-renders as removable chips inline on the toolbar
line, after the bulk bar / headerToolbar (nothing to wire per page — driven
by the grid's own state): filter · sort · search · row-grouping ("Grouped by
X"). Deliberately NO chip for: hidden columns (hiding a column is a
deliberate layout act, not an active view constraint — surfacing it in
either a chip OR a count badge reads as an unresolved "notification" to
clear), pin (visually self-evident), and the column-bracket group
(column-config, not a per-session toggle). There are NO toolbar Filter/Sort
buttons — they were pure redundancy (their popovers only viewed/cleared
active state, which the chips now do, and never ADDED a filter). Adding a
filter/sort is the column ⋮ 3-dot menu; the chips are the active-state
display; the Manage-columns button is a plain icon (no badge).

## A grid can never be hidden down to zero columns

The last visible hideable column locks: its "Hide column" item (per-column
3-dot menu) disables with a "last column" hint, and its checkbox in the
Manage-columns popover disables like a required column. An empty grid paints
blank AND takes its own 3-dot menu with it, so the operator would have no
way back — the floor is enforced on BOTH hide paths, not just the one you're
touching.

## Aggregation = footer totals, opt-in per column

A column earns a footer total (sum / avg / min / max / count, picked by the
operator from the ⋮ menu → **Aggregate**) by declaring `aggregable: true` on
its Column config — explicit by design, because the grid has no column type
system to infer "this is a number" from. Pair it with `aggValue: (row) =>
number` when the cell renders something formatted (`"$2,847"`) but the true
value lives elsewhere on the row, and `aggFormat: (value, fn) => node` to
format the total (currency, units) — **switch on `fn`** so `count` doesn't
render as `$`. For a date/timestamp column set `aggType: 'date'` — the menu
then offers only Min (earliest) / Max (latest), `aggValue` may return a
Date/ISO-string/ms-number, and the result formats as a day (a bare
`YYYY-MM-DD` is treated as a tz-neutral calendar day; a full timestamp's day
is shown in the account tz). Date columns deliberately do NOT offer Count —
it's the whole-view row count, not "how many rows have a date", so on a
nullable column (`reviewed_at`) it misleads; put Count on a count column or
read the pagination total. Narrow further with `aggFns` (default: number →
all five, date → min/max). A missing numeric (null/undefined/'') is EXCLUDED
from sum/avg/min/max, never folded in as 0. The chosen model persists
per-user (`table.<id>.aggregation`, so it needs `tableId`);
`defaultAggregation={{ key: fn }}` starts it on. The total reduces over the
**filtered** set (all pages), and the function name shows as a muted
micro-label under the header (MUI's "Gross / sum"). Do NOT hand-roll a
totals row under a grid — this is the SSOT. When row grouping is ALSO
active, each group row shows that group's totals aligned under their columns
(the group identity stays pinned at the left edge during horizontal scroll),
while the footer keeps the grand total — matching MUI. This per-group render
is skipped when the page supplies a custom `rowGroupHeader` or
`firstColumnLeading.groupHeader` (those own the group row) and keeps the
classic full-width label. (Phases 1+2 shipped: footer + per-group. Custom
functions = Phase 3.) Pure engine + tests in
[`datagrid/aggregation.ts`](aggregation.ts).

## Column `filterable` = by cardinality, not by reflex

Set `filterable: true` on a column when the dropdown will actually help an
operator narrow the list. Two supported filter modes on the Column config:

- `filterMode: 'select'` (default) — the multi-select checkbox popover.
  Right when there are **≤ ~30 distinct values** that fit an enum (Type /
  Company / Status / Priority / Role). Provide `filterValue: (row) => code`
  + `filterLabel: (row) => nice` when the raw match-value differs from the
  display (e.g. `"oil"` → `"Oil Change"`).
- `filterMode: 'range'` — Min / Max number-input pair. Right for
  **continuous numeric** columns (percentages, mileage, hours, counts).
  Configure `filterRange: { min?, max?, step?, unit? }` — omit `min`/`max`
  to auto-compute from live data. Provide `step` to match display precision
  (`1` for percentages, `1000` for miles).
- `filterMode: 'date-range'` — From / To native `<input type="date">` pair.
  Right for **date / timestamp** columns (Submitted / Due date / Updated /
  Created). Filter value shape `[isoFrom|null, isoTo|null]` (YYYY-MM-DD); the
  "To" bound is inclusive-to-end-of-day so a single-day filter keeps the
  whole day. Bounds auto-compute from live data (earliest / latest).
- **Skip filterable entirely** for **free-text uniques** (Vehicle name,
  description) — the dropdown becomes a scroll-forever list of every value,
  slower than typing into the global search box. Extend `searchKey={[…]}`
  instead so the search field matches those columns. For **address**-style
  columns where the raw value is unique but the city / state groups many
  rows, opt into `filterMode: 'select'` with a `filterValue: (row) =>
  extractCityState(row.address)` accessor — the same pattern that turns
  Vehicle addresses into a tractable "Battle Creek, MI (3)" list.

## Lifecycle tabs = `segments`; live counts = feature hero

When a dataset has ONE dominant lifecycle dimension (Active/Archive,
pipeline stages), pass `segments` to DataGrid — folder-style tabs above the
toolbar with live counts; every page load starts on the FIRST tab (the
working set — selection is session-only by design, never persisted) —
instead of hand-rolling a chip row on the page. Fine-grained stage slicing
stays in column filters (e.g. a derived-status `filterValue`), and
feature-level live counts go in the TOPBAR hero: export a `<Feature>Hero`
from the feature folder and register it in
[`shells/heroes/featureHeroes.tsx`](../../shells/heroes/featureHeroes.tsx). A
hero MUST read the same react-query hook + classifier module the page uses
(shared cache — never a second fetch or a re-implemented computation), so
its numbers can't drift from the page's. Worked examples:
`features/maintenance` (useMaintenanceTasks + MaintenanceHero) and
`features/applications` (useApplications + ApplicationsHero).

## User-managed tabs = the `savedTabs` prop (personal scope tabs)

Opt a `tableId` grid in with `savedTabs` and a "+ New tab" affordance lets
an operator save the current filters + search as a named, per-user tab that
applies as an ISOLATED SCOPE (not a removable filter). The engine + dialog
+ full rules live in the sub-feature: [tabs/CLAUDE.md](tabs/CLAUDE.md)
([`tabs/`](tabs/) — `savedTabs.ts`, `SavedTabDialog.tsx`). Don't hand-roll
saved-filter tabs on a page.

## Bigger than one fetch? Hand the grid's view-state to the page

DataGrid only ever sees the rows it was given. On a page whose data
exceeds one fetch (the server caps a page at N rows), **every client-side
narrowing silently lies**: it filters the loaded N, then reports that as
the answer for the whole set. A grid holding 2,000 of 3,938 alerts,
filtered to `critical`, shows a confident number missing ~1,900 rows.
Segment tabs make it worse — they print a count *badge*, so the wrong
number lands in the most authoritative-looking spot on the page.

The fix is not to move filtering back onto the page (that splits the UI
into a chip bar plus a grid, two surfaces for one job). It's to keep the
filter UI here and let the PAGE own the state, so it can put the filters
into its server query. Opt in per grid — every prop below is optional and
omitting them leaves today's behaviour exactly as it was:

- `columnFilters` + `onColumnFiltersChange` — controlled filter state.
  The grid renders what it's handed and reports intent outward (column
  menus, removable chips, "clear all" all route through it).
- `manualFiltering` — "these rows ARRIVED filtered; don't filter them
  again." **Separate from being controlled on purpose**: a page may
  control the filters only to mirror them into the URL while the grid
  still does the work. Without this flag a server-filtered grid
  double-filters and drops rows. Note it neutralises the per-column
  `filterFn`, NOT the filter state — the state stays real so the column
  menus, the header tint and the ⋮ badge keep showing what's active. (It
  also deliberately does not use tanstack's own `manualFiltering` option:
  that short-circuits the entire filtered row model, and GLOBAL SEARCH
  lives in there too, so the search box would silently stop working.)
- `segmentKey` + `onSegmentChange` — controlled lifecycle tab. Give the
  segments no `match` fn when the server does the slicing. For a SAVED
  tab, `onSegmentChange`'s second argument carries that tab's captured
  `{ filters, search }`, because the key alone is an opaque id — a
  server-filtered page needs the criteria to put them in its query.
  Saved tabs also stop matching locally under `manualFiltering`, so they
  can't re-narrow an already-narrowed page. A controlled grid does not
  auto-apply the operator's stored default tab either: the page's
  `segmentKey` wins, since a controlled prop the child overrules isn't
  controlled.
- `segmentCounts` — authoritative per-segment counts. **Required** for
  server-driven segments: without it the badge tallies loaded rows.
  Keys you leave out fall back to the local tally.
- Column `filterOptions` — declare a `filterMode: 'select'` column's
  options instead of deriving them from loaded rows. Also required on a
  server-filtered grid: derivation reads the rows in hand, so choosing
  "Fault" unloads every other type and the menu collapses to the one
  value you already picked, with no way back.

Search stays local by design (it's scoped to the loaded page) — say so
above the table rather than implying it searched everything.

## Right-click row actions = the `rowActions` prop

`rowActions={(row) => MenuAction[]}` wraps each data row in a right-click
menu; return `[]` for no menu. It's built on the shared context-menu
primitive — full menu rules, the `MenuAction` shape, and the
`features/<x>/contextMenu.tsx` builder convention live in
[components/ui/CLAUDE.md](../ui/CLAUDE.md).
