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
TOP strip between the toolbar and the table (icon + LABEL buttons with
`<Tip>` tooltips, `tone: 'danger'` paints the icon red), shown when 1+ rows
are selected. Labels are shown, not hidden behind hover: the bar exists
because rows are selected, so its buttons are the reason the operator
selected them — wordless glyphs made the primary action reachable only by
hovering, which a touch user can't do. Turn it on with `bulkSelection` and pass `bulkActions={[{ label,
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

## Global search covers EVERY column — `searchKey` is for non-columns

The search box matches the needle against **every column**, whether or not
the page named it, and whether or not it is currently visible. Columns are
read through **`cellText`** ([`lib/cellText.ts`](../../lib/cellText.ts)) —
the same accessor CSV export uses (`csvValue` → `filterLabel` →
`filterValue` → raw) — so a badge column is searched by the word it
**displays** ("Critical"), not the code behind it, and a column with no
accessor needs no work at all.

`searchKey` is still required (it's what makes the box appear) but its job
is now narrow: **row fields that are NOT columns** — a carrier's
`experience_summary`, an applicant's `reference`. Listing a key that is
already a column is harmless but redundant.

This was a real defect, not a polish item: pages declared 2–7 keys while
rendering 20+ columns, so a box captioned "Search carriers…" answered for a
fraction of the table. Carrier Directory could render `60` in a Solo Pay
Rate cell and print "No match for 60" in the same frame.

Two deliberate calls:

- **Hidden columns are searched.** On a 74-field directory, "which carrier
  mentioned hazmat?" must not require knowing which column holds it first.
  The cost is paid by the search note below, not by the operator.
- **Object-valued cells never match.** They stringify to `[object Object]`,
  so the needle "object" would return every row. Give such a column a
  `csvValue`/`filterValue` accessor to make it searchable. Arrays of
  primitives match element-wise and need nothing.

One implementation, pure and tested: `rowMatchesSearch` in
[`search.ts`](search.ts) — shared by the live filter, a saved tab's
captured search, and the filter dropdowns' option counts, so those three
can't disagree about what a search means. (It is re-exported from
[`tabs/savedTabs.ts`](tabs/savedTabs.ts), its original home, so that
import keeps working.)

### A hit you can't see explains itself — the search note

Searching hidden columns has a failure mode, and it looked like a bug in
the search: type `60` into Carrier Directory and get back one row whose
every visible cell reads `—`. Nothing on screen contains what you typed,
and the only recovery was to guess which of 76 columns to reveal.

So the grid now says which one. A quiet band between the toolbar and the
rows names the hidden column(s) that account for the rows nothing visible
accounts for, and offers to reveal them:

> 👁 1 row matches "60" only in a hidden column: **Solo Pay Rate** · Show column

Nothing to wire — it is driven by the grid's own state. What governs it:

- **It only speaks when the operator is actually stuck.** The trigger is a
  row that NO VISIBLE column explains, not merely a hit that also exists
  in a hidden column. A note on every search is a nag, and a nag is
  ignored on the one search where it mattered.
- **Scoped to the rows on THIS page**, so the cost is bounded by the page
  size instead of the dataset, and it explains the rows in front of the
  operator rather than making a claim about rows they can't see. It says
  "on this page" only when there IS another page.
- **It never offers a button that would do nothing.** Rows that matched a
  `searchKey` ROW FIELD (a carrier's `experience_summary`) have no column
  to reveal, so those get a plain sentence and no action — plus "Open the
  row to see it" when the page passes `onRowClick`, because that IS the
  recovery there. The count beside "Show column" is the rows that button
  will actually explain.
- **Revealing SCROLLS the column into view.** Setting visibility is only
  half the action: on a 76-column grid the column appears at its ordinal
  position among the visible ones, which can be far off the right edge —
  so the click would complete with nothing visibly different, which reads
  as a dead button. Done in an effect keyed on `effectiveVisibility`, not
  a rAF after the click: the write goes through a persisted preference,
  so the `<th>` does not exist yet when the handler returns.
- **Revealing is undoable.** The column layout is the operator's own work
  and the write is persisted per-user ACROSS DEVICES, so a one-click link
  must not permanently edit a curated view — an Undo toast restores the
  visibility map, the same shape as deleting a saved tab.
- **Silent under `manualFiltering`** — there the SERVER matched, so the
  grid doesn't know what it matched on and would be naming a column by
  guesswork.
- **This is not the hidden-columns chip that was deliberately rejected.**
  That one would have announced hidden columns as active view state on
  every visit, reading as an unresolved notification to clear. This
  speaks only when a SEARCH RESULT is unexplainable from what's on
  screen — a specific, momentary, actionable problem the operator is
  looking at right now. Hiding a column stays a silent layout act.
- **The live region stays mounted while empty.** A region that appears at
  the same moment as its text is frequently not announced at all, which
  would hand a screen-reader user the unexplained rows and none of the
  explanation.

The analysis is `searchProvenance` in [`search.ts`](search.ts), deliberately
in the same file as `rowMatchesSearch`: both read a cell through `cellText`,
and if they ever disagreed about what a cell says the grid would show rows
it can't account for and account for rows it doesn't show.

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
  slower than typing into the global search box — which already matches
  every column (see below), so there is nothing to wire. For **address**-style
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
- `sorting` + `onSortingChange` + `manualSorting` — controlled sort. With
  `manualSorting` the rows arrived ordered, so the grid reports the click
  and renders what it's handed. This is what lets a 25-row page be
  correctly sorted across 11,200 rows.
- `pageIndex` + `pageSize` + `onPaginationChange` + `pageCount` +
  `manualPagination` — the page fetches ONE page. Pass `totalRows` too, or
  the footer counts the rows in hand and reads "1–25 of 25" forever.
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

### Holding a slice? Say so — `totalRows`

Filtering server-side fixes the FILTERS. It doesn't fix everything else
that assumes the grid holds the whole result. Pass `totalRows` (the true
count behind the grid) whenever the page hands it a capped page, and the
grid stops answering for the whole from a part:

| Operation | Silently wrong on a slice | With `totalRows` |
|---|---|---|
| Sort | orders the loaded rows, reads as sorted | disabled — *unless* `manualSorting`, where the order came from upstream and a slice is the right slice |
| Group rows by | groups a fragment | disabled (ungroup always allowed) |
| Export → all | writes a `-all` file of the loaded rows | "All loaded rows", both counts, `-loaded` suffix |
| Pivot | summarises a fragment as a total | disabled with the reason |

Pivot is the worst of the four: a cross-tab shows no rows, so there's
nothing to count and notice the shortfall by.

Disabled **with the reason**, never hidden — a control that vanishes
teaches nothing, and "narrow the view first" is something an operator can
act on. Omit `totalRows` on any grid that holds its whole dataset and
nothing changes.

Search stays local by design (it's scoped to the loaded page) — say so
above the table rather than implying it searched everything.

## Tall grids own a viewport — the `fillHeight` prop

By default a grid grows to fit its rows, so "rows per page: 250" makes
the card 250 rows TALL and the PAGE does the scrolling. That pushes four
things out of reach at once, and they get worse the more rows you show:

| | Default (page scrolls) | `fillHeight` (body scrolls) |
|---|---|---|
| Column headers | scroll away — unlabelled columns by row 40 | sticky at the top of the body |
| Horizontal scrollbar | rides the bottom of the table, thousands of px down | pinned to the card's bottom edge |
| Bulk-action bar | a TOP strip, above the rows it acts on | always visible |
| Pagination / rows-per-page | past the last row | always visible |

`fillHeight` gives the grid its own viewport: toolbar and footer pin to
the card's edges, the body scrolls between them. **No measurement and no
magic height** — the shell is already `h-screen overflow-hidden` with ONE
scroll region ([`shells/`](../../shells/)), so this is pure flexbox.

**The page must cooperate**: its root needs `h-full flex flex-col min-h-0`
so there's a definite height to divide up, and the grid must be a direct
flex child of it. On a page not laid out that way the grid keeps its
natural height — nothing breaks, the prop just does nothing. Worked
example: [`features/loads/Loads.tsx`](../../features/loads/Loads.tsx).

Moving the scrolling off the document takes three things with it, all
restored here and pinned by `DataGrid.fillHeight.test.tsx`:

- **Scroll position resets when the list changes identity** — page,
  **page size**, sort, filter, search, tab, **grouping**. Position only
  means something relative to the list you were reading; keeping it means
  "next page" drops you into the middle of page 2, and a sticky header
  leaves no visual cue that it happened. Applied in BOTH modes so
  behaviour can't diverge.
  ⚠️ The reset must key on the list's **identity, not on object
  identity**. Pivot's copy keyed on the `model` OBJECT, which DataGrid
  rebuilds whenever `columns` gets a new array — and a page that builds
  its column config inline hands over a fresh array on every parent
  render, so the report jumped to the top on any unrelated upstream state
  change. It keys on a serialised `listKey` of the fields that change
  WHICH ROWS EXIST; the pins and the drill toggle are deliberately
  excluded, because freezing a column is not a new list.
- **The scrollport is padded away from the sticky chrome.**
  `scrollPaddingTop` / `Left` / `Right` are set from the measured header
  height and pinned widths. Without them the browser's own
  scroll-into-view puts a tabbed-to control at the container's literal
  edge — i.e. behind the sticky header or the frozen columns — so the
  focused element is "in view" and invisible (WCAG 2.4.11).
- **The scroll container is focusable** (`tabIndex={0}` + `role="region"`
  + a name). A plain `overflow` div is not, so keyboard users lose the
  rows past the first screen entirely — the document used to scroll for
  them (WCAG 2.1.1).
- **The aggregation footer anchors to the bottom** when the body scrolls
  — sticky on the `<tfoot>` element, the same trick the header uses, so
  pinned cells' own z-indexes stay relative to it and nothing needs
  re-layering. A header micro-label reading "sum" that points at a number
  250 rows below is a promise the reader can't collect.

**The vertical scrollbar is ours, not the browser's.** The sticky header
lives inside the scroll container, so a native bar would run the
container's full height — up alongside the column labels and their ⋮
menus, which reads as the rows scrolling *into* the header. When the
body scrolls we hide the native bar and draw one starting below the
measured header height, the same treatment the horizontal bar already
had. Only the PAINTING is ours: `overflow-y` stays `auto`, so wheel,
touch, keyboard and scroll-into-view are untouched. (MUI gets this for
free because its headers are a separate element outside the scroller;
ours share one `<table>` so the columns can't drift out of alignment.)

**The vertical bar gets a lane of its own.** It is an absolute overlay,
so unlike a native bar it takes no layout space — and with nothing
reserved it is painted over whatever sits at the scrollport's right
edge, which on a table wide enough to need it is a column of real data.
Both renderers reserve `V_BAR_GUTTER` as `padding-right` on the
`group/grid` wrapper when (and only when) the bar actually draws. There
is deliberately **no fade** at the clipped right edge: one was tried and
removed, because it veils data even when you are scrolled to the end and
there is nothing more to point at. Full rule:
[components/scrolling/CLAUDE.md](../scrolling/CLAUDE.md).

**Scrollbars are shared, not per-renderer.** [`scrollbars.tsx`](scrollbars.tsx)
owns `useScrollMetrics` + `<ScrollbarH>` + `<ScrollbarV>` +
`HIDE_NATIVE_SCROLLBAR`, and both the record list and the pivot matrix
use them. Custom rather than native because with pinned columns a native
bar spans the WHOLE container, implying the frozen columns scroll too;
the vertical one starts below the sticky header instead of running up
beside the column ⋮ menus. Only the PAINTING is ours — `overflow-y`
stays `auto` so wheel/touch/keyboard/scroll-into-view are untouched;
`overflow-x` is `hidden` (a native x-bar reserves a track even at height
0) with a wheel handler restoring trackpad swipe.

⚠️ **Nothing outside a scrollbar may subscribe to scroll POSITION.**
`useScrollMetrics` sets state per frame, so a surface that calls it
re-renders its whole subtree while you scroll — on a pivot matrix that
is ~22,000 cells (360 rows × 61 columns) reconciled per frame, which
freezes the tab. The bars are two divs, and they subscribe themselves.
A surface that needs "is there overflow" for a layout class uses
**`useOverflow`**, which is ResizeObserver-driven and never watches
scrolling. Updates are rAF-coalesced on top of that.

⚠️ **The horizontal wheel bridge is called ONCE, by whoever owns the
container** — `useWheelToHorizontal(scrollEl)` in `DataGrid` and in
`PivotView`, never in a scrollbar. It used to live inside
`useScrollMetrics`, which **both** bars call on the **same** element, so
two handlers each applied the delta and **every trackpad swipe scrolled
twice as far as it should**. An unneeded bar's early `return null` does
not save you: hooks run before it, so even an invisible bar installed its
handler. `scrollbars.test.tsx` pins the single-handler contract and was
verified to go red against the old shape (60px instead of 0 with two bars
mounted). The bridge sets no state, so calling it from a parent cannot
re-render anything at scroll rate.

Wheel deltas are **normalised by `deltaMode`**: Chrome and Safari report
pixels, but **Firefox reports LINES** for a physical mouse wheel, so the
raw value moved the grid 3px per notch.

Thumb drags use **pointer capture**, not `window` listeners. Capture
routes later events for that pointer to the thumb, so a drag survives the
pointer leaving the window and the listeners die with the element — the
old version had no `pointercancel` branch and no unmount cleanup, so a
`pointerup` the window never saw left a live `pointermove` handler
scrolling the grid forever. The thumb is `touch-none` because on a
touchscreen it is currently the ONLY horizontal-scroll affordance.

The seam is deliberate: **track geometry is shared, insets are local.**
The two renderers freeze different things — the list reads `data-pin` off
its leaf header row, the matrix measures its corner cell and Total group
— so each measures its own and passes `insetLeft/insetRight/insetTop`.
Anything else and one renderer's DOM assumptions leak into the other.

**Measurement effects key on the ELEMENT, via callback refs.** Pivot
unmounts the table branch and mounts a brand-new one on the way back, so
any observer set up in an effect keyed on props/state alone ends up
watching a detached node. That happened to all three at once (scroll
metrics, header height, pinned widths): metrics froze, and with them the
custom scrollbars stopped rendering — a wide grid with no way to scroll
sideways, only after a pivot round-trip. `setScrollNode` / `setTheadNode`
publish the live node into state so the effects re-attach whenever the
element is replaced, for any reason. `DataGrid.fillHeight.test.tsx` pins
it (and needs a STATEFUL preferences mock — with a no-op `setValue`
pivot never actually toggles and the test is theatre).

Two gotchas the implementation encodes, both easy to reintroduce:

- **`min-h-0` at every level.** A flex item defaults to `min-height:
  auto` — "never smaller than my content" — which on a 250-row table
  means "never smaller than 250 rows", and the whole mechanism silently
  does nothing. The one exception is the body wrapper, which carries a
  `min-h-[16rem]` FLOOR instead: on a phone, or under a tall page
  header, the body stops shrinking and the page's scroll region takes
  over rather than leaving a slit.
- **Pivot mode needs its own scroller.** The card now clips
  (`overflow-hidden`) at a definite height and `PivotView` caps nothing
  vertically, so a tall matrix would be cut off with no way to reach the
  rest. The pivot branch gets `overflow-y-auto`; horizontal scrolling
  stays inside `PivotView` (its sticky row-label column depends on it).

`stickyHeader="65vh"` is the older hand-tuned form — a fixed max-height,
still right when a grid must be SHORTER than the space available to it
(Scorecards). Both routes set the same internal `bodyScrolls` flag, so
the sticky header and its z-index behave identically.

## Right-click row actions = the `rowActions` prop

`rowActions={(row) => MenuAction[]}` wraps each data row in a right-click
menu; return `[]` for no menu. It's built on the shared context-menu
primitive — full menu rules, the `MenuAction` shape, and the
`features/<x>/contextMenu.tsx` builder convention live in
[components/ui/CLAUDE.md](../ui/CLAUDE.md).

## Pivot = the `pivot` prop (cross-tab reporting)

Opt a `tableId` grid in with `pivot` and a toolbar toggle swaps the record
list for a matrix (rows × columns × aggregated values) plus a Fields
panel. Mark dimensions `pivotable: true`; measures are the existing
`aggregable: true` columns. A dimension can bucket via `pivotValue`
(that's how a date becomes a MONTH — in the ACCOUNT timezone).

Pivot is a REPORT, not a record list: it renders through its own
read-only view rather than the interactive grid, so column machinery
(manage-columns, pagination) hides while scope controls (tabs, filter
chips, search) stay live and narrow its input. CLIENT-COMPLETE data only.
Rules + the deferred list: [pivot/CLAUDE.md](pivot/CLAUDE.md).
